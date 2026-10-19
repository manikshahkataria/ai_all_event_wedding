"""Local-date planning models, opt-in suggestions and deterministic schedule checks."""

from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
from uuid import uuid4

from ai_event_planning_platform.budget_engine import budget_context, budget_plan_to_json, context_fingerprint
from ai_event_planning_platform.ceremony_plan import CeremonyDateCertainty
from ai_event_planning_platform.wedding_profile import WeddingDateStatus


PHASES = ("Early planning", "Venue and vendor arrangements", "Guest and invitation preparation",
          "Final arrangements", "Wedding week", "Ceremony day")
STATUSES = ("pending", "in_progress", "completed")
SOURCES = ("ai_suggestion", "fallback_suggestion", "user_created")
KINDS = ("setup", "checks", "arrival", "ceremony", "meal", "photography", "cleanup", "other")


def local_today():
    # Used only for warnings and the calendar's initial selection, never to shift stored dates.
    return datetime.now(timezone(timedelta(hours=5, minutes=30))).date()


@dataclass(frozen=True)
class CeremonyActivity:
    activity_id: str
    ceremony_id: str
    title: str
    description: str = ""
    order: int = 1
    start_time: time | None = None
    end_time: time | None = None
    duration_minutes: int | None = None
    status: str = "pending"
    notes: str = ""
    source: str = "user_created"
    kind: str = "other"


@dataclass(frozen=True)
class CeremonySchedule:
    ceremony_id: str
    ceremony_name: str
    ceremony_date: date | None = None
    date_certainty: str = "undecided"
    preferred_timing: str | None = None
    start_time: time | None = None
    end_time: time | None = None
    venue: str = "Not provided"
    guest_count: int | None = None
    activities: tuple[CeremonyActivity, ...] = ()
    notes: str = ""
    meal: str | None = None
    meal_source: str = "not_provided"


@dataclass(frozen=True)
class PreparationTask:
    task_id: str
    title: str
    description: str = ""
    ceremony_id: str | None = None
    phase: str = PHASES[0]
    due_date: date | None = None
    relative_window: str = "When planning begins"
    days_before: int | None = None
    priority: str = "normal"
    status: str = "pending"
    dependency_ids: tuple[str, ...] = ()
    notes: str = ""
    source: str = "user_created"
    due_source: str = "not_provided"


@dataclass(frozen=True)
class WeddingTimeline:
    context_fingerprint: str
    schedules: tuple[CeremonySchedule, ...]
    tasks: tuple[PreparationTask, ...] = ()
    accepted: bool = False


@dataclass(frozen=True)
class ScheduleIssue:
    severity: str
    code: str
    message: str
    resolution: str


def planning_context(profile, ceremonies, budget=None):
    return {**budget_context(profile, ceremonies),
            "approved_budget": budget_plan_to_json(budget) if budget and budget.accepted else None,
            "budget_matches_context": bool(budget and budget.accepted and budget.context_fingerprint == context_fingerprint(profile, ceremonies))}


def planning_fingerprint(profile, ceremonies, budget=None):
    return hashlib.sha256(json.dumps(planning_context(profile, ceremonies, budget), sort_keys=True, default=str).encode()).hexdigest()


def current_schedules(ceremonies, timeline=None, budget=None, profile=None):
    """Dates/names/venues always follow saved ceremony plans, never AI output."""
    previous = {s.ceremony_id: s for s in timeline.schedules} if timeline else {}
    meal_needs = {n.ceremony_id: n for n in budget.needs} if (
        budget and budget.accepted and profile is not None
        and budget.context_fingerprint == context_fingerprint(profile, ceremonies)
    ) else {}
    result = []
    for c in ceremonies:
        p, old = c.plan, previous.get(c.ceremony_id)
        needs = meal_needs.get(c.ceremony_id)
        meal = needs.catering if needs and needs.reviewed and (needs.catering == "none" or "catering" in needs.services) else None
        venue = p.venue_preference.value.replace("_", " ") if p else "Not provided"
        if p and p.venue_notes:
            venue += ": " + p.venue_notes
        result.append(CeremonySchedule(c.ceremony_id, c.name,
            ceremony_date=p.ceremony_date if p else None,
            date_certainty=p.date_certainty.value if p else "undecided",
            preferred_timing=p.preferred_timing.value if p and p.preferred_timing else None,
            start_time=old.start_time if old else None, end_time=old.end_time if old else None,
            venue=venue, guest_count=p.guest_count if p else None,
            activities=old.activities if old else (), notes=old.notes if old else "",
            meal=meal, meal_source="accepted_budget" if meal is not None else "not_provided"))
    return tuple(result)


