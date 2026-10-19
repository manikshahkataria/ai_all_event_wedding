from dataclasses import replace
from datetime import date, time

from ai_event_planning_platform.ceremony_plan import CeremonyDateCertainty
from ai_event_planning_platform.wedding_calendar import (
    activity_agenda, clock_range, daily_schedules, month_grid, saved_calendar_data,
    shift_day, shift_month, week_dates,
)
from ai_event_planning_platform.wedding_timeline import CeremonyActivity, fallback_timeline
from test_budget_engine import ceremony, profile
from test_wedding_timeline import DAY, dated_ceremony


def test_leap_month_weekday_alignment_and_every_date_once():
    grid = month_grid(2028, 2)
    assert all(len(week) == 7 for week in grid)
    assert grid[0][0] is None and grid[0][1] == date(2028, 2, 1)
    days = [day for week in grid for day in week if day]
    assert len(days) == 29 and days[-1] == date(2028, 2, 29)


def test_month_year_boundaries_week_and_day_navigation():
    assert shift_month(date(2027, 12, 31), 1) == date(2028, 1, 31)
    assert shift_month(date(2028, 3, 31), -1) == date(2028, 2, 29)
    assert shift_month(date.min, -1) == date.min
    assert shift_month(date.max, 1) == date.max
    week = week_dates(date(2028, 1, 1))
    assert week[0] == date(2027, 12, 27) and week[-1] == date(2028, 1, 2)
    assert shift_day(date(2028, 2, 28), 1) == date(2028, 2, 29)
    assert shift_day(date.max, 1) == date.max


def test_calendar_ignores_unsaved_suggestions_and_reflects_updated_ceremony_details():
    p, cs = profile(), [dated_ceremony(), ceremony("reception")]
    draft = fallback_timeline(p, cs)
    schedules, tasks = saved_calendar_data(p, cs, draft)
    assert len(daily_schedules(schedules, DAY)) == 1
    assert schedules[1].ceremony_date is None
    assert not schedules[0].activities and not tasks
    saved = replace(draft, accepted=True)
    schedules, tasks = saved_calendar_data(p, cs, saved)
    assert schedules[0].activities and tasks
    updated = dated_ceremony(day=date(2027, 11, 15), certainty=CeremonyDateCertainty.TENTATIVE)
    schedules, _ = saved_calendar_data(p, [updated], saved)
    assert schedules[0].ceremony_date == date(2027, 11, 15) and schedules[0].date_certainty == "tentative"
    assert not daily_schedules(schedules, DAY)


def test_multiple_events_and_global_activity_agenda_use_explicit_clock_order():
    plan = fallback_timeline(profile(), [dated_ceremony(), dated_ceremony("mehendi"), dated_ceremony("reception")])
    a, b, c = plan.schedules
    a = replace(a, start_time=time(18), activities=(CeremonyActivity("a", "haldi", "Late", start_time=time(19)),))
    b = replace(b, start_time=time(9), activities=(CeremonyActivity("b", "mehendi", "Early", start_time=time(10)),))
    c = replace(c, activities=(CeremonyActivity("c", "reception", "Flexible"),))
    assert [s.ceremony_id for s in daily_schedules((a, b, c), DAY)] == ["mehendi", "haldi", "reception"]
    assert [a.activity_id for a, _s in activity_agenda((a, b, c), DAY)] == ["b", "a", "c"]
    assert "Flexible" in clock_range(None, None) and "00:00" not in clock_range(None, None)
