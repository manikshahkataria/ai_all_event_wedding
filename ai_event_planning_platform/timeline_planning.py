"""Combined saved calendar, preparation checklist and ceremony execution editor."""

from dataclasses import replace
from datetime import date
from uuid import uuid4

import streamlit as st

from ai_event_planning_platform.timeline_intelligence import cached_timeline
from ai_event_planning_platform.wedding_calendar import render_calendar, saved_calendar_data
from ai_event_planning_platform.wedding_project_state import (
    BUDGET_SAVED_KEY, PROFILE_SAVED_KEY, TIMELINE_DRAFT_KEY, TIMELINE_SAVED_KEY,
    TIMELINE_ERROR_KEY, persist_timeline_plan, reload_timeline_context, saved_project_ceremonies,
)
from ai_event_planning_platform.wedding_timeline import (
    PHASES, STATUSES, KINDS, PreparationTask, add_activity, calculate_deadline,
    empty_timeline, fallback_timeline, move_activity, planning_fingerprint,
    rebase_timeline, schedule_issues,
)

TIMELINE_PAGE = "wedding_timeline"
EDITOR = "wedding_timeline_editor_"
ACCEPT = "wedding_timeline_accept"


def _store(plan):
    st.session_state[TIMELINE_DRAFT_KEY] = replace(plan, accepted=False)
    st.session_state[ACCEPT] = False
    st.session_state.pop("wedding_timeline_save_notice", None)


def _clear_widgets():
    for key in tuple(st.session_state):
        if key.startswith(EDITOR):
            del st.session_state[key]


def _change(kind, identity, field, key):
    plan = st.session_state[TIMELINE_DRAFT_KEY]
    value = st.session_state[key]
    if field == "dependency_ids":
        value = tuple(value)
    if kind == "task":
        tasks = []
        for t in plan.tasks:
            if t.task_id == identity:
                t = replace(t, **{field: value})
                if field == "due_date":
                    t = replace(t, due_source="user")
                elif field in {"ceremony_id", "days_before"} and t.due_source != "user":
                    t = calculate_deadline(t, st.session_state[PROFILE_SAVED_KEY], saved_project_ceremonies(st.session_state))
                    st.session_state[f"{EDITOR}{t.task_id}_due_date"] = t.due_date
            tasks.append(t)
        _store(replace(plan, tasks=tuple(tasks)))
    else:
        schedules = []
        for s in plan.schedules:
            if kind == "schedule" and s.ceremony_id == identity:
                s = replace(s, **{field: value})
            if kind == "activity":
                s = replace(s, activities=tuple(replace(a, **{field: value}) if a.activity_id == identity else a for a in s.activities))
            schedules.append(s)
        _store(replace(plan, schedules=tuple(schedules)))


def _widget(widget, label, kind, item, field, **kwargs):
    identity = item.task_id if kind == "task" else item.activity_id if kind == "activity" else item.ceremony_id
    key = f"{EDITOR}{identity}_{field}"
    value = getattr(item, field)
    if widget in (st.selectbox,):
        kwargs["index"] = list(kwargs["options"]).index(value)
    elif widget is st.multiselect:
        kwargs["default"] = list(value)
    else:
        kwargs["value"] = value
    return widget(label, key=key, on_change=_change, args=(kind, identity, field, key), **kwargs)


def _add_task():
    plan = st.session_state[TIMELINE_DRAFT_KEY]
    _store(replace(plan, tasks=(*plan.tasks, PreparationTask(str(uuid4()), "New preparation task"))))


def _delete_task(task_id):
    plan = st.session_state[TIMELINE_DRAFT_KEY]
    # Keep dependent links visible as errors, so removing work never silently changes dependencies.
    _store(replace(plan, tasks=tuple(t for t in plan.tasks if t.task_id != task_id)))


def _activity_action(cid, aid=None, action="add"):
    plan = st.session_state[TIMELINE_DRAFT_KEY]
    if action == "add":
        plan = add_activity(plan, cid)
    elif action in {"up", "down"}:
        plan = move_activity(plan, cid, aid, -1 if action == "up" else 1)
    else:
        plan = replace(plan, schedules=tuple(replace(s, activities=tuple(
            replace(a, order=i + 1) for i, a in enumerate(a for a in sorted(s.activities, key=lambda a: a.order) if a.activity_id != aid)))
            if s.ceremony_id == cid else s for s in plan.schedules))
    _store(plan)