def empty_timeline(profile, ceremonies, budget=None):
    return WeddingTimeline(planning_fingerprint(profile, ceremonies, budget),
                           current_schedules(ceremonies, budget=budget, profile=profile))


def task_anchor(task, profile, ceremonies):
    if task.ceremony_id:
        c = next((c for c in ceremonies if c.ceremony_id == task.ceremony_id), None)
        return c.plan.ceremony_date if c and c.plan and c.plan.date_certainty is CeremonyDateCertainty.FIXED else None
    if profile.wedding_date_status is WeddingDateStatus.FIXED_DATE:
        value = profile.wedding_date
        return date.fromisoformat(value) if isinstance(value, str) else value
    return None


def calculate_deadline(task, profile, ceremonies):
    if task.due_source == "user":
        return task
    anchor = task_anchor(task, profile, ceremonies)
    try:
        due = anchor - timedelta(days=task.days_before) if anchor and task.days_before is not None else None
    except OverflowError:
        due = None
    return replace(task, due_date=due, due_source="calculated" if due else "not_provided")


def rebase_timeline(plan, profile, ceremonies, budget=None):
    ids = {c.ceremony_id for c in ceremonies}
    tasks = [replace(t, ceremony_id=None, notes=(t.notes + " Previously linked ceremony was removed.").strip())
             if t.ceremony_id and t.ceremony_id not in ids else t for t in plan.tasks]
    return WeddingTimeline(planning_fingerprint(profile, ceremonies, budget),
        current_schedules(ceremonies, plan, budget, profile),
        tuple(calculate_deadline(t, profile, ceremonies) for t in tasks))


def fallback_timeline(profile, ceremonies, budget=None):
    plan = empty_timeline(profile, ceremonies, budget)
    definitions = [
        ("Review ceremony requirements and budget", PHASES[0], 90, "3–6 months before; review early planning choices"),
        ("Review venues and shortlist vendors", PHASES[1], 60, "2–3 months before; availability to be confirmed"),
        ("Update guest list and plan invitation arrangements", PHASES[2], 30, "1–2 months before; no invitations generated"),
        ("Review catering, decoration and photography scope", PHASES[3], 14, "2–4 weeks before"),
        ("Confirm vendor preparations and final schedules", PHASES[4], 7, "Wedding week"),
    ]
    if profile.location_preference and profile.location_preference.value == "destination":
        definitions.append(("Review travel and accommodation arrangements", PHASES[2], 30, "1–2 months before; bookings unconfirmed"))
    tasks = [PreparationTask(str(uuid4()), title, phase=phase, days_before=days,
                             relative_window=window, source="fallback_suggestion")
             for title, phase, days, window in definitions]
    schedules = []
    for s in plan.schedules:
        entries = [("Venue preparation", "setup"), ("Vendor, decoration and equipment checks", "checks"),
                   ("Guest arrivals", "arrival"), (s.ceremony_name + " activities", "ceremony"),
                   ("Photography / video coverage review", "photography")]
        if s.meal and s.meal != "none":
            entries.append((s.meal.capitalize() + " arrangements", "meal"))
        elif s.meal is None:
            entries.append(("Review refreshments or meal needs (optional)", "meal"))
        entries.append(("Closure and cleanup", "cleanup"))
        activities = tuple(CeremonyActivity(str(uuid4()), s.ceremony_id, title,
            description="Optional planning suggestion; no booking, ritual or clock time is confirmed.",
            order=i + 1, source="fallback_suggestion", kind=kind) for i, (title, kind) in enumerate(entries))
        schedules.append(replace(s, activities=activities))
        tasks.append(PreparationTask(str(uuid4()), "Review " + s.ceremony_name + " setup and cleanup checklist",
            ceremony_id=s.ceremony_id, phase=PHASES[5], days_before=0,
            relative_window="Ceremony day; confirm responsibilities", source="fallback_suggestion"))
    return replace(plan, schedules=tuple(schedules), tasks=tuple(calculate_deadline(t, profile, ceremonies) for t in tasks))


