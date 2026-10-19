from dataclasses import replace
from datetime import date, time, timedelta
import json

import pytest

from ai_event_planning_platform.ceremony_plan import CeremonyDateCertainty
from ai_event_planning_platform.budget_engine import generate_budget
from ai_event_planning_platform.wedding_profile import WeddingDateStatus, WeddingLocationPreference
from ai_event_planning_platform.wedding_timeline import (
    PHASES, CeremonyActivity, PreparationTask, add_activity, calculate_deadline,
    current_schedules, empty_timeline, fallback_timeline, move_activity,
    planning_fingerprint, rebase_timeline, schedule_issues, timeline_from_json, timeline_to_json,
)
from test_budget_engine import ceremony, profile, reviewed_plan


DAY = date(2027, 11, 12)


def dated_ceremony(cid="haldi", day=DAY, certainty=CeremonyDateCertainty.FIXED):
    c = ceremony(cid)
    return replace(c, plan=replace(c.plan, ceremony_date=day, date_certainty=certainty))


def codes(plan):
    return {i.code for i in schedule_issues(plan, today=date(2026, 10, 17))}


def test_fixed_deadlines_use_correct_global_and_ceremony_anchors():
    p = profile(wedding_date_status=WeddingDateStatus.FIXED_DATE, wedding_date=DAY + timedelta(days=2))
    plan = fallback_timeline(p, [dated_ceremony()])
    assert {t.phase for t in plan.tasks} == set(PHASES)
    assert plan.tasks[0].due_date == p.wedding_date - timedelta(days=90)
    assert plan.tasks[-1].due_date == DAY
    assert plan.tasks[0].due_source == "calculated"
    assert all(a.start_time is a.end_time is None for a in plan.schedules[0].activities)


@pytest.mark.parametrize("status,value", [(WeddingDateStatus.APPROXIMATE_DATE, "2027-11"),
                                           (WeddingDateStatus.NOT_DECIDED_YET, None)])
def test_relative_windows_without_fabricated_dates(status, value):
    p = profile(wedding_date_status=status, wedding_date=value)
    plan = fallback_timeline(p, [ceremony()])
    assert all(t.due_date is None and t.relative_window for t in plan.tasks)
    assert plan.schedules[0].ceremony_date is None
    explicit = fallback_timeline(p, [dated_ceremony()])
    assert explicit.schedules[0].ceremony_date == DAY
    assert explicit.tasks[-1].due_date == DAY
    assert explicit.tasks[0].due_date is None


def test_tentative_ceremony_never_becomes_fixed_or_anchors_deadlines():
    plan = fallback_timeline(profile(), [dated_ceremony(certainty=CeremonyDateCertainty.TENTATIVE)])
    assert plan.schedules[0].date_certainty == "tentative"
    assert plan.tasks[-1].due_date is None


def test_user_deadlines_and_retained_activity_ids_survive_context_refresh():
    p, c = profile(), dated_ceremony()
    plan = fallback_timeline(p, [c])
    t = replace(plan.tasks[-1], due_date=DAY + timedelta(days=1), due_source="user", notes="Keep this")
    plan = replace(plan, tasks=(t,))
    moved = dated_ceremony(day=DAY + timedelta(days=5))
    refreshed = rebase_timeline(plan, p, [moved, ceremony("reception")])
    assert refreshed.tasks == (t,)
    assert refreshed.schedules[0].activities == plan.schedules[0].activities
    assert refreshed.schedules[0].ceremony_date == moved.plan.ceremony_date
    assert not refreshed.accepted
    removed = rebase_timeline(plan, p, [ceremony("reception")])
    assert removed.tasks[0].ceremony_id is None
    assert "removed" in removed.tasks[0].notes


def test_budget_meal_requires_current_accepted_reviewed_preference():
    p, ceremonies = profile(), [ceremony()]
    budget = reviewed_plan(p, ceremonies)
    assert current_schedules(ceremonies, budget=budget, profile=p)[0].meal is None
    accepted = replace(budget, accepted=True)
    assert current_schedules(ceremonies, budget=accepted, profile=p)[0].meal == "breakfast"
    assert current_schedules(ceremonies, budget=accepted, profile=profile(city="Pune"))[0].meal is None
    no_meal = replace(generate_budget(p, ceremonies, [replace(n, catering="none", services=tuple(s for s in n.services if s != "catering")) for n in accepted.needs]), accepted=True)
    assert not any(a.kind == "meal" for a in fallback_timeline(p, ceremonies, no_meal).schedules[0].activities)


def test_fallback_destination_and_optional_meals_no_assumed_rituals():
    plan = fallback_timeline(profile(location_preference=WeddingLocationPreference.DESTINATION), [ceremony()])
    assert any("travel" in t.title for t in plan.tasks)
    assert any("optional" in a.title for a in plan.schedules[0].activities)
    assert all(a.source == "fallback_suggestion" and a.duration_minutes is None for a in plan.schedules[0].activities)


