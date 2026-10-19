from dataclasses import asdict, replace
from datetime import date
import hashlib
import json

import pytest

from ai_event_planning_platform.budget_engine import add_expense, context_fingerprint, edit_allocations
from ai_event_planning_platform.ceremony_plan import VenuePreference
from ai_event_planning_platform.dashboard_data import (
    DashboardNextStep, budget_dashboard_data, planning_alerts, recommended_next_step,
    wedding_display_title,
)
from ai_event_planning_platform.wedding_profile import WeddingDateStatus, WeddingLocationPreference, WeddingProfile
from ai_event_planning_platform.wedding_timeline import fallback_timeline, planning_fingerprint
from test_budget_engine import ceremony, profile, reviewed_plan
from test_wedding_timeline import dated_ceremony


def essential_profile(**changes):
    return replace(profile(city="Pune", state="Maharashtra",
        location_preference=WeddingLocationPreference.LOCAL, guest_count=80), **changes)


@pytest.mark.parametrize("field", ["city", "state", "location_preference", "guest_count", "budget_inr"])
def test_next_step_prioritizes_missing_essential_details(field):
    p = essential_profile(**{field: None})
    assert recommended_next_step(p, []) == DashboardNextStep("Complete Wedding Details", "step_by_step")


def test_next_step_handles_minimal_profile_and_missing_profile():
    for p in (WeddingProfile(), None):
        assert recommended_next_step(p, []).route == "step_by_step"


@pytest.mark.parametrize("status,value", [
    (WeddingDateStatus.NOT_DECIDED_YET, None),
    (WeddingDateStatus.APPROXIMATE_DATE, "2027-11"),
    (WeddingDateStatus.FIXED_DATE, date(2027, 11, 12)),
])
def test_next_step_accepts_declared_date_status_and_optional_answers(status, value):
    p = essential_profile(wedding_date_status=status, wedding_date=value)
    assert p.bride_name is p.groom_name is p.style is p.partner_1_culture is None
    assert recommended_next_step(p, []) == DashboardNextStep("Choose Ceremonies", "wedding_ceremonies")


@pytest.mark.parametrize("status", [WeddingDateStatus.FIXED_DATE, WeddingDateStatus.APPROXIMATE_DATE])
def test_next_step_catches_date_missing_for_selected_status(status):
    p = essential_profile(wedding_date_status=status)
    assert recommended_next_step(p, []).route == "step_by_step"


def test_next_step_plans_first_missing_confirmed_ceremony_before_budget():
    p = essential_profile()
    planned = ceremony()
    unplanned = replace(ceremony("reception"), plan=None)
    later = replace(ceremony("mehendi"), plan=None)
    assert recommended_next_step(p, [planned, unplanned, later]) == DashboardNextStep(
        "Plan Ceremonies", "ceremony_planning", "reception")


def test_next_step_ignores_unconfirmed_ceremonies():
    p = essential_profile()
    suggested = replace(ceremony(), selected=False, plan=None)
    assert recommended_next_step(p, [suggested]).route == "wedding_ceremonies"
    assert recommended_next_step(p, [suggested, ceremony("reception")]).route == "smart_wedding_budget"


def test_next_step_only_accepted_budget_advances_to_timeline():
    p, cs = essential_profile(), [ceremony()]
    draft_budget = reviewed_plan(p, cs)
    for budget in (None, draft_budget):
        assert recommended_next_step(p, cs, budget).route == "smart_wedding_budget"
    accepted_budget = replace(draft_budget, accepted=True)
    assert recommended_next_step(p, cs, accepted_budget) == DashboardNextStep(
        "Create Wedding Timeline", "wedding_timeline", view="Preparation timeline")


def test_next_step_only_accepted_timeline_completes_the_saved_plan():
    p, cs = essential_profile(), [ceremony()]
    budget = replace(reviewed_plan(p, cs), accepted=True)
    draft_timeline = fallback_timeline(p, cs, budget)
    assert not draft_timeline.accepted
    assert recommended_next_step(p, cs, budget, draft_timeline).route == "wedding_timeline"
    timeline = replace(draft_timeline, accepted=True)
    assert recommended_next_step(p, cs, budget, timeline) == DashboardNextStep(
        "Review Wedding Dashboard", "wedding_dashboard")


def test_next_step_keeps_projects_independent_and_never_mutates_sources():
    p, cs = essential_profile(), [ceremony()]
    budget = replace(reviewed_plan(p, cs), accepted=True)
    timeline = replace(fallback_timeline(p, cs, budget), accepted=True)
    before = (asdict(p), [asdict(c) for c in cs], asdict(budget), asdict(timeline))
    assert recommended_next_step(p, cs, budget, timeline).route == "wedding_dashboard"
    assert recommended_next_step(WeddingProfile(), []).route == "step_by_step"
    assert recommended_next_step(p, []).route == "wedding_ceremonies"
    assert recommended_next_step(p, cs, budget, timeline).route == "wedding_dashboard"
    assert before == (asdict(p), [asdict(c) for c in cs], asdict(budget), asdict(timeline))


@pytest.mark.parametrize("bride,groom,title", [(None, None, "Our project"), ("Priya", None, "Our project"),
    (None, "Rahul", "Our project"), (" Priya ", "Rahul", "Priya & Rahul's Wedding")])
def test_personalized_titles_require_both_optional_names(bride, groom, title):
    p = profile(bride_name=bride, groom_name=groom)
    assert wedding_display_title(p, "Our project") == title
    assert wedding_display_title(asdict(p), "Our project") == title


