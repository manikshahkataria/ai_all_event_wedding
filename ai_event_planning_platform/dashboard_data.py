"""Saved wedding dashboard projections. All financial arithmetic uses assess_budget."""

from dataclasses import dataclass, replace

from ai_event_planning_platform.budget_engine import assess_budget, context_fingerprint
from ai_event_planning_platform.ceremony_plan import CeremonyPlan, VenuePreference
from ai_event_planning_platform.wedding_ceremonies import CEREMONY_BY_ID, Ceremony
from ai_event_planning_platform.wedding_calendar import saved_calendar_data
from ai_event_planning_platform.wedding_timeline import current_schedules, schedule_issues
from ai_event_planning_platform.wedding_followups import missing_followup_ids
from ai_event_planning_platform.wedding_profile import WeddingDateStatus, WeddingProfile


CATEGORY_LABELS = {
    "catering": "Food and catering", "venue_rental": "Venue",
    "decoration": "Decoration", "photography": "Photography and videography",
    "lighting_sound": "Lighting and sound", "entertainment": "Entertainment",
    "service_staff": "Seating and staffing", "seating": "Seating and staffing",
    "travel_accommodation": "Travel and accommodation (combined)",
    "ceremony_supplies": "Ceremony supplies", "custom": "Other / custom expenses",
}


def wedding_display_title(profile, project_name="Wedding"):
    def name(field):
        value = profile.get(field) if isinstance(profile, dict) else getattr(profile, field, None)
        return value.strip() if isinstance(value, str) else ""
    bride, groom = name("bride_name"), name("groom_name")
    return f"{bride} & {groom}'s Wedding" if bride and groom else project_name or "Wedding"


@dataclass(frozen=True)
class PlanningAlert:
    kind: str
    message: str
    route: str
    ceremony_id: str | None = None


@dataclass(frozen=True)
class DashboardNextStep:
    label: str
    route: str
    ceremony_id: str | None = None
    view: str | None = None


def recommended_next_step(profile, ceremonies, budget=None, timeline=None):
    """Recommend one saved-project action without treating optional answers as gates.

    Callers pass the verified saved profile and confirmed ceremony snapshot. An
    explicitly undecided wedding date is a supported answer; optional names,
    style, culture and traditions do not hold up the next planning suggestion.
    """
    essentials = {"city", "state", "location_preference", "guest_count", "budget_inr"}
    if not isinstance(profile, WeddingProfile):
        return DashboardNextStep("Complete Wedding Details", "step_by_step")
    missing = set(missing_followup_ids(profile))
    date_missing = (
        profile.wedding_date_status is not WeddingDateStatus.NOT_DECIDED_YET
        and profile.wedding_date is None
    )
    if missing & essentials or date_missing:
        return DashboardNextStep("Complete Wedding Details", "step_by_step")
    ceremonies = tuple(c for c in ceremonies if c.selected)
    if not ceremonies:
        return DashboardNextStep("Choose Ceremonies", "wedding_ceremonies")
    unplanned = next((c for c in ceremonies if c.plan is None), None)
    if unplanned is not None:
        return DashboardNextStep("Plan Ceremonies", "ceremony_planning", unplanned.ceremony_id)
    if budget is None or not budget.accepted:
        return DashboardNextStep("Review Smart Budget", "smart_wedding_budget")
    if timeline is None or not timeline.accepted:
        return DashboardNextStep("Create Wedding Timeline", "wedding_timeline", view="Preparation timeline")
    return DashboardNextStep("Review Wedding Dashboard", "wedding_dashboard")


