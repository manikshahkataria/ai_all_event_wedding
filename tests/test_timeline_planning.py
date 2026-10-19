from dataclasses import replace
from datetime import date, time
from unittest.mock import Mock, patch

import pytest
from streamlit.testing.v1 import AppTest

from ai_event_planning_platform.auth import AuthSessionTokens, store_auth_session
from ai_event_planning_platform.timeline_planning import EDITOR
from ai_event_planning_platform.wedding_project_state import TIMELINE_DRAFT_KEY, TIMELINE_SAVED_KEY, PROJECT_DIRTY_KEY
from ai_event_planning_platform.wedding_projects import WeddingProjectRepository
from ai_event_planning_platform.wedding_timeline import fallback_timeline
from test_budget_engine import ceremony, profile
from test_wedding_projects import FakeClient, OWNER_ONE
from test_wedding_timeline import DAY, dated_ceremony


@pytest.fixture
def environment(monkeypatch):
    repo = WeddingProjectRepository(FakeClient(), OWNER_ONE)
    monkeypatch.setattr("ai_event_planning_platform.home.restore_authenticated_session", lambda a, r: AuthSessionTokens(a, r))
    monkeypatch.setattr("ai_event_planning_platform.home.project_repository_from_state", lambda s: repo)
    monkeypatch.setattr("ai_event_planning_platform.wedding_project_state.project_repository_from_state", lambda s: repo)
    ai = Mock(side_effect=fallback_timeline)
    monkeypatch.setattr("ai_event_planning_platform.timeline_intelligence.interpret_timeline", ai)

    def open_project(project_id):
        app = AppTest.from_file("../app.py", default_timeout=10)
        store_auth_session(app.session_state, AuthSessionTokens("test-access", "test-refresh"))
        app.session_state["page"] = "wedding_projects"
        app.run()
        app.button(key=f"continue_wedding_project_{project_id}").click().run()
        app.button(key="wedding_dashboard_calendar").click().run()
        assert not app.exception
        return app

    def create(ceremonies=None):
        row = repo.create_wedding_project("Timeline wedding", profile(), ceremonies or [dated_ceremony()], planning_step="wedding_timeline")
        return row, open_project(row["project_id"])

    return repo, create, open_project, ai


def workspace(app, name):
    app.segmented_control(key="wedding_timeline_view").set_value(name).run()
    assert not app.exception
    return app


def generate(app):
    app.button(key="wedding_timeline_generate").click().run()
    assert not app.exception
    return app


def save(app):
    app.checkbox(key="wedding_timeline_accept").check().run()
    app.button(key="wedding_timeline_save").click().run()
    assert not app.exception
    return app


def test_route_requires_authentication():
    app = AppTest.from_file("../app.py")
    app.session_state["page"] = "wedding_timeline"
    app.run()
    assert app.title[0].value == "Log in" and not app.exception


def test_initial_calendar_no_api_and_month_week_day_controls_no_database(environment):
    repo, create, _open, ai = environment
    _row, app = create([dated_ceremony(), dated_ceremony("mehendi"), ceremony("reception")])
    assert app.title[0].value == "Wedding Calendar & Timeline"
    assert app.button(key="wedding_calendar_day_2027-11-12")
    with patch.object(repo, "load_selected_project", wraps=repo.load_selected_project) as load:
        app.button(key="wedding_calendar_next_month").click().run()
        assert app.session_state["wedding_calendar_cursor"] == date(2027, 12, 1)
        app.button(key="wedding_calendar_previous_month").click().run()
        app.button(key="wedding_calendar_day_2027-11-14").click().run()
        assert app.session_state["wedding_calendar_selected"] == date(2027, 11, 14)
        app.segmented_control(key="wedding_calendar_view").set_value("Week").run()
        app.button(key="wedding_calendar_next_week").click().run()
        assert app.session_state["wedding_calendar_selected"] == date(2027, 11, 21)
        app.button(key="wedding_calendar_previous_week").click().run()
        app.segmented_control(key="wedding_calendar_view").set_value("Day").run()
        app.button(key="wedding_calendar_next_day").click().run()
        assert app.session_state["wedding_calendar_selected"] == date(2027, 11, 15)
        app.button(key="wedding_calendar_previous_day").click().run()
        app.date_input(key="wedding_calendar_picker").set_value(DAY).run()
        assert app.session_state["wedding_calendar_selected"] == DAY
        load.assert_not_called()
    ai.assert_not_called()
    assert not app.exception


