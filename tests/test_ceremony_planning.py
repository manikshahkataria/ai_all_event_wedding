from dataclasses import replace
from datetime import date
from unittest.mock import patch
from uuid import uuid4

import pytest
from streamlit.testing.v1 import AppTest

from ai_event_planning_platform.auth import ACCESS_TOKEN_KEY, AuthSessionTokens, store_auth_session
from ai_event_planning_platform.ceremony_plan import CeremonyDateCertainty, CeremonyPlan
from ai_event_planning_platform.ceremony_planning import CEREMONY_PLANNING_PAGE
from ai_event_planning_platform.wedding_ceremonies import CEREMONY_BY_ID, CEREMONY_CHECKBOX_PREFIX, Ceremony
from ai_event_planning_platform.wedding_profile import WeddingDateStatus, WeddingProfile
from ai_event_planning_platform.wedding_project_state import ACTIVE_PROJECT_ID_KEY, PROJECT_DIRTY_KEY
from ai_event_planning_platform.wedding_projects import WeddingProjectRepository, ceremonies_from_json
from test_wedding_projects import FakeClient, FakeQuery, OWNER_ONE


TOKENS = AuthSessionTokens("test-access", "test-refresh")


def selected(ceremony_id):
    return replace(CEREMONY_BY_ID[ceremony_id], selected=True)


def key(project, ceremony_id, field):
    return f"wedding_plan_{project['project_id']}_{ceremony_id}_{field}"


def open_plans_from_dashboard(app):
    app.button(key="wedding_dashboard_details").click().run()
    button = app.button(key="edit_ceremony_plans")
    if button.disabled:
        # Exercise the protected empty-editor URL; the UI correctly asks users
        # without ceremonies to choose them before opening an editor.
        app._page_hash = next(k for k, info in app._registered_pages.items()
                              if info.get("url_pathname") == CEREMONY_PLANNING_PAGE)
        app.run()
    else:
        button.click().run()
    assert not app.exception


@pytest.fixture
def environment(monkeypatch):
    client = FakeClient()
    repository = WeddingProjectRepository(client, OWNER_ONE)
    monkeypatch.setattr("ai_event_planning_platform.home.restore_authenticated_session", lambda a, r: AuthSessionTokens(a, r))
    monkeypatch.setattr("ai_event_planning_platform.home.project_repository_from_state", lambda state: repository)
    monkeypatch.setattr("ai_event_planning_platform.wedding_project_state.project_repository_from_state", lambda state: repository)

    def reopen(project_id):
        app = AppTest.from_file("../app.py")
        store_auth_session(app.session_state, TOKENS)
        app.session_state["page"] = "wedding_projects"
        app.run()
        app.button(key=f"continue_wedding_project_{project_id}").click().run()
        open_plans_from_dashboard(app)
        return app

    def open_app(ceremonies=None, profile=None):
        project = repository.create_wedding_project(
            "Our wedding", profile or WeddingProfile(guest_count=100),
            ceremonies if ceremonies is not None else [selected("reception"), selected("haldi")],
            planning_step=CEREMONY_PLANNING_PAGE,
        )
        return project, reopen(project["project_id"])

    return repository, client, open_app, reopen


def saved_plan(repository, project, ceremony_id):
    row = repository.load_selected_project(project["project_id"])
    return next(item.plan for item in ceremonies_from_json(row["selected_ceremonies"]) if item.ceremony_id == ceremony_id)


def test_new_editors_have_no_fake_values_and_support_independent_saves(environment):
    repository, _client, open_app, _reopen = environment
    project, app = open_app()
    assert app.title[0].value == "Plan Your Wedding Ceremonies"
    for ceremony_id in ("reception", "haldi"):
        assert app.number_input(key=key(project, ceremony_id, "guest_count")).value is None
        assert app.number_input(key=key(project, ceremony_id, "estimated_budget_inr")).value is None
        assert app.date_input(key=key(project, ceremony_id, "ceremony_date")).value is None
        assert app.selectbox(key=key(project, ceremony_id, "preferred_timing")).value == "Not specified"
    app.number_input(key=key(project, "reception", "guest_count")).set_value(80).run()
    assert any("Unsaved changes" in item.value for item in app.caption)
    app.number_input(key=key(project, "haldi", "guest_count")).set_value(40).run()
    app.button(key=key(project, "reception", "save")).click().run()
    assert saved_plan(repository, project, "reception").guest_count == 80
    assert saved_plan(repository, project, "haldi") is None
    assert app.number_input(key=key(project, "haldi", "guest_count")).value == 40
    assert any("Saved · Incomplete" in item.value for item in app.caption)
    app.button(key=key(project, "haldi", "save")).click().run()
    assert saved_plan(repository, project, "haldi").guest_count == 40
    assert saved_plan(repository, project, "reception").guest_count == 80
    assert not app.exception