def render_tasks(plan):
    st.subheader("Wedding Preparation Timeline")
    st.caption("Suggested deadlines require review. User-entered deadlines are preserved; relative windows remain available without fixed dates.")
    st.button("Add preparation task", key="wedding_timeline_add_task", on_click=_add_task)
    names = {s.ceremony_id: s.ceremony_name for s in plan.schedules}
    task_names = {t.task_id: t.title for t in plan.tasks}
    for phase in PHASES:
        rows = [t for t in plan.tasks if t.phase == phase]
        with st.expander(f"{phase} · {len(rows)} tasks", expanded=True):
            for t in rows:
                with st.container(border=True):
                    st.caption(t.source.replace("_", " ") + " · " + t.status.replace("_", " "))
                    _widget(st.text_input, "Task title", "task", t, "title", max_chars=120)
                    _widget(st.text_area, "Description", "task", t, "description", max_chars=2000)
                    left, right = st.columns(2)
                    with left:
                        _widget(st.selectbox, "Preparation phase", "task", t, "phase", options=PHASES)
                        _widget(st.selectbox, "Related ceremony", "task", t, "ceremony_id", options=[None, *names], format_func=lambda v: names.get(v, "General wedding preparation"))
                        _widget(st.selectbox, "Task status", "task", t, "status", options=STATUSES, format_func=lambda v: v.replace("_", " ").capitalize())
                    with right:
                        _widget(st.date_input, "Deadline (optional)", "task", t, "due_date", min_value=date.min, max_value=date.max)
                        st.caption("Deadline source: " + t.due_source)
                        _widget(st.selectbox, "Priority", "task", t, "priority", options=("low", "normal", "high"))
                        _widget(st.number_input, "Suggested days before fixed date (optional)", "task", t, "days_before", min_value=0, max_value=3650, step=1)
                    _widget(st.text_input, "Relative preparation window", "task", t, "relative_window", max_chars=300)
                    options = list(dict.fromkeys([*task_names, *t.dependency_ids]))
                    _widget(st.multiselect, "Prerequisites", "task", t, "dependency_ids", options=options,
                            format_func=lambda v: task_names.get(v, "Missing prerequisite: " + v))
                    _widget(st.text_area, "Task notes", "task", t, "notes", max_chars=2000)
                    st.button("Delete task", key="wedding_timeline_delete_task_" + t.task_id, on_click=_delete_task, args=(t.task_id,))
            if not rows:
                st.caption("No tasks in this phase.")