def test_stable_activity_order_and_json_roundtrip():
    plan = add_activity(add_activity(empty_timeline(profile(), [ceremony()]), "haldi"), "haldi")
    ids = [a.activity_id for a in plan.schedules[0].activities]
    moved = move_activity(plan, "haldi", ids[1], -1)
    assert [a.activity_id for a in moved.schedules[0].activities] == ids[::-1]
    assert [a.order for a in moved.schedules[0].activities] == [1, 2]
    assert timeline_from_json(json.loads(json.dumps(timeline_to_json(moved)))) == moved


@pytest.mark.parametrize("mutate", [
    lambda v: v.update(version=2),
    lambda v: v["schedules"][0].update(ceremony_date="2027-02-30"),
    lambda v: v["schedules"][0].update(start_time="09:00+05:30"),
    lambda v: v["schedules"][0].update(start_time="09:00:30"),
    lambda v: v["schedules"][0]["activities"][0].update(title=""),
    lambda v: v["tasks"][0].update(due_date="2027-13-01"),
    lambda v: v["tasks"][0].update(status="booked"),
])
def test_malformed_json_rejected(mutate):
    raw = timeline_to_json(fallback_timeline(profile(), [ceremony()]))
    mutate(raw)
    with pytest.raises(ValueError):
        timeline_from_json(raw)


def test_same_day_and_time_preference_alone_are_not_overlaps():
    plan = empty_timeline(profile(), [dated_ceremony(), dated_ceremony("mehendi")])
    assert "ceremony_overlap" not in codes(plan)
    a, b = plan.schedules
    plan = replace(plan, schedules=(replace(a, start_time=time(9), end_time=time(11)), replace(b, start_time=time(11), end_time=time(12))))
    assert "ceremony_overlap" not in codes(plan)
    overlap = replace(plan, schedules=(plan.schedules[0], replace(plan.schedules[1], start_time=time(10))))
    assert "ceremony_overlap" in codes(overlap)
    assert all(i.resolution for i in schedule_issues(overlap))


def test_invalid_ranges_and_activity_clashes_are_distinct_from_incomplete_information():
    plan = empty_timeline(profile(), [dated_ceremony()])
    activities = (CeremonyActivity("a", "haldi", "Guest arrivals", kind="arrival", start_time=time(10), end_time=time(11)),
                  CeremonyActivity("b", "haldi", "Photography", order=2, start_time=time(10, 30), end_time=time(12)))
    plan = replace(plan, schedules=(replace(plan.schedules[0], activities=activities),))
    assert {"activity_overlap", "missing_setup", "incomplete_ceremony"} <= codes(plan)
    bad = replace(plan, schedules=(replace(plan.schedules[0], start_time=time(12), end_time=time(9)),))
    assert any(i.code == "invalid_time_range" and i.severity == "error" for i in schedule_issues(bad))
    assert plan.schedules[0].start_time is None


def test_setup_readiness_only_when_clock_information_exists():
    plan = fallback_timeline(profile(), [dated_ceremony()])
    assert "missing_setup" not in codes(plan) and "setup_late" not in codes(plan)
    s = plan.schedules[0]
    activities = tuple(replace(a, start_time=time(10)) if a.kind == "arrival" else a for a in s.activities)
    unknown = replace(plan, schedules=(replace(s, activities=activities),))
    assert "setup_time_unknown" in codes(unknown)
    late = replace(unknown, schedules=(replace(s, activities=tuple(replace(a, end_time=time(11)) if a.kind == "setup" else a for a in activities)),))
    assert "setup_late" in codes(late)


def test_dependency_cycles_missing_impossible_order_and_late_tasks():
    plan = empty_timeline(profile(), [dated_ceremony()])
    tasks = (PreparationTask("a", "First", due_date=DAY, dependency_ids=("b",)),
             PreparationTask("b", "Second", due_date=DAY + timedelta(days=1), dependency_ids=("a",), ceremony_id="haldi"),
             PreparationTask("c", "Third", dependency_ids=("missing",), due_date=date(2026, 1, 1)))
    plan = replace(plan, tasks=tasks)
    assert {"dependency_cycle", "missing_dependency", "dependency_order", "task_after_ceremony", "past_due"} <= codes(plan)
    assert {i.severity for i in schedule_issues(plan)} == {"warning", "error", "incomplete"}
    assert "past_due" not in codes(replace(plan, tasks=(replace(tasks[-1], status="completed", dependency_ids=()),)))


def test_context_changes_include_budget_and_no_mutation_during_checks():
    p, cs = profile(), [ceremony()]
    budget = replace(reviewed_plan(p, cs), accepted=True)
    assert planning_fingerprint(p, cs) != planning_fingerprint(p, cs, budget)
    plan = fallback_timeline(p, cs)
    before = timeline_to_json(plan)
    schedule_issues(plan)
    assert timeline_to_json(plan) == before
