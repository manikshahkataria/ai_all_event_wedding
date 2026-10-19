"""Native interactive calendar over saved local dates and accepted execution plans."""

import calendar
from datetime import date

import streamlit as st

from ai_event_planning_platform.wedding_timeline import current_schedules, local_today

COLORS = ("🔵", "🟣", "🟢", "🟠", "🔴", "🟡")


def month_grid(year, month):
    return tuple(tuple(date(year, month, n) if n else None for n in week)
                 for week in calendar.Calendar(firstweekday=0).monthdayscalendar(year, month))


def shift_month(value, offset):
    index = max(0, min(9999 * 12 - 1, (value.year - 1) * 12 + value.month - 1 + offset))
    year, month = index // 12 + 1, index % 12 + 1
    return date(year, month, min(value.day, calendar.monthrange(year, month)[1]))


def shift_day(value, offset):
    return date.fromordinal(max(date.min.toordinal(), min(date.max.toordinal(), value.toordinal() + offset)))


def week_dates(value):
    start = shift_day(value, -value.weekday())
    return tuple(shift_day(start, i) for i in range(7) if start.toordinal() + i <= date.max.toordinal())


def saved_calendar_data(profile, ceremonies, timeline=None, budget=None):
    accepted = timeline if timeline and timeline.accepted else None
    ids = {c.ceremony_id for c in ceremonies}
    tasks = tuple(t for t in accepted.tasks if t.ceremony_id is None or t.ceremony_id in ids) if accepted else ()
    return current_schedules(ceremonies, accepted, budget, profile), tasks


def daily_schedules(schedules, day):
    return tuple(sorted((s for s in schedules if s.ceremony_date == day),
                        key=lambda s: (s.start_time is None, s.start_time.isoformat() if s.start_time else "", s.ceremony_id)))


def activity_agenda(schedules, day):
    return tuple(sorted(((a, s) for s in schedules if s.ceremony_date == day for a in s.activities),
                        key=lambda pair: (pair[0].start_time is None,
                            pair[0].start_time.isoformat() if pair[0].start_time else "",
                            pair[1].ceremony_id, pair[0].order, pair[0].activity_id)))


def clock_range(start, end):
    if start is None and end is None:
        return "Flexible — clock times not provided"
    return f"{start.strftime('%H:%M') if start else 'Start unknown'} – {end.strftime('%H:%M') if end else 'End unknown'}"


def _select(day):
    st.session_state["wedding_calendar_selected"] = day
    st.session_state["wedding_calendar_cursor"] = day.replace(day=1)
    st.session_state["wedding_calendar_picker"] = day


def _pick_date():
    chosen = st.session_state["wedding_calendar_picker"]
    _select(chosen if chosen is not None else st.session_state["wedding_calendar_selected"])


def _move(offset, unit):
    key = "wedding_calendar_cursor" if unit == "month" else "wedding_calendar_selected"
    value = st.session_state[key]
    st.session_state[key] = shift_month(value, offset) if unit == "month" else shift_day(value, offset)
    if unit != "month":
        st.session_state["wedding_calendar_picker"] = st.session_state[key]


def _edit(ceremony_id, dates=False):
    if dates:
        st.session_state["page"] = "ceremony_planning"
    else:
        st.session_state["wedding_timeline_view"] = "Ceremony execution"
        st.session_state["wedding_timeline_focus"] = ceremony_id


def render_day(schedules, tasks, day, prefix):
    st.markdown(f"**{day.strftime('%A, %d %B %Y')}**")
    events = daily_schedules(schedules, day)
    if not events:
        st.caption("No saved ceremonies on this date.")
    for s in events:
        with st.container(border=True):
            st.markdown(f"**{s.ceremony_name}** · {s.date_certainty.capitalize()} date")
            st.write(clock_range(s.start_time, s.end_time))
            st.caption(f"Preferred timing: {s.preferred_timing or 'Not provided'} · Venue: {s.venue} · Guests: {s.guest_count if s.guest_count is not None else 'Not provided'}")
            st.write("Meal preference: " + (f"{s.meal} · accepted budget preference, booking unconfirmed"
                     if s.meal is not None else "Not provided; suggested activities do not confirm a meal."))
            if s.notes:
                st.write("Notes: " + s.notes)
            with st.container(horizontal=True):
                st.button("Edit execution plan", key=f"{prefix}_execution_{s.ceremony_id}", on_click=_edit, args=(s.ceremony_id,))
                st.button("Edit ceremony date/details", key=f"{prefix}_date_{s.ceremony_id}", on_click=_edit, args=(s.ceremony_id, True))
    agenda = activity_agenda(schedules, day)
    if agenda:
        st.markdown("**Activity agenda**")
    for a, s in agenda:
        st.write(f"{clock_range(a.start_time, a.end_time)} · {s.ceremony_name} · {a.order}. {a.title}")
        st.caption(f"{a.status.replace('_', ' ')} · {a.source.replace('_', ' ')} (reviewed and saved)"
                   + (f" · Suggested duration: {a.duration_minutes} min" if a.duration_minutes else ""))
        if a.description:
            st.write(a.description)
        if a.notes:
            st.write("Notes: " + a.notes)
    for t in tasks:
        if t.due_date == day:
            st.write(f"☐ {t.title} · {t.status.replace('_', ' ')} · {t.priority} priority")
            st.caption(f"Deadline: {t.due_source} · {t.source.replace('_', ' ')} · {t.relative_window}")
            if t.notes:
                st.write(t.notes)