def add_activity(plan, ceremony_id):
    return replace(plan, accepted=False, schedules=tuple(replace(s, activities=(*s.activities,
        CeremonyActivity(str(uuid4()), ceremony_id, "New activity", order=len(s.activities) + 1)))
        if s.ceremony_id == ceremony_id else s for s in plan.schedules))


def move_activity(plan, ceremony_id, activity_id, direction):
    schedules = []
    for s in plan.schedules:
        if s.ceremony_id == ceremony_id:
            rows = sorted(s.activities, key=lambda a: a.order)
            i = next(i for i, a in enumerate(rows) if a.activity_id == activity_id)
            j = max(0, min(len(rows) - 1, i + direction))
            rows[i], rows[j] = rows[j], rows[i]
            s = replace(s, activities=tuple(replace(a, order=i + 1) for i, a in enumerate(rows)))
        schedules.append(s)
    return replace(plan, schedules=tuple(schedules), accepted=False)


def validate_timeline(plan):
    def text(value, maximum, required=False):
        return isinstance(value, str) and len(value) <= maximum and (not required or bool(value.strip()))

    def day(value):
        return value is None or type(value) is date

    def clock(value):
        return value is None or (type(value) is time and value.tzinfo is None and value.second == 0 and value.microsecond == 0)

    def identity(value):
        return text(value, 80, True)

    if (not isinstance(plan, WeddingTimeline) or not text(plan.context_fingerprint, 64, True)
            or len(plan.context_fingerprint) != 64 or type(plan.accepted) is not bool
            or not isinstance(plan.schedules, tuple) or not isinstance(plan.tasks, tuple)):
        raise ValueError("Invalid timeline structure.")
    ids, activity_ids = set(), set()
    for s in plan.schedules:
        if (not isinstance(s, CeremonySchedule) or not identity(s.ceremony_id) or s.ceremony_id in ids
                or not text(s.ceremony_name, 120, True) or not day(s.ceremony_date)
                or s.date_certainty not in {"fixed", "tentative", "undecided"}
                or (s.ceremony_date is not None and s.date_certainty == "undecided")
                or s.preferred_timing not in {None, "morning", "afternoon", "evening", "night", "flexible"}
                or not clock(s.start_time) or not clock(s.end_time) or not text(s.venue, 2000)
                or (s.guest_count is not None and (type(s.guest_count) is not int or s.guest_count < 0))
                or not text(s.notes, 2000) or s.meal not in {None, "breakfast", "lunch", "dinner", "snacks", "none"}
                or s.meal_source not in {"accepted_budget", "not_provided"} or not isinstance(s.activities, tuple)):
            raise ValueError("Invalid ceremony schedule fields.")
        ids.add(s.ceremony_id)
        orders = set()
        for a in s.activities:
            if (not isinstance(a, CeremonyActivity) or not identity(a.activity_id) or a.activity_id in activity_ids
                    or a.ceremony_id != s.ceremony_id or not text(a.title, 120, True)
                    or not text(a.description, 2000) or not text(a.notes, 2000)
                    or type(a.order) is not int or a.order < 1 or a.order in orders
                    or not clock(a.start_time) or not clock(a.end_time)
                    or (a.duration_minutes is not None and (type(a.duration_minutes) is not int or not 1 <= a.duration_minutes <= 1440))
                    or a.status not in STATUSES or a.source not in SOURCES or a.kind not in KINDS):
                raise ValueError("Invalid activity fields, identity or ordering.")
            activity_ids.add(a.activity_id)
            orders.add(a.order)
    task_ids = set()
    for t in plan.tasks:
        if (not isinstance(t, PreparationTask) or not identity(t.task_id) or t.task_id in task_ids
                or not text(t.title, 120, True) or not text(t.description, 2000) or not text(t.notes, 2000)
                or (t.ceremony_id is not None and t.ceremony_id not in ids) or t.phase not in PHASES
                or not day(t.due_date) or not text(t.relative_window, 300)
                or (t.days_before is not None and (type(t.days_before) is not int or not 0 <= t.days_before <= 3650))
                or t.priority not in {"low", "normal", "high"} or t.status not in STATUSES
                or not isinstance(t.dependency_ids, tuple) or any(not identity(v) for v in t.dependency_ids)
                or len(set(t.dependency_ids)) != len(t.dependency_ids) or t.source not in SOURCES
                or t.due_source not in {"user", "calculated", "not_provided"}):
            raise ValueError("Invalid preparation task fields.")
        task_ids.add(t.task_id)


