from dataclasses import replace
from datetime import date
from unittest.mock import Mock, patch

import pytest
from streamlit.testing.v1 import AppTest

from ai_event_planning_platform.auth import ACCESS_TOKEN_KEY, AuthSessionTokens, store_auth_session
from ai_event_planning_platform.wedding_project_state import BUDGET_DRAFT_KEY, PROFILE_SAVED_KEY, PROJECT_DIRTY_KEY
from ai_event_planning_platform.wedding_projects import WeddingProjectRepository
from ai_event_planning_platform.wedding_timeline import fallback_timeline
from test_budget_engine import ceremony, profile, reviewed_plan
from test_wedding_projects import FakeClient, OWNER_ONE
from test_wedding_timeline import dated_ceremony
from test_dashboard_data import essential_profile
from ai_event_planning_platform.wedding_profile import WeddingDateStatus, WeddingProfile


def visit(app, route):
    app._page_hash = next(key for key, info in app._registered_pages.items() if info.get("url_pathname") == route)
    return app.run()


@pytest.fixture
def environment(monkeypatch):
    repo = WeddingProjectRepository(FakeClient(), OWNER_ONE)
    monkeypatch.setattr("ai_event_planning_platform.home.restore_authenticated_session", lambda a, r: AuthSessionTokens(a, r))
    monkeypatch.setattr("ai_event_planning_platform.home.project_repository_from_state", lambda s: repo)
    monkeypatch.setattr("ai_event_planning_platform.wedding_project_state.project_repository_from_state", lambda s: repo)
    monkeypatch.setattr("ai_event_planning_platform.invitation_state.project_repository_from_state", lambda s: repo)
    ai = Mock(side_effect=AssertionError("Dashboard must not call Groq"))
    monkeypatch.setattr("ai_event_planning_platform.timeline_intelligence.interpret_timeline", ai)
    monkeypatch.setattr("ai_event_planning_platform.budget_intelligence.interpret_budget_needs", ai)
    def open_project(project_id):
        app = AppTest.from_file("../app.py", default_timeout=20)
        store_auth_session(app.session_state, AuthSessionTokens("access", "refresh"))
        app.session_state["page"] = "wedding_projects"
        app.run()
        app.button(key=f"continue_wedding_project_{project_id}").click().run()
        assert not app.exception
        return app
    def create(*, names=True, accepted=True):
        p = profile(bride_name="Priya" if names else None, groom_name="Rahul" if names else None)
        cs = [dated_ceremony(), ceremony("reception")]
        row = repo.create_wedding_project("Custom stored name", p, cs, planning_step="smart_wedding_budget")
        if accepted:
            budget = replace(reviewed_plan(p, cs), accepted=True)
            repo.save_budget_plan(row["project_id"], budget)
            repo.save_timeline_plan(row["project_id"], replace(fallback_timeline(p, cs, budget), accepted=True))
        return row, open_project(row["project_id"])
    return repo, create, open_project, ai


def test_open_project_enters_dashboard_despite_old_step_and_shows_saved_cards(environment):
    repo, create, _open, ai = environment
    row, app = create()
    assert app.session_state["page"] == "wedding_dashboard"
    assert app.title[0].value == "Priya & Rahul's Wedding"
    assert repo.load_selected_project(row["project_id"])["project_name"] == "Custom stored name"
    assert {m.label: m.value for m in app.metric}["Selected ceremonies"] == "2"
    assert not app.get("page_link")  # Four body destinations replace the duplicate header links.
    assert [app.button(key="wedding_dashboard_" + key).label for key in ("details", "budget", "calendar", "invitations")] == [
        "Wedding Details", "Budget Dashboard", "Calendar & Timeline", "Invitations"]
    assert not next(e for e in app.expander if e.label == "Wedding summary").proto.expanded
    assert not next(e for e in app.expander if e.label == "Planning alerts").proto.expanded
    ai.assert_not_called()