def test_names_preserve_legacy_planning_fingerprints():
    p, cs = profile(), [ceremony()]
    raw_profile = {k: v for k, v in asdict(p).items() if k not in {"bride_name", "groom_name"}}
    legacy = hashlib.sha256(json.dumps({"profile": raw_profile, "ceremonies": [asdict(cs[0])]}, sort_keys=True, default=str).encode()).hexdigest()
    named = replace(p, bride_name="Priya", groom_name="Rahul")
    assert context_fingerprint(named, cs) == context_fingerprint(p, cs) == legacy
    assert planning_fingerprint(named, cs) == planning_fingerprint(p, cs)


def test_dashboard_totals_reconcile_actual_rows_reservations_contingency_and_shared_once():
    p, cs = profile(), [ceremony(budget=200000), ceremony("reception", budget=300000)]
    plan = replace(reviewed_plan(p, cs), accepted=True)
    data = budget_dashboard_data(plan, p, cs)
    a = data["assessment"]
    assert sum(data["categories"].values()) == sum(e.allocated_inr for e in plan.expenses)
    assert sum(data["categories"].values()) == sum(a.ceremony_expenses.values()) + a.shared_inr
    assert a.total_inr == sum(data["categories"].values()) + sum(a.ceremony_reserves.values()) + a.contingency_inr
    shared_photo = next(e for e in plan.expenses if e.category == "photography")
    assert data["categories"]["Photography and videography"] == shared_photo.allocated_inr
    assert data["contributions"][("Photography and videography", None)] == shared_photo.allocated_inr
    assert sum(data["categories"].values()) != a.total_inr


def test_custom_names_and_equivalent_builtins_keep_original_records():
    p, cs = profile(), [ceremony()]
    plan = add_expense(reviewed_plan(p, cs), "haldi", "My catering keeps its custom identity", 1234)
    plan = add_expense(plan, None, "Family transport", 2345)
    data = budget_dashboard_data(replace(plan, accepted=True), p, cs)
    assert data["categories"]["Other / custom expenses"] == 3579
    assert data["categories"]["Seating and staffing"] == sum(e.allocated_inr for e in plan.expenses if e.category in {"seating", "service_staff"})
    assert {r["Expense"] for r in data["records"] if r["Original category"] == "custom"} == {"Family transport", "My catering keeps its custom identity"}
    assert len(data["records"]) == len(plan.expenses)


def test_unsaved_plan_is_not_analytics_and_stale_accepted_allocations_are_preserved():
    p, cs = profile(), [ceremony(budget=200000)]
    plan = reviewed_plan(p, cs)
    assert budget_dashboard_data(plan, p, cs) is None
    current = [ceremony(budget=800000), ceremony("reception")]
    data = budget_dashboard_data(replace(plan, accepted=True), p, current)
    assert data["stale"]
    assert data["assessment"].ceremony_allocations["haldi"] == 200000
    assert data["ceremonies"][1]["Allocated (INR)"] is None
    removed = budget_dashboard_data(replace(plan, accepted=True), p, [ceremony("reception")])
    assert removed["assessment"].ceremony_allocations["haldi"] == 200000
    assert removed["ceremonies"][-1]["Status"] == "Previously selected; review"


def test_shortfalls_are_preserved_and_displayed_without_changing_rows():
    p, cs = profile(), [ceremony(budget=1000)]
    plan = replace(reviewed_plan(p, cs), accepted=True)
    before = plan.expenses
    data = budget_dashboard_data(plan, p, cs)
    assert data["ceremonies"][0]["Status"] == "Shortfall"
    assert data["ceremonies"][0]["Remaining allocation (INR)"] < 0
    assert plan.expenses == before and data["assessment"].issues


def test_saved_status_alerts_are_actionable_and_not_fake_conflicts():
    p, cs = profile(), [ceremony(guests=None, venue=VenuePreference.UNDECIDED)]
    alerts = planning_alerts(p, cs)
    assert any("date is not decided" in a.message for a in alerts)
    assert any("venue is undecided" in a.message for a in alerts)
    assert any("guest count" in a.message for a in alerts)
    assert any("No accepted budget" in a.message for a in alerts)
    assert not any(a.kind == "Scheduling conflict" for a in alerts)
    assert all(a.route for a in alerts)
    cs = [dated_ceremony(), dated_ceremony("mehendi")]
    timeline = replace(fallback_timeline(p, cs), accepted=True)
    assert not any(a.kind == "Scheduling conflict" for a in planning_alerts(p, cs, timeline=timeline))
    assert any(a.kind == "Pending work" for a in planning_alerts(p, cs, timeline=timeline))


def test_duplicate_ceremony_labels_keep_distinct_contribution_ids():
    p, cs = profile(), [ceremony(), ceremony("reception")]
    plan = replace(reviewed_plan(p, cs), accepted=True)
    current = [replace(c, name="Same display name") for c in cs]
    data = budget_dashboard_data(plan, p, current)
    assert ("Food and catering", "haldi") in data["contributions"]
    assert ("Food and catering", "reception") in data["contributions"]


def test_no_ceiling_alerts_when_overall_budget_was_cleared():
    p, cs = profile(), [ceremony()]
    plan = replace(reviewed_plan(p, cs), accepted=True)
    alerts = planning_alerts(replace(p, budget_inr=None), cs, budget=plan)
    assert not any("exceeds" in a.message or "shortfall" in a.message for a in alerts)
    assert any("overall wedding budget" in a.message for a in alerts)