def test_task_edit_completion_dependencies_and_save_restore(environment):
    repo, create, reopen, ai = environment
    row, app = create()
    generate(app)
    t = app.session_state[TIMELINE_DRAFT_KEY].tasks[0]
    app.text_input(key=f"{EDITOR}{t.task_id}_title").set_value("Review family plan").run()
    app.selectbox(key=f"{EDITOR}{t.task_id}_status").set_value("completed").run()
    app.date_input(key=f"{EDITOR}{t.task_id}_due_date").set_value(date(2027, 10, 1)).run()
    app.text_area(key=f"{EDITOR}{t.task_id}_notes").set_value("Reviewed together").run()
    changed = app.session_state[TIMELINE_DRAFT_KEY].tasks[0]
    assert changed.title == "Review family plan" and changed.status == "completed" and changed.due_source == "user"
    assert "wedding_timeline" not in repo.load_selected_project(row["project_id"])["wedding_profile"]
    save(app)
    assert any("saved and read back" in s.value for s in app.success)
    assert reopen(row["project_id"]).session_state[TIMELINE_SAVED_KEY].tasks[0] == changed
    assert ai.call_count == 1


def test_activity_crud_reorder_times_status_and_draft_survives_view_changes(environment):
    _repo, create, _open, ai = environment
    _row, app = create()
    workspace(app, "Ceremony execution")
    app.button(key="wedding_timeline_add_activity_haldi").click().run()
    aid = app.session_state[TIMELINE_DRAFT_KEY].schedules[0].activities[0].activity_id
    app.text_input(key=f"{EDITOR}{aid}_title").set_value("Sound check").run()
    app.time_input(key=f"{EDITOR}{aid}_start_time").set_value(time(9)).run()
    app.time_input(key=f"{EDITOR}{aid}_end_time").set_value(time(9, 30)).run()
    app.selectbox(key=f"{EDITOR}{aid}_status").set_value("completed").run()
    app.text_area(key=f"{EDITOR}{aid}_notes").set_value("Equipment checked").run()
    app.button(key="wedding_timeline_add_activity_haldi").click().run()
    other = app.session_state[TIMELINE_DRAFT_KEY].schedules[0].activities[1].activity_id
    app.button(key="wedding_timeline_up_" + other).click().run()
    assert app.session_state[TIMELINE_DRAFT_KEY].schedules[0].activities[1].activity_id == aid
    workspace(app, "Calendar")
    assert not any("Sound check" in m.value for m in app.markdown)
    workspace(app, "Ceremony execution")
    assert app.text_input(key=f"{EDITOR}{aid}_title").value == "Sound check"
    app.button(key="wedding_timeline_delete_activity_" + other).click().run()
    assert len(app.session_state[TIMELINE_DRAFT_KEY].schedules[0].activities) == 1
    save(app)
    workspace(app, "Calendar")
    assert any("Sound check" in m.value for m in app.markdown)
    ai.assert_not_called()
    assert not app.exception


def test_task_add_delete_and_circular_dependency_blocks_save(environment):
    _repo, create, _open, _ai = environment
    _row, app = create()
    workspace(app, "Preparation timeline")
    app.button(key="wedding_timeline_add_task").click().run()
    t = app.session_state[TIMELINE_DRAFT_KEY].tasks[0]
    app.multiselect(key=f"{EDITOR}{t.task_id}_dependency_ids").set_value([t.task_id]).run()
    app.checkbox(key="wedding_timeline_accept").check().run()
    assert app.button(key="wedding_timeline_save").disabled
    assert any("cycle" in e.value for e in app.error)
    app.button(key="wedding_timeline_delete_task_" + t.task_id).click().run()
    assert not app.session_state[TIMELINE_DRAFT_KEY].tasks
    assert not app.exception


def test_failed_save_retains_draft_blocks_project_switch_and_can_retry(environment):
    repo, create, _open, ai = environment
    row, app = create()
    second = repo.create_wedding_project("Other", profile(), [ceremony()])
    generate(app)
    draft = app.session_state[TIMELINE_DRAFT_KEY]
    with patch.object(repo, "save_timeline_plan", side_effect=RuntimeError("readback failed")):
        save(app)
    assert not app.success and app.session_state[PROJECT_DIRTY_KEY]
    assert app.session_state[TIMELINE_DRAFT_KEY] == draft
    app.button(key="open_wedding_projects").click().run()
    assert app.button(key=f"continue_wedding_project_{second['project_id']}").disabled
    app.button(key=f"continue_wedding_project_{row['project_id']}").click().run()
    save(app)
    assert PROJECT_DIRTY_KEY not in app.session_state
    assert ai.call_count == 1