def test_navigation_history_preserves_project_and_never_reads_project_on_rerun(environment):
    repo, create, _open, ai = environment
    row, app = create()
    with patch.object(repo, "load_selected_project", wraps=repo.load_selected_project) as load:
        visit(app, "budget_dashboard")
        assert app.title[0].value == "Budget Dashboard"
        assert len(app.get("vega_lite_chart")) == 1
        visit(app, "wedding_timeline")
        assert any(e.label == "Planning Status" for e in app.expander)
        visit(app, "budget_dashboard")
        visit(app, "wedding_dashboard")
        app.run()
        load.assert_not_called()
    assert app.session_state["active_wedding_project_id"] == row["project_id"]
    assert app.session_state[ACCESS_TOKEN_KEY] == "access"
    ai.assert_not_called()
    assert not app.exception


def test_budget_drilldown_uses_saved_plan_and_opens_existing_focused_editor(environment):
    _repo, create, _open, _ai = environment
    _row, app = create()
    saved = app.session_state[BUDGET_DRAFT_KEY]
    app.session_state[BUDGET_DRAFT_KEY] = replace(saved, contingency_inr=9999999, accepted=False)
    visit(app, "budget_dashboard")
    assert {m.label: m.value for m in app.metric}["Contingency"] != "₹99,99,999"
    app.selectbox(key="wedding_dashboard_category").set_value("Photography and videography").run()
    assert app.dataframe[-1].value["Ceremony"].tolist() == ["Shared expenses"]
    app.button(key="wedding_dashboard_edit_haldi").click().run()
    assert not app.exception
    assert app.session_state["page"] == "smart_wedding_budget"
    assert app.session_state["wedding_budget_focus"] == "haldi"
    assert next(e for e in app.expander if e.label.startswith("Haldi")).proto.expanded
    assert not app.exception


def test_optional_prompt_skip_edit_save_and_personalized_cards(environment):
    repo, create, _open, _ai = environment
    row, app = create(names=False)
    assert app.title[0].value == "Custom stored name"
    app.button(key="wedding_names_skip").click().run()
    app.button(key="wedding_names_edit").click().run()
    app.text_input(key="wedding_names_bride").set_value("Priya")
    app.text_input(key="wedding_names_groom").set_value("Rahul")
    app.button(key="wedding_names_save").click().run()
    assert app.title[0].value == "Priya & Rahul's Wedding"
    assert any("read back" in s.value for s in app.success)
    assert repo.load_selected_project(row["project_id"])["project_name"] == "Custom stored name"
    app.button(key="open_wedding_projects").click().run()
    assert any(s.value == "Priya & Rahul's Wedding" for s in app.subheader)
    assert not app.exception


def test_failed_names_save_retains_entries_and_no_false_success(environment):
    repo, create, _open, _ai = environment
    row, app = create(names=False)
    app.text_input(key="wedding_names_bride").set_value("Priya")
    app.text_input(key="wedding_names_groom").set_value("Rahul")
    with patch.object(repo, "save_couple_names", side_effect=RuntimeError("network")):
        app.button(key="wedding_names_save").click().run()
    assert not app.success and app.session_state[PROJECT_DIRTY_KEY]
    assert app.text_input(key="wedding_names_bride").value == "Priya"
    visit(app, "budget_dashboard")
    visit(app, "wedding_dashboard")
    assert app.text_input(key="wedding_names_groom").value == "Rahul"
    app.button(key="wedding_names_save").click().run()
    assert app.session_state[PROFILE_SAVED_KEY].groom_name == "Rahul"
    assert PROJECT_DIRTY_KEY not in app.session_state


def test_calendar_status_links_target_existing_editors(environment):
    _repo, create, _open, _ai = environment
    _row, app = create()
    visit(app, "wedding_timeline")
    button = next(b for b in app.button if b.label == "Set Ceremony Date")
    button.click().run()
    assert app.session_state["page"] == "ceremony_planning"
    assert app.session_state["wedding_ceremony_focus"] == "reception"
    assert next(e for e in app.expander if e.label == "Reception").proto.expanded
    assert not app.exception