def budget_dashboard_data(plan, profile, ceremonies):
    """Keep accepted rows/reservations intact even when the current ceremony list changes."""
    if plan is None or not plan.accepted:
        return None
    stale = plan.context_fingerprint != context_fingerprint(profile, ceremonies)
    current = {c.ceremony_id: c for c in ceremonies}
    scope = ceremonies
    if stale:
        scope = []
        for n in plan.needs:
            c = current.get(n.ceremony_id) or CEREMONY_BY_ID.get(n.ceremony_id)
            c = c or Ceremony(n.ceremony_id, "Previously selected ceremony (" + n.ceremony_id + ")", "", is_custom=True)
            # Accepted allocations come from the saved plan, not subsequently edited constraints.
            scope.append(replace(c, selected=True, plan=CeremonyPlan(c.ceremony_id, c.name,
                estimated_budget_inr=plan.ceremony_budgets.get(c.ceremony_id))))
    assessment = assess_budget(plan, profile, scope)
    names = {c.ceremony_id: c.name for c in scope}
    names.update({c.ceremony_id: c.name for c in ceremonies})
    records = tuple({"Expense ID": e.key, "Expense": e.display_name,
        "Original category": e.category, "Category": CATEGORY_LABELS.get(e.category, e.category),
        "Ceremony": names.get(e.ceremony_id, e.ceremony_id) if e.ceremony_id else "Shared expenses",
        "Ceremony ID": e.ceremony_id, "Estimated amount (INR)": e.allocated_inr,
        "Basis": e.basis} for e in plan.expenses)
    categories = {}
    for row in records:
        categories[row["Category"]] = categories.get(row["Category"], 0) + row["Estimated amount (INR)"]
    contributions = {}
    for row in records:
        key = (row["Category"], row["Ceremony ID"])
        contributions[key] = contributions.get(key, 0) + row["Estimated amount (INR)"]
    rows = []
    for cid in dict.fromkeys([*(c.ceremony_id for c in ceremonies), *assessment.ceremony_allocations]):
        if cid not in assessment.ceremony_allocations:
            rows.append({"Ceremony ID": cid, "Ceremony": names[cid], "Allocated (INR)": None,
                "Calculated expenses (INR)": None, "Remaining allocation (INR)": None, "Status": "Not in accepted budget"})
            continue
        remaining = assessment.ceremony_remaining[cid]
        rows.append({"Ceremony ID": cid, "Ceremony": names[cid], "Allocated (INR)": assessment.ceremony_allocations[cid],
            "Calculated expenses (INR)": assessment.ceremony_expenses[cid], "Remaining allocation (INR)": remaining,
            "Status": "Previously selected; review" if cid not in current else "Shortfall" if remaining < 0 else "Reserved allocation" if remaining else "Within allocation"})
    messages = (*assessment.issues, *assessment.warnings)
    if profile.budget_inr is None:
        messages = tuple(m for m in messages if not m.startswith(("Minimum requirements including", "Suggested allocations exceed the overall ceiling")))
    return {"assessment": assessment, "messages": messages, "stale": stale, "ceremonies": tuple(rows), "records": records,
            "categories": categories, "contributions": contributions, "names": names}


def planning_alerts(profile, ceremonies, budget=None, timeline=None, *, include_budget=True):
    alerts = []
    if profile.guest_count is None:
        alerts.append(PlanningAlert("Missing information", "Overall guest count is not provided.", "step_by_step"))
    for c in ceremonies:
        p = c.plan
        if not p or p.ceremony_date is None:
            alerts.append(PlanningAlert("Missing information", c.name + ": ceremony date is not decided.", "ceremony_planning", c.ceremony_id))
        if not p or p.guest_count is None:
            alerts.append(PlanningAlert("Missing information", c.name + ": guest count is not provided.", "ceremony_planning", c.ceremony_id))
        if not p or p.venue_preference is VenuePreference.UNDECIDED:
            alerts.append(PlanningAlert("Missing information", c.name + ": venue is undecided.", "ceremony_planning", c.ceremony_id))
    if not ceremonies:
        alerts.append(PlanningAlert("Missing information", "No ceremonies have been confirmed.", "wedding_ceremonies"))
    schedules, tasks = saved_calendar_data(profile, ceremonies, timeline, budget)
    for s in schedules:
        if s.start_time is None or s.end_time is None:
            alerts.append(PlanningAlert("Incomplete timing", s.ceremony_name + ": explicit start/end time is missing; timing remains flexible.", "wedding_timeline", s.ceremony_id))
    if timeline is None or not timeline.accepted:
        alerts.append(PlanningAlert("Missing information", "No timeline has been saved. Generate suggestions or add your own tasks and activities.", "wedding_timeline"))
    else:
        from ai_event_planning_platform.wedding_timeline import planning_fingerprint
        if timeline.context_fingerprint != planning_fingerprint(profile, ceremonies, budget):
            alerts.append(PlanningAlert("Needs review", "Saved timeline predates changes to wedding details. Refresh and review its draft.", "wedding_timeline"))
        ids = {c.ceremony_id for c in ceremonies}
        check = replace(timeline, schedules=current_schedules(ceremonies, timeline, budget, profile),
            tasks=tuple(replace(t, ceremony_id=None) if t.ceremony_id not in ids else t for t in timeline.tasks))
        for issue in schedule_issues(check):
            if issue.severity != "incomplete":
                kind = "Scheduling conflict" if issue.code == "ceremony_overlap" and "(fixed dates)" in issue.message else "Validation error" if issue.severity == "error" else "Scheduling advisory"
                alerts.append(PlanningAlert(kind, issue.message + " " + issue.resolution, "wedding_timeline"))
    pending = sum(t.status != "completed" for t in tasks)
    if pending:
        alerts.append(PlanningAlert("Pending work", f"{pending} preparation task(s) are unfinished.", "wedding_timeline"))
    if include_budget:
        if budget is None or not budget.accepted:
            alerts.append(PlanningAlert("Missing information", "No accepted budget plan has been saved.", "smart_wedding_budget"))
        else:
            data = budget_dashboard_data(budget, profile, ceremonies)
            for message in data["messages"]:
                alerts.append(PlanningAlert("Budget review", message, "budget_dashboard"))
            if profile.budget_inr is not None and data["assessment"].maximum_inr > profile.budget_inr:
                alerts.append(PlanningAlert("Budget review", "The upper budget estimate exceeds the saved overall ceiling.", "budget_dashboard"))
    return tuple(alerts)