def schedule_issues(plan, *, today=None):
    validate_timeline(plan)
    today = today or local_today()
    issues = []

    def report(severity, code, message, resolution):
        issues.append(ScheduleIssue(severity, code, message, resolution))

    def interval(item, name):
        if item.start_time and item.end_time and item.end_time <= item.start_time:
            report("error", "invalid_time_range", name + ": end must be after start on the same date.", "Correct the times; split overnight work across dates.")
            return False
        return bool(item.start_time and item.end_time)

    for s in plan.schedules:
        valid = interval(s, s.ceremony_name)
        if not s.ceremony_date or not valid:
            report("incomplete", "incomplete_ceremony", s.ceremony_name + ": date or clock range is incomplete.", "Enter saved ceremony dates and explicit times when known; flexible timing is allowed.")
        activities = sorted(s.activities, key=lambda a: a.order)
        missing_clocks = sum(a.start_time is None or a.end_time is None for a in activities)
        if missing_clocks:
            report("incomplete", "incomplete_activities", f"{s.ceremony_name}: {missing_clocks} activities have incomplete clock times.", "Use activity order while times are flexible, or enter explicit start/end times when known.")
        timed = [a for a in activities if interval(a, s.ceremony_name + " / " + a.title)]
        for i, a in enumerate(timed):
            for b in timed[i + 1:]:
                if a.start_time < b.end_time and b.start_time < a.end_time:
                    report("warning", "activity_overlap", f"{s.ceremony_name}: {a.title} overlaps {b.title}.", "Adjust times or confirm that parallel activities have separate coverage.")
            if a.duration_minutes and a.duration_minutes != (_minutes(a.end_time) - _minutes(a.start_time)):
                report("warning", "duration_mismatch", a.title + ": duration differs from its clock range.", "Review the duration or the explicit times.")
            if s.end_time and a.end_time > s.end_time:
                report("warning", "activity_after_end", a.title + ": finishes after the ceremony end.", "Review closure/cleanup coverage or adjust the event range.")
        starts = [a.start_time for a in activities if a.kind in {"arrival", "ceremony"} and a.start_time]
        setup = [a for a in activities if a.kind == "setup"]
        if starts and not setup:
            report("warning", "missing_setup", s.ceremony_name + ": no setup activity precedes the timed arrivals/ceremony.", "Add setup time or document that preparation is already covered.")
        elif starts and setup and not any(a.end_time for a in setup):
            report("incomplete", "setup_time_unknown", s.ceremony_name + ": setup finish time is unknown.", "Enter setup timing before checking readiness.")
        elif starts and setup and max(a.end_time for a in setup if a.end_time) > min(starts):
            report("warning", "setup_late", s.ceremony_name + ": setup finishes after timed arrivals/ceremony begin.", "Finish setup earlier or revise arrival time.")
    for i, a in enumerate(plan.schedules):
        for b in plan.schedules[i + 1:]:
            if (a.ceremony_date and a.ceremony_date == b.ceremony_date and a.start_time and a.end_time
                    and b.start_time and b.end_time and a.end_time > a.start_time and b.end_time > b.start_time
                    and a.start_time < b.end_time and b.start_time < a.end_time):
                fixed = a.date_certainty == b.date_certainty == "fixed"
                report("warning", "ceremony_overlap", f"{a.ceremony_name} overlaps {b.ceremony_name} ({'fixed dates' if fixed else 'tentative dates; potential overlap'}).",
                       "Review actual times, venues and staffing; sharing a date alone is not a conflict.")
    by_id = {t.task_id: t for t in plan.tasks}
    dates = {s.ceremony_id: s.ceremony_date for s in plan.schedules}
    for t in plan.tasks:
        if t.due_date and t.due_date < today and t.status != "completed":
            report("warning", "past_due", t.title + ": deadline is in the past.", "Choose a realistic deadline or mark work completed; no date was moved automatically.")
        if t.due_date and t.ceremony_id and dates.get(t.ceremony_id) and t.due_date > dates[t.ceremony_id]:
            report("warning", "task_after_ceremony", t.title + ": preparation deadline is after its ceremony.", "Move preparation earlier or revise its relationship to the ceremony.")
        for dep in t.dependency_ids:
            if dep not in by_id:
                report("error", "missing_dependency", t.title + ": a prerequisite no longer exists.", "Restore the prerequisite or remove its dependency link.")
            elif t.due_date and by_id[dep].due_date and by_id[dep].due_date > t.due_date:
                report("warning", "dependency_order", t.title + ": its prerequisite has a later deadline.", "Schedule the prerequisite earlier or the dependent task later.")
            elif t.status == "completed" and by_id[dep].status != "completed":
                report("warning", "dependency_incomplete", t.title + ": completed while a prerequisite is unfinished.", "Review completion statuses and remaining prerequisite work.")
    visiting, visited = set(), set()

    def visit(key):
        if key in visiting:
            return True
        if key in visited:
            return False
        visiting.add(key)
        cyclic = any(visit(dep) for dep in by_id[key].dependency_ids if dep in by_id)
        visiting.remove(key)
        visited.add(key)
        return cyclic

    if any(visit(key) for key in by_id):
        report("error", "dependency_cycle", "Preparation task dependencies contain a cycle.", "Remove circular/self dependencies before saving.")
    return tuple(issues)