def render_execution(plan):
    st.subheader("Ceremony Execution Plans")
    st.caption("Saved ceremony dates and venue details come from ceremony planning. Activities below are editable drafts until saved.")
    focus = st.session_state.get("wedding_timeline_focus")
    for s in plan.schedules:
        with st.expander(s.ceremony_name, expanded=focus in (None, s.ceremony_id)):
            st.write(f"Date: {s.ceremony_date or 'Not provided'} · {s.date_certainty} · Preferred timing: {s.preferred_timing or 'Not provided'}")
            st.caption(f"Venue: {s.venue} · Guests: {s.guest_count if s.guest_count is not None else 'Not provided'}")
            st.button("Edit saved ceremony details", key="wedding_timeline_details_" + s.ceremony_id,
                      on_click=lambda: st.session_state.update({"page": "ceremony_planning"}))
            left, right = st.columns(2)
            with left:
                _widget(st.time_input, "Ceremony start (optional)", "schedule", s, "start_time", step=60)
            with right:
                _widget(st.time_input, "Ceremony end (optional)", "schedule", s, "end_time", step=60)
            _widget(st.text_area, "Schedule notes", "schedule", s, "notes", max_chars=2000)
            st.button("Add activity", key="wedding_timeline_add_activity_" + s.ceremony_id, on_click=_activity_action, args=(s.ceremony_id,))
            for a in sorted(s.activities, key=lambda a: a.order):
                with st.container(border=True):
                    st.caption(f"{a.order}. {a.source.replace('_', ' ')}")
                    _widget(st.text_input, "Activity title", "activity", a, "title", max_chars=120)
                    _widget(st.text_area, "Activity description", "activity", a, "description", max_chars=2000)
                    columns = st.columns(3)
                    with columns[0]:
                        _widget(st.time_input, "Activity start (optional)", "activity", a, "start_time", step=60)
                        _widget(st.selectbox, "Activity type", "activity", a, "kind", options=KINDS)
                    with columns[1]:
                        _widget(st.time_input, "Activity end (optional)", "activity", a, "end_time", step=60)
                        _widget(st.selectbox, "Activity status", "activity", a, "status", options=STATUSES, format_func=lambda v: v.replace("_", " ").capitalize())
                    with columns[2]:
                        _widget(st.number_input, "Suggested duration in minutes (optional)", "activity", a, "duration_minutes", min_value=1, max_value=1440, step=1)
                    _widget(st.text_area, "Activity notes", "activity", a, "notes", max_chars=2000)
                    with st.container(horizontal=True):
                        st.button("Move up", key="wedding_timeline_up_" + a.activity_id, disabled=a.order == 1,
                                  on_click=_activity_action, args=(s.ceremony_id, a.activity_id, "up"))
                        st.button("Move down", key="wedding_timeline_down_" + a.activity_id, disabled=a.order == len(s.activities),
                                  on_click=_activity_action, args=(s.ceremony_id, a.activity_id, "down"))
                        st.button("Delete activity", key="wedding_timeline_delete_activity_" + a.activity_id,
                                  on_click=_activity_action, args=(s.ceremony_id, a.activity_id, "delete"))


def render_timeline_summary(state, profile, ceremonies):
    schedules, tasks = saved_calendar_data(profile, ceremonies, state.get(TIMELINE_SAVED_KEY), state.get(BUDGET_SAVED_KEY))
    scheduled = sum(s.ceremony_date is not None for s in schedules)
    columns = st.columns(4)
    for col, title, value in zip(columns, ("Scheduled ceremonies", "Without dates", "Pending tasks", "Scheduling issues"),
            (scheduled, len(schedules) - scheduled, sum(t.status != "completed" for t in tasks),
             sum(i.severity in {"warning", "error"} for i in schedule_issues(replace(empty_timeline(profile, ceremonies), schedules=schedules, tasks=tasks))))):
        col.metric(title, value)