def test_invitation_studio_is_available_and_header_not_duplicated(environment):
    _repo, create, _open, ai = environment
    _row, app = create()
    visit(app, "wedding_invitations")
    assert app.title[0].value == "Wedding Invitation Studio"
    assert not any(i.value == "Coming Soon" for i in app.info)
    assert len([b for b in app.button if b.label == "Back"]) == 1
    assert len([b for b in app.button if b.label == "My Wedding Projects"]) == 1
    assert not any("Generate" in b.label for b in app.button)
    ai.assert_not_called()


def test_name_restore_logout_login_and_multiple_wedding_isolation(environment):
    repo, create, reopen, _ai = environment
    first, app = create()
    with (patch("ai_event_planning_platform.home.logout_user", return_value=True),
          patch("ai_event_planning_platform.home.login_user", return_value=AuthSessionTokens("a", "r"))):
        app.button(key="logout").click().run()
        assert PROFILE_SAVED_KEY not in app.session_state
        app.text_input(key="login_email").set_value("planner@example.invalid")
        app.text_input(key="login_password").set_value("not-stored")
        app.button(key="login_submit").click().run()
        assert app.title[0].value == "My Wedding Projects"
        app.button(key=f"continue_wedding_project_{first['project_id']}").click().run()
    assert app.title[0].value == "Priya & Rahul's Wedding"
    second = repo.create_wedding_project("Second wedding", profile(), [])
    app.button(key="open_wedding_projects").click().run()
    app.button(key=f"continue_wedding_project_{second['project_id']}").click().run()
    assert app.title[0].value == "Second wedding" and app.session_state[PROFILE_SAVED_KEY].bride_name is None
    assert reopen(first["project_id"]).title[0].value == "Priya & Rahul's Wedding"


def test_new_project_names_are_optional_and_not_auth_fields(environment):
    repo, create, _open, _ai = environment
    _row, app = create()
    app.button(key="open_wedding_projects").click().run()
    app.text_input(key="new_wedding_project_name").set_value("A separate project")
    app.text_input(key="new_wedding_bride_name").set_value("Asha")
    app.text_input(key="new_wedding_groom_name").set_value("Dev")
    app.button(key="start_new_wedding").click().run()
    assert app.title[0].value == "Asha & Dev's Wedding"
    assert repo.list_user_projects()[-1]["project_name"] == "A separate project"
    assert not app.exception


def test_requirement_name_edits_preserve_accepted_budget_and_timeline(environment):
    from copy import deepcopy
    repo, create, _open, _ai = environment
    row, app = create()
    before = deepcopy(repo.load_selected_project(row["project_id"]))
    app.button(key="wedding_dashboard_details").click().run()
    app.text_input(key=f"wedding_{row['project_id']}_bride_name_value").set_value("Asha")
    app.text_input(key=f"wedding_{row['project_id']}_groom_name_value").set_value("Dev")
    app.button(key=f"wedding_{row['project_id']}_save_profile").click().run()
    saved = repo.load_selected_project(row["project_id"])
    assert saved["project_name"] == before["project_name"]
    assert saved["selected_ceremonies"] == before["selected_ceremonies"]
    for key in ("smart_budget_plan", "wedding_timeline"):
        assert saved["wedding_profile"][key] == before["wedding_profile"][key]
    visit(app, "budget_dashboard")
    assert any(s.value == "Asha & Dev's Wedding" for s in app.subheader)
    assert not any("Planning details changed" in w.value for w in app.warning)
    assert not app.exception