def test_partial_plan_save_edit_and_fresh_session_restore(environment):
    repository, _client, open_app, reopen = environment
    project, app = open_app()
    app.button(key=key(project, "reception", "save")).click().run()
    assert app.success
    assert saved_plan(repository, project, "reception").guest_count is None
    app.number_input(key=key(project, "reception", "guest_count")).set_value(0).run()
    app.number_input(key=key(project, "reception", "estimated_budget_inr")).set_value(60000.5).run()
    app.selectbox(key=key(project, "reception", "date_certainty")).set_value("Tentative").run()
    app.date_input(key=key(project, "reception", "ceremony_date")).set_value(date(2027, 10, 23)).run()
    app.selectbox(key=key(project, "reception", "preferred_timing")).set_value("Evening").run()
    app.selectbox(key=key(project, "reception", "venue_preference")).set_value("Home").run()
    app.text_input(key=key(project, "reception", "venue_notes")).set_value("Family garden").run()
    app.button(key=key(project, "reception", "save")).click().run()
    reopened = reopen(project["project_id"])
    assert reopened.number_input(key=key(project, "reception", "guest_count")).value == 0
    assert reopened.number_input(key=key(project, "reception", "estimated_budget_inr")).value == 60000.5
    assert reopened.selectbox(key=key(project, "reception", "date_certainty")).value == "Tentative"
    assert reopened.date_input(key=key(project, "reception", "ceremony_date")).value == date(2027, 10, 23)
    assert reopened.text_input(key=key(project, "reception", "venue_notes")).value == "Family garden"
    reopened.selectbox(key=key(project, "reception", "date_certainty")).set_value("Fixed").run()
    reopened.button(key=key(project, "reception", "save")).click().run()
    assert saved_plan(repository, project, "reception").date_certainty is CeremonyDateCertainty.FIXED
    assert not reopened.exception


def test_guest_warning_does_not_block_save_and_undecided_date_is_not_silently_cleared(environment):
    repository, _client, open_app, _reopen = environment
    project, app = open_app()
    app.number_input(key=key(project, "reception", "guest_count")).set_value(150).run()
    assert any("exceeds" in item.value for item in app.warning)
    app.button(key=key(project, "reception", "save")).click().run()
    assert saved_plan(repository, project, "reception").guest_count == 150
    app.date_input(key=key(project, "reception", "ceremony_date")).set_value(date(2027, 10, 24)).run()
    app.button(key=key(project, "reception", "save")).click().run()
    assert any("Fixed or Tentative" in item.value for item in app.error)
    assert app.date_input(key=key(project, "reception", "ceremony_date")).value == date(2027, 10, 24)
    assert saved_plan(repository, project, "reception").ceremony_date is None
    assert not app.success


def test_suggestions_require_opt_in_and_do_not_save_or_confirm_dates(environment):
    repository, _client, open_app, _reopen = environment
    profile = WeddingProfile(guest_count=100, wedding_date_status=WeddingDateStatus.FIXED_DATE, wedding_date=date(2027, 10, 24))
    project, app = open_app([selected("wedding_ceremony")], profile)
    assert app.number_input(key=key(project, "wedding_ceremony", "guest_count")).value is None
    assert saved_plan(repository, project, "wedding_ceremony") is None
    app.button(key=key(project, "wedding_ceremony", "apply_suggestions")).click().run()
    assert app.number_input(key=key(project, "wedding_ceremony", "guest_count")).value == 100
    assert app.selectbox(key=key(project, "wedding_ceremony", "date_certainty")).value == "Tentative"
    assert saved_plan(repository, project, "wedding_ceremony") is None
    app.button(key=key(project, "wedding_ceremony", "save")).click().run()
    assert saved_plan(repository, project, "wedding_ceremony").date_certainty is CeremonyDateCertainty.TENTATIVE