def render_calendar(profile, ceremonies, timeline=None, budget=None):
    schedules, tasks = saved_calendar_data(profile, ceremonies, timeline, budget)
    first = next((s.ceremony_date for s in schedules if s.ceremony_date), local_today())
    st.session_state.setdefault("wedding_calendar_selected", first)
    st.session_state.setdefault("wedding_calendar_cursor", first.replace(day=1))
    st.caption("Saved ceremony dates and accepted timeline only. Tentative dates remain tentative. All clock times are local to the wedding.")
    view = st.segmented_control("Calendar view", ("Month", "Week", "Day"), default="Month",
                                key="wedding_calendar_view", selection_mode="single", required=True)
    st.date_input("Go to date", value=st.session_state["wedding_calendar_selected"],
                  min_value=date.min, max_value=date.max, key="wedding_calendar_picker", on_change=_pick_date)
    selected = st.session_state["wedding_calendar_selected"]
    if view == "Month":
        cursor = st.session_state["wedding_calendar_cursor"]
        left, middle, right = st.columns((1, 3, 1), vertical_alignment="center")
        left.button("Previous month", key="wedding_calendar_previous_month", on_click=_move, args=(-1, "month"), disabled=cursor == date.min)
        middle.subheader(cursor.strftime("%B %Y"))
        right.button("Next month", key="wedding_calendar_next_month", on_click=_move, args=(1, "month"), disabled=cursor.year == 9999 and cursor.month == 12)
        for col, label in zip(st.columns(7), ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")):
            col.markdown(f"**{label}**")
        for week in month_grid(cursor.year, cursor.month):
            for col, day in zip(st.columns(7), week):
                with col:
                    if day is None:
                        st.write("")
                        continue
                    st.button(str(day.day), key="wedding_calendar_day_" + day.isoformat(), width="stretch",
                              type="primary" if day == selected else "secondary", on_click=_select, args=(day,))
                    for s in daily_schedules(schedules, day):
                        color = COLORS[next(i for i, item in enumerate(schedules) if item.ceremony_id == s.ceremony_id) % len(COLORS)]
                        st.caption(f"{color} {s.ceremony_name}" + (" (tentative)" if s.date_certainty != "fixed" else ""))
                    count = sum(t.due_date == day for t in tasks)
                    if count:
                        st.caption(f"☑ {count} preparation task(s)")
        st.divider()
        render_day(schedules, tasks, selected, "wedding_calendar_selected_day")
    else:
        unit, step = ("week", 7) if view == "Week" else ("day", 1)
        with st.container(horizontal=True):
            st.button(f"Previous {unit}", key="wedding_calendar_previous_" + unit, on_click=_move, args=(-step, "day"), disabled=selected == date.min)
            st.button(f"Next {unit}", key="wedding_calendar_next_" + unit, on_click=_move, args=(step, "day"), disabled=selected == date.max)
        for day in week_dates(selected) if view == "Week" else (selected,):
            render_day(schedules, tasks, day, "wedding_calendar_agenda_" + day.isoformat())
            st.divider()
    st.subheader("Unscheduled, tentative & incomplete")
    if profile.wedding_date_status and profile.wedding_date_status.value == "approximate_date":
        st.caption(f"Overall wedding window: {profile.wedding_date or 'Approximate'}; no arbitrary calendar date has been assigned.")
    incomplete = [s for s in schedules if s.ceremony_date is None or s.date_certainty != "fixed" or s.start_time is None or s.end_time is None
                  or any(a.start_time is None or a.end_time is None for a in s.activities)]
    for s in incomplete:
        st.write(f"{s.ceremony_name} · {s.ceremony_date.isoformat() if s.ceremony_date else 'Date not provided'} · {s.date_certainty} · {clock_range(s.start_time, s.end_time)}")
        missing = sum(a.start_time is None or a.end_time is None for a in s.activities)
        if missing:
            st.caption(f"{missing} activities have flexible or incomplete clock times.")
        with st.container(horizontal=True):
            st.button("Set ceremony date/details", key="wedding_calendar_missing_" + s.ceremony_id, on_click=_edit, args=(s.ceremony_id, True))
            st.button("Set execution times", key="wedding_calendar_times_" + s.ceremony_id, on_click=_edit, args=(s.ceremony_id,))
    for t in tasks:
        if t.due_date is None:
            st.write(f"{t.title} · {t.phase} · {t.relative_window} · {t.status.replace('_', ' ')}")
    if any(t.due_date is None for t in tasks):
        st.button("Edit preparation tasks", key="wedding_calendar_edit_tasks",
                  on_click=lambda: st.session_state.update({"wedding_timeline_view": "Preparation timeline"}))
    if not incomplete and not any(t.due_date is None for t in tasks):
        st.caption("No unscheduled or incomplete entries.")