def test_logout_login_restoration_and_project_isolation(environment):
    repo, create, reopen, _ai = environment
    row, app = create()
    generate(app)
    save(app)
    plan = app.session_state[TIMELINE_SAVED_KEY]
    with (patch("ai_event_planning_platform.home.logout_user", return_value=True),
          patch("ai_event_planning_platform.home.login_user", return_value=AuthSessionTokens("a", "r"))):
        app.button(key="logout").click().run()
        assert TIMELINE_SAVED_KEY not in app.session_state
        app.text_input(key="login_email").set_value("planner@example.invalid")
        app.text_input(key="login_password").set_value("not-stored")
        app.button(key="login_submit").click().run()
        app.button(key=f"continue_wedding_project_{row['project_id']}").click().run()
    assert app.session_state[TIMELINE_SAVED_KEY] == plan
    second = repo.create_wedding_project("Second", profile(), [ceremony("reception")], planning_step="wedding_timeline")
    app.button(key="open_wedding_projects").click().run()
    app.button(key=f"continue_wedding_project_{second['project_id']}").click().run()
    app.button(key="wedding_dashboard_calendar").click().run()
    assert TIMELINE_SAVED_KEY not in app.session_state
    assert not app.session_state[TIMELINE_DRAFT_KEY].tasks
    assert reopen(row["project_id"]).session_state[TIMELINE_SAVED_KEY] == plan


def test_overview_actions_and_saved_counts(environment):
    _repo, create, _open, _ai = environment
    _row, app = create([dated_ceremony(), ceremony("reception")])
    app.session_state["page"] = "step_by_step"
    app.run()
    # The global breadcrumb is the established route back to the overview.
    if not any(b.key == "wedding_timeline_open" for b in app.button):
        app.session_state["page"] = "step_by_step"
        app.run()
    assert app.button(key="wedding_budget_open")
    assert app.button(key="wedding_timeline_open")
    assert [(m.label, m.value) for m in app.metric if m.label in {"Scheduled ceremonies", "Without dates"}] == [("Scheduled ceremonies", "1"), ("Without dates", "1")]
    app.button(key="wedding_timeline_open").click().run()
    assert app.title[0].value == "Wedding Calendar & Timeline" and not app.exception


def test_concurrent_save_comparison_preserves_local_draft(environment):
    repo, create, _open, _ai = environment
    row, app = create()
    generate(app)
    local = app.session_state[TIMELINE_DRAFT_KEY]
    other = replace(local, accepted=True, tasks=(replace(local.tasks[0], title="Other session work"), *local.tasks[1:]))
    repo.save_timeline_plan(row["project_id"], other)
    save(app)
    assert app.error and not app.success
    app.button(key="wedding_timeline_reload").click().run()
    assert not app.exception
    assert app.session_state[TIMELINE_DRAFT_KEY] == local
    assert app.session_state[TIMELINE_SAVED_KEY] == other
    assert not app.checkbox(key="wedding_timeline_accept").value
    save(app)
    assert app.session_state[TIMELINE_SAVED_KEY] == replace(local, accepted=True)


def test_ai_failure_cached_reruns_and_explicit_retry(environment):
    from ai_event_planning_platform.timeline_intelligence import TimelineIntelligenceError
    _repo, create, _open, ai = environment
    ai.side_effect = TimelineIntelligenceError("Groq unavailable; rule-based suggestions shown.")
    _row, app = create()
    generate(app)
    assert any("Groq unavailable" in i.value for i in app.info)
    assert app.button(key="wedding_timeline_generate").disabled
    app.checkbox(key="wedding_timeline_replace").check().run()
    generate(app)
    workspace(app, "Calendar")
    app.button(key="wedding_calendar_next_month").click().run()
    assert ai.call_count == 1
    app.checkbox(key="wedding_timeline_retry").check().run()
    generate(app)
    assert ai.call_count == 2


def test_updated_saved_ceremony_requires_refresh_and_preserves_activity_edits(environment):
    repo, create, _open, _ai = environment
    row, app = create()
    generate(app)
    before = app.session_state[TIMELINE_DRAFT_KEY].schedules[0].activities
    repo.save_ceremony_plan(row["project_id"], replace(dated_ceremony().plan, ceremony_date=date(2027, 11, 15)))
    app.button(key="wedding_timeline_reload").click().run()
    assert app.button(key="wedding_timeline_save").disabled
    app.button(key="wedding_timeline_refresh").click().run()
    plan = app.session_state[TIMELINE_DRAFT_KEY]
    assert plan.schedules[0].ceremony_date == date(2027, 11, 15)
    assert plan.schedules[0].activities == before
    save(app)
    workspace(app, "Calendar")
    app.date_input(key="wedding_calendar_picker").set_value(date(2027, 11, 15)).run()
    assert any("Haldi" in m.value for m in app.markdown)
    assert not app.exception