def test_suggestions_can_be_dismissed_and_do_not_replace_entered_values(environment):
    _repository, _client, open_app, _reopen = environment
    profile = WeddingProfile(guest_count=100, wedding_date_status=WeddingDateStatus.FIXED_DATE, wedding_date=date(2027, 10, 24))
    project, app = open_app([selected("wedding_ceremony"), selected("reception")], profile)
    app.number_input(key=key(project, "wedding_ceremony", "guest_count")).set_value(40).run()
    app.button(key=key(project, "wedding_ceremony", "apply_suggestions")).click().run()
    assert app.number_input(key=key(project, "wedding_ceremony", "guest_count")).value == 40
    app.button(key=key(project, "reception", "dismiss_suggestions")).click().run()
    assert not any(item.key == key(project, "reception", "apply_suggestions") for item in app.button)
    assert app.number_input(key=key(project, "reception", "guest_count")).value is None


def test_failed_plan_save_preserves_edits_blocks_switching_and_recovers(environment):
    repository, _client, open_app, _reopen = environment
    project, app = open_app()
    with patch.object(repository, "save_ceremony_plan", side_effect=RuntimeError("private error")):
        app.number_input(key=key(project, "reception", "guest_count")).set_value(80).run()
        app.button(key=key(project, "reception", "save")).click().run()
        assert any("could not be saved or verified" in item.value for item in app.error)
        assert not app.success
        assert app.session_state[PROJECT_DIRTY_KEY]
        assert saved_plan(repository, project, "reception") is None
        app.button(key="planning_back").click().run()
        assert app.session_state[PROJECT_DIRTY_KEY]
        app.button(key="open_wedding_projects").click().run()
        assert app.button(key="start_new_wedding").disabled
        app.button(key=f"continue_wedding_project_{project['project_id']}").click().run()
        app.button(key="edit_ceremony_plans").click().run()
        assert app.number_input(key=key(project, "reception", "guest_count")).value == 80
    app.button(key=key(project, "reception", "save")).click().run()
    assert app.success and not app.error
    assert PROJECT_DIRTY_KEY not in app.session_state
    assert saved_plan(repository, project, "reception").guest_count == 80


def test_failed_plan_save_cannot_be_orphaned_by_removing_its_ceremony(environment):
    repository, _client, open_app, _reopen = environment
    project, app = open_app()
    with patch.object(repository, "save_ceremony_plan", side_effect=RuntimeError("private error")):
        app.number_input(key=key(project, "reception", "guest_count")).set_value(80).run()
        app.button(key=key(project, "reception", "save")).click().run()
    app.button(key="planning_back").click().run()
    app.button(key="choose_ceremonies_from_profile").click().run()
    assert app.button(key="confirm_wedding_ceremonies").disabled
    assert any("Retry failed ceremony plan saves" in item.value for item in app.warning)
    app.button(key="open_ceremony_planning").click().run()
    assert app.number_input(key=key(project, "reception", "guest_count")).value == 80
    app.button(key=key(project, "reception", "save")).click().run()
    assert not app.error
    assert PROJECT_DIRTY_KEY not in app.session_state


def test_false_plan_save_response_never_shows_success(environment):
    repository, _client, open_app, _reopen = environment
    project, app = open_app()
    row = repository.load_selected_project(project["project_id"])
    with patch.object(repository, "save_ceremony_plan", return_value=row):
        app.number_input(key=key(project, "reception", "guest_count")).set_value(80).run()
        app.button(key=key(project, "reception", "save")).click().run()
    assert not app.success
    assert app.error
    assert app.session_state[PROJECT_DIRTY_KEY]
    assert app.number_input(key=key(project, "reception", "guest_count")).value == 80


def test_custom_plan_logout_login_and_overview_restoration(environment):
    repository, _client, open_app, _reopen = environment
    custom_id = str(uuid4())
    custom = Ceremony(custom_id, "Family welcome", "Family gathering", is_custom=True, selected=True)
    project, app = open_app([custom])
    app.number_input(key=key(project, custom_id, "guest_count")).set_value(30).run()
    app.number_input(key=key(project, custom_id, "estimated_budget_inr")).set_value(12000.5).run()
    app.selectbox(key=key(project, custom_id, "date_certainty")).set_value("Tentative").run()
    app.date_input(key=key(project, custom_id, "ceremony_date")).set_value(date(2027, 10, 23)).run()
    app.button(key=key(project, custom_id, "save")).click().run()
    with (
        patch("ai_event_planning_platform.home.logout_user", return_value=True),
        patch("ai_event_planning_platform.home.login_user", return_value=TOKENS),
    ):
        app.button(key="logout").click().run()
        assert ACTIVE_PROJECT_ID_KEY not in app.session_state
        app.text_input(key="login_email").set_value("planner@example.invalid")
        app.text_input(key="login_password").set_value("not-stored")
        app.button(key="login_submit").click().run()
        app.button(key=f"continue_wedding_project_{project['project_id']}").click().run()
    open_plans_from_dashboard(app)
    assert app.number_input(key=key(project, custom_id, "guest_count")).value == 30
    app.button(key="planning_back").click().run()
    overview = [item.value for item in app.markdown]
    assert "Your Ceremony Plans" in [item.value for item in app.subheader]
    assert "**Family welcome**" in overview
    assert "Guests: 30" in overview
    assert "Tentative date: 23 October 2027" in overview
    assert "Estimated budget: ₹12,000.5" in overview
    assert saved_plan(repository, project, custom_id).ceremony_name == "Family welcome"
    assert not app.exception