def render_wedding_timeline():
    st.title("Wedding Calendar & Timeline")
    profile = st.session_state.get(PROFILE_SAVED_KEY)
    if profile is None:
        st.info("Save wedding requirements before planning your timeline.")
        return
    try:
        ceremonies = saved_project_ceremonies(st.session_state)
    except ValueError:
        st.info("Saved ceremony details are unavailable. Reopen the project before planning its timeline.")
        return
    budget = st.session_state.get(BUDGET_SAVED_KEY)
    from ai_event_planning_platform.wedding_dashboard import render_planning_status
    render_planning_status(profile, ceremonies, budget, st.session_state.get(TIMELINE_SAVED_KEY),
                           prefix="wedding_calendar_status", calendar_only=True)
    if not ceremonies:
        st.info("Confirm ceremonies before planning your timeline.")
        return
    if st.session_state.get(TIMELINE_ERROR_KEY):
        st.warning(st.session_state[TIMELINE_ERROR_KEY])
    st.session_state.setdefault(TIMELINE_DRAFT_KEY, empty_timeline(profile, ceremonies, budget))
    draft = st.session_state[TIMELINE_DRAFT_KEY]
    saved = st.session_state.get(TIMELINE_SAVED_KEY)
    if saved and saved.context_fingerprint != planning_fingerprint(profile, ceremonies, budget):
        st.info("The saved timeline predates changes to wedding details. Calendar ceremony details follow current saved plans; task deadlines and execution times remain as previously saved until you refresh, review and save the draft.")
    st.caption("Prepare your wedding, review ceremony activities and explore saved schedules in one workspace.")
    has_content = bool(draft.tasks or any(s.activities or s.start_time or s.end_time or s.notes for s in draft.schedules))
    with st.expander("Generate planning suggestions", expanded=not has_content):
        use_ai = st.checkbox("Use Groq for contextual suggestions", value=True, key="wedding_timeline_use_ai")
        replace_draft = st.checkbox("Replace my current timeline draft with generated suggestions", key="wedding_timeline_replace") if has_content else True
        retry = st.checkbox("Retry Groq for this context", key="wedding_timeline_retry", help="Otherwise the previous result, including a fallback, is reused.") if use_ai else False
        if st.button("Generate timeline & activities", key="wedding_timeline_generate", disabled=not replace_draft):
            if use_ai:
                with st.spinner("Preparing planning suggestions…"):
                    draft, notice = cached_timeline(st.session_state, profile, ceremonies, budget, retry=retry)
            else:
                draft, notice = fallback_timeline(profile, ceremonies, budget), "Rule-based suggestions generated for review."
            _clear_widgets()
            _store(draft)
            st.session_state["wedding_timeline_generation_notice"] = notice or "AI suggestions generated for review; no dates, clock times, rituals or bookings were confirmed."
            st.session_state["wedding_timeline_view"] = "Preparation timeline"
            st.rerun()
    if st.session_state.get("wedding_timeline_generation_notice"):
        st.info(st.session_state["wedding_timeline_generation_notice"])
    if st.button("Load latest saved details for comparison", key="wedding_timeline_reload",
                 help="Reads current project data and keeps your timeline draft. Review the saved calendar and your draft before replacing concurrent edits."):
        try:
            reload_timeline_context(st.session_state)
        except Exception:
            st.error("Current saved details could not be loaded. Your draft is retained. Save other pending project changes first, then retry.")
        else:
            st.session_state[ACCEPT] = False
            st.session_state["wedding_timeline_generation_notice"] = "Latest saved details loaded for comparison. Your timeline draft is retained. Review the saved calendar and your draft before saving; saving replaces the current saved timeline."
            st.rerun()
    stale = draft.context_fingerprint != planning_fingerprint(profile, ceremonies, budget)
    if stale:
        st.warning("Saved wedding details changed. Refresh the draft to review current ceremonies, dates and budget. Retained activities and user deadlines are preserved; removed ceremonies leave the draft only when refreshed.")
        if st.button("Refresh draft from saved details", key="wedding_timeline_refresh"):
            _clear_widgets()
            _store(rebase_timeline(draft, profile, ceremonies, budget))
            st.rerun()
    view = st.segmented_control("Workspace", ("Calendar", "Preparation timeline", "Ceremony execution"), default="Calendar",
                                required=True, key="wedding_timeline_view")
    if view == "Calendar":
        render_calendar(profile, ceremonies, saved, budget)
    elif view == "Preparation timeline":
        render_tasks(draft)
    else:
        render_execution(draft)
    st.divider()
    st.subheader("Review & save timeline")
    if saved == draft and draft.accepted:
        st.caption("Showing the saved timeline. Changes require a new review and save.")
    else:
        st.caption("Draft changes are kept in this session. Save to include them in the calendar and restore them after login.")
    try:
        issues = schedule_issues(draft)
        errors = any(i.severity == "error" for i in issues)
    except ValueError as error:
        st.error(str(error) + " Check titles, dates and other required fields.")
        issues, errors = (), True
    with st.expander(f"Schedule review · {len(issues)} checks", expanded=errors):
        for i in issues:
            renderer = st.error if i.severity == "error" else st.warning if i.severity == "warning" else st.info
            renderer(i.message + " " + i.resolution)
        if not issues and not errors:
            st.caption("No scheduling issues found in the available information.")
    accepted = st.checkbox("I reviewed these activities, tasks and scheduling warnings", key=ACCEPT)
    if st.button("Save timeline changes", key="wedding_timeline_save", type="primary", disabled=not accepted or errors or stale):
        try:
            persist_timeline_plan(st.session_state, replace(draft, accepted=True))
        except Exception:
            st.error("The timeline could not be saved and verified. Your draft is retained. Save pending wedding details first, or reload current project details and retry after reviewing concurrent changes.")
        else:
            st.session_state["wedding_timeline_save_notice"] = "Timeline saved and read back from Supabase."
            st.rerun()
    if st.session_state.get("wedding_timeline_save_notice"):
        st.success(st.session_state["wedding_timeline_save_notice"])