def _minutes(value):
    return value.hour * 60 + value.minute


def timeline_to_json(plan):
    validate_timeline(plan)
    return json.loads(json.dumps({"version": 1, **asdict(plan)}, default=lambda v: v.isoformat()))


def timeline_from_json(value):
    if not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != 1:
        raise ValueError("Unsupported wedding timeline JSON.")
    try:
        data = {k: v for k, v in value.items() if k != "version"}
        schedules = []
        for raw in data["schedules"]:
            s = dict(raw)
            s["ceremony_date"] = date.fromisoformat(s["ceremony_date"]) if s.get("ceremony_date") is not None else None
            for key in ("start_time", "end_time"):
                s[key] = time.fromisoformat(s[key]) if s.get(key) is not None else None
            activities = []
            for raw_a in s["activities"]:
                a = dict(raw_a)
                for key in ("start_time", "end_time"):
                    a[key] = time.fromisoformat(a[key]) if a.get(key) is not None else None
                activities.append(CeremonyActivity(**a))
            schedules.append(CeremonySchedule(**{**s, "activities": tuple(activities)}))
        data["schedules"] = tuple(schedules)
        data["tasks"] = tuple(PreparationTask(**{**t,
            "due_date": date.fromisoformat(t["due_date"]) if t.get("due_date") is not None else None,
            "dependency_ids": tuple(t["dependency_ids"])}) for t in data["tasks"])
        plan = WeddingTimeline(**data)
        validate_timeline(plan)
    except (TypeError, ValueError, KeyError, AttributeError):
        raise ValueError("Invalid wedding timeline JSON fields.") from None
    return plan