def test_plans_and_drafts_are_isolated_between_projects(environment):
    repository, _client, open_app, _reopen = environment
    first, app = open_app()
    second = repository.create_wedding_project("Second wedding", WeddingProfile(), [selected("reception")], planning_step=CEREMONY_PLANNING_PAGE)
    app.number_input(key=key(first, "reception", "guest_count")).set_value(80).run()
    app.button(key=key(first, "reception", "save")).click().run()
    app.number_input(key=key(first, "haldi", "guest_count")).set_value(40).run()
    app.button(key="open_wedding_projects").click().run()
    app.button(key=f"continue_wedding_project_{second['project_id']}").click().run()
    open_plans_from_dashboard(app)
    assert app.number_input(key=key(second, "reception", "guest_count")).value is None
    app.number_input(key=key(second, "reception", "guest_count")).set_value(20).run()
    app.button(key=key(second, "reception", "save")).click().run()
    app.button(key="open_wedding_projects").click().run()
    app.button(key=f"continue_wedding_project_{first['project_id']}").click().run()
    open_plans_from_dashboard(app)
    assert app.number_input(key=key(first, "reception", "guest_count")).value == 80
    assert app.number_input(key=key(first, "haldi", "guest_count")).value is None
    assert saved_plan(repository, second, "reception").guest_count == 20


def test_profile_edits_and_ceremony_reconfirmation_preserve_plans(environment):
    repository, _client, open_app, _reopen = environment
    project, app = open_app()
    app.number_input(key=key(project, "reception", "guest_count")).set_value(80).run()
    app.button(key=key(project, "reception", "save")).click().run()
    app.button(key="planning_back").click().run()
    app.text_input(key=f"wedding_{project['project_id']}_city_value").set_value("Jaipur")
    app.button(key=f"wedding_{project['project_id']}_save_profile").click().run()
    assert saved_plan(repository, project, "reception").guest_count == 80
    app.button(key="choose_ceremonies_from_profile").click().run()
    assert not app.warning
    app.checkbox(key=f"{CEREMONY_CHECKBOX_PREFIX}engagement").check().run()
    app.button(key="confirm_wedding_ceremonies").click().run()
    assert saved_plan(repository, project, "reception").guest_count == 80
    row = repository.load_selected_project(project["project_id"])
    assert {item.ceremony_id for item in ceremonies_from_json(row["selected_ceremonies"])} == {"reception", "haldi", "engagement"}
    assert row["wedding_profile"]["city"] == "Jaipur"


def test_reruns_do_not_fetch_or_write_plans_and_unconfirmed_ceremonies_are_excluded(environment):
    repository, _client, open_app, _reopen = environment
    project, app = open_app([selected("reception")])
    with (
        patch.object(repository, "load_selected_project", wraps=repository.load_selected_project) as load,
        patch.object(repository, "save_ceremony_plan", wraps=repository.save_ceremony_plan) as save,
    ):
        app.number_input(key=key(project, "reception", "guest_count")).set_value(80).run()
        app.run()
    load.assert_not_called()
    save.assert_not_called()
    assert len(app.expander) == 1
    assert app.expander[0].label == "Reception"


def test_no_ceremonies_and_unauthenticated_routes_are_safe(environment):
    _repository, _client, open_app, _reopen = environment
    _project, app = open_app([])
    assert any("No ceremonies selected yet" in item.value for item in app.info)
    assert not app.expander
    app = AppTest.from_file("../app.py")
    app.session_state["page"] = CEREMONY_PLANNING_PAGE
    app.run()
    assert app.session_state["page"] == "login"
    assert ACCESS_TOKEN_KEY not in app.session_state
    assert not app.exception