def test_compact_header_uses_saved_approximate_date_location_and_available_name(environment):
    from ai_event_planning_platform.wedding_form import DRAFT_STATE_KEY
    repo, _create, reopen, ai = environment
    p = essential_profile(bride_name="Esha", groom_name=None, wedding_date_status=WeddingDateStatus.APPROXIMATE_DATE,
                          wedding_date=date(2027, 11, 1))
    row = repo.create_wedding_project("Stored project name", p, [])
    app = reopen(row["project_id"])
    app.session_state[DRAFT_STATE_KEY] = replace(p, bride_name="Unsaved name", city="Unsaved city", wedding_date=date(2028, 1, 1))
    app.run()
    assert app.title[0].value == "Esha's Wedding"
    header = app.caption[0].value
    assert "November 2027 (approximate date)" in header and "Pune, Maharashtra" in header
    assert "01 November" not in header and "Unsaved" not in header
    assert app.button(key="wedding_dashboard_next_step").label == "Choose Ceremonies"
    ai.assert_not_called()


@pytest.mark.parametrize("stage,label,route", [
    ("details", "Complete Wedding Details", "step_by_step"),
    ("ceremonies", "Choose Ceremonies", "wedding_ceremonies"),
    ("plans", "Plan Ceremonies", "ceremony_planning"),
    ("budget", "Review Smart Budget", "smart_wedding_budget"),
    ("timeline", "Create Wedding Timeline", "wedding_timeline"),
    ("complete", "Review Wedding Dashboard", "wedding_dashboard"),
])
def test_one_saved_next_step_opens_the_relevant_existing_page(environment, stage, label, route):
    repo, _create, reopen, ai = environment
    p = WeddingProfile() if stage == "details" else essential_profile()
    cs = [] if stage in {"details", "ceremonies"} else [dated_ceremony()]
    if stage == "plans":
        cs = [replace(cs[0], plan=None)]
    row = repo.create_wedding_project("Next step wedding", p, cs)
    if stage in {"timeline", "complete"}:
        budget = replace(reviewed_plan(p, cs), accepted=True)
        repo.save_budget_plan(row["project_id"], budget)
        if stage == "complete":
            repo.save_timeline_plan(row["project_id"], replace(fallback_timeline(p, cs, budget), accepted=True))
    app = reopen(row["project_id"])
    assert app.button(key="wedding_dashboard_next_step").label == label
    assert len([b for b in app.button if b.proto.type == "primary"]) == 1
    assert not app.button(key="wedding_dashboard_invitations").disabled
    app.button(key="wedding_dashboard_next_step").click().run()
    assert not app.exception and app.session_state["page"] == route
    assert app.session_state["active_wedding_project_id"] == row["project_id"]
    if stage == "plans":
        assert app.session_state["wedding_ceremony_focus"] == cs[0].ceremony_id
    if stage == "complete":
        assert next(e for e in app.expander if e.label == "Wedding summary").proto.expanded
    ai.assert_not_called()


@pytest.mark.parametrize("key,route", [("details", "step_by_step"), ("budget", "budget_dashboard"),
    ("calendar", "wedding_timeline"), ("invitations", "wedding_invitations")])
def test_four_feature_destinations_work_independently_for_minimal_weddings(environment, key, route):
    repo, _create, reopen, ai = environment
    row = repo.create_wedding_project("Minimal wedding", WeddingProfile(), [])
    app = reopen(row["project_id"])
    app.button(key="wedding_dashboard_" + key).click().run()
    assert not app.exception and app.session_state["page"] == route
    assert app.session_state["active_wedding_project_id"] == row["project_id"]
    ai.assert_not_called()


def test_next_step_and_header_are_isolated_when_switching_projects(environment):
    repo, _create, reopen, _ai = environment
    first = repo.create_wedding_project("First wedding", essential_profile(bride_name="Asha", groom_name="Dev"), [])
    second = repo.create_wedding_project("Second wedding", WeddingProfile(), [])
    app = reopen(first["project_id"])
    assert app.button(key="wedding_dashboard_next_step").label == "Choose Ceremonies"
    app.button(key="open_wedding_projects").click().run()
    app.button(key=f"continue_wedding_project_{second['project_id']}").click().run()
    assert app.title[0].value == "Second wedding"
    assert app.button(key="wedding_dashboard_next_step").label == "Complete Wedding Details"
    assert "Asha" not in app.caption[0].value
    assert not app.exception
