from dataclasses import replace
from unittest.mock import Mock, patch

import pytest
from streamlit.testing.v1 import AppTest

from ai_event_planning_platform.auth import AuthSessionTokens, store_auth_session
from ai_event_planning_platform.budget_engine import infer_ceremony_needs
from ai_event_planning_platform.wedding_project_state import BUDGET_DRAFT_KEY, BUDGET_SAVED_KEY, PROJECT_DIRTY_KEY
from ai_event_planning_platform.wedding_projects import WeddingProjectRepository
from ai_event_planning_platform.smart_wedding_budget import WORKING_KEY
from test_budget_engine import ceremony, profile
from test_wedding_projects import FakeClient, OWNER_ONE


@pytest.fixture
def environment(monkeypatch):
    client = FakeClient()
    repo = WeddingProjectRepository(client, OWNER_ONE)
    monkeypatch.setattr("ai_event_planning_platform.home.restore_authenticated_session", lambda a, r: AuthSessionTokens(a, r))
    monkeypatch.setattr("ai_event_planning_platform.home.project_repository_from_state", lambda state: repo)
    monkeypatch.setattr("ai_event_planning_platform.wedding_project_state.project_repository_from_state", lambda state: repo)
    ai = Mock(side_effect=lambda p, ceremonies: infer_ceremony_needs(p, ceremonies))
    monkeypatch.setattr("ai_event_planning_platform.budget_intelligence.interpret_budget_needs", ai)

    def open_project(project_id):
        app = AppTest.from_file("../app.py", default_timeout=10)
        store_auth_session(app.session_state, AuthSessionTokens("test-access", "test-refresh"))
        app.session_state["page"] = "wedding_projects"
        app.run()
        app.button(key=f"continue_wedding_project_{project_id}").click().run()
        app.button(key="wedding_dashboard_budget").click().run()
        app.button(key="wedding_dashboard_edit_budget").click().run()
        return app

    def create(p=None, ceremonies=None):
        row = repo.create_wedding_project("Budget wedding", p or profile(), ceremonies or [ceremony()],
                                          planning_step="smart_wedding_budget")
        return row, open_project(row["project_id"])

    return repo, create, open_project, ai


def generate_and_review(app):
    app.button(key="wedding_budget_generate").click().run()
    app.checkbox(key="wedding_budget_editor_reviewed").check()
    app.button(key="wedding_budget_apply_needs").click().run()
    return app


def accept_and_save(app):
    app.checkbox(key="wedding_budget_editor_accept").check().run()
    app.button(key="wedding_budget_save").click().run()
    return app


def test_budget_route_requires_authentication():
    app = AppTest.from_file("../app.py")
    app.session_state["page"] = "smart_wedding_budget"
    app.run()
    assert app.title[0].value == "Log in"
    assert not app.button(key="login_submit").disabled
    assert not app.exception


def test_generation_review_acceptance_and_saved_readback(environment):
    repo, create, _open, ai = environment
    row, app = create(ceremonies=[ceremony(budget=200000)])
    assert app.title[0].value == "Smart Wedding Budget"
    app.button(key="wedding_budget_generate").click().run()
    assert app.button(key="wedding_budget_save").disabled
    assert "smart_budget_plan" not in row["wedding_profile"]
    app.checkbox(key="wedding_budget_editor_reviewed").check()
    app.button(key="wedding_budget_apply_needs").click().run()
    assert app.button(key="wedding_budget_save").disabled
    accept_and_save(app)
    assert any("saved and read back" in item.value for item in app.success)
    assert repo.load_selected_project(row["project_id"])["selected_ceremonies"][0]["plan"]["estimated_budget_inr"] == 200000
    assert app.session_state[BUDGET_SAVED_KEY].accepted
    assert ai.call_count == 1
    assert not app.exception


def test_edit_and_recalculate_totals_without_api_or_database_calls(environment):
    repo, create, _open, ai = environment
    _row, app = create()
    generate_and_review(app)
    key = "wedding_budget_editor_allocation_haldi:catering"
    before = next(e.allocated_inr for e in app.session_state[BUDGET_DRAFT_KEY].expenses if e.category == "catering")
    with patch.object(repo, "save_budget_plan", wraps=repo.save_budget_plan) as save:
        app.number_input(key=key).set_value(before + 1000).run()
        app.button(key="wedding_budget_recalculate_haldi").click().run()
        app.run()
        save.assert_not_called()
    assert next(e.allocated_inr for e in app.session_state[BUDGET_DRAFT_KEY].expenses if e.category == "catering") == before + 1000
    assert ai.call_count == 1
    assert not app.exception


def test_infeasible_budget_cannot_be_saved_and_exposes_shortfall(environment):
    _repo, create, _open, _ai = environment
    row, app = create(p=profile(1000))
    generate_and_review(app)
    app.checkbox(key="wedding_budget_editor_accept").check().run()
    assert app.button(key="wedding_budget_save").disabled
    assert any("shortfall" in w.value for w in app.warning)
    assert "smart_budget_plan" not in row["wedding_profile"]


def test_user_can_change_meal_venue_guests_and_services(environment):
    _repo, create, _open, _ai = environment
    _row, app = create()
    app.button(key="wedding_budget_generate").click().run()
    app.selectbox(key="wedding_budget_editor_haldi_meal").set_value("lunch")
    app.selectbox(key="wedding_budget_editor_haldi_venue").set_value("hotel")
    app.number_input(key="wedding_budget_editor_haldi_guests").set_value(20)
    app.multiselect(key="wedding_budget_editor_haldi_services").set_value(["catering", "venue_rental"])
    app.checkbox(key="wedding_budget_editor_reviewed").check()
    app.button(key="wedding_budget_apply_needs").click().run()
    n = app.session_state[BUDGET_DRAFT_KEY].needs[0]
    assert (n.catering, n.venue, n.guest_count, n.reviewed) == ("lunch", "hotel", 20, True)
    assert {e.category for e in app.session_state[BUDGET_DRAFT_KEY].expenses} == {"catering", "venue_rental"}
    assert not app.exception


def test_accepted_budget_restores_after_logout_login_and_isolates_projects(environment):
    repo, create, reopen, _ai = environment
    first, app = create()
    generate_and_review(app)
    accept_and_save(app)
    plan = app.session_state[BUDGET_SAVED_KEY]
    with (patch("ai_event_planning_platform.home.logout_user", return_value=True),
          patch("ai_event_planning_platform.home.login_user", return_value=AuthSessionTokens("a", "r"))):
        app.button(key="logout").click().run()
        assert BUDGET_SAVED_KEY not in app.session_state
        app.text_input(key="login_email").set_value("planner@example.invalid")
        app.text_input(key="login_password").set_value("not-stored")
        app.button(key="login_submit").click().run()
        app.button(key=f"continue_wedding_project_{first['project_id']}").click().run()
    assert app.session_state[BUDGET_SAVED_KEY] == plan
    app.button(key="wedding_dashboard_budget").click().run()
    app.button(key="wedding_dashboard_edit_budget").click().run()
    assert any("Saved and verified" in item.value for item in app.caption)
    second = repo.create_wedding_project("Second", profile(), [ceremony("reception")], planning_step="smart_wedding_budget")
    app.button(key="open_wedding_projects").click().run()
    app.button(key=f"continue_wedding_project_{second['project_id']}").click().run()
    assert BUDGET_SAVED_KEY not in app.session_state and BUDGET_DRAFT_KEY not in app.session_state
    assert "smart_budget_plan" not in second["wedding_profile"]
    assert reopen(first["project_id"]).session_state[BUDGET_SAVED_KEY] == plan
    assert not app.exception


def test_failed_save_has_no_success_and_blocks_switching_until_retried(environment):
    repo, create, _open, _ai = environment
    first, app = create()
    second = repo.create_wedding_project("Second", profile(), [ceremony()])
    generate_and_review(app)
    with patch.object(repo, "save_budget_plan", side_effect=RuntimeError("readback failed")):
        accept_and_save(app)
    assert not app.success
    assert any("could not be saved or verified" in e.value for e in app.error)
    assert app.session_state[PROJECT_DIRTY_KEY]
    app.button(key="open_wedding_projects").click().run()
    assert app.button(key=f"continue_wedding_project_{second['project_id']}").disabled
    app.button(key=f"continue_wedding_project_{first['project_id']}").click().run()
    accept_and_save(app)
    assert any("saved and read back" in s.value for s in app.success)
    assert PROJECT_DIRTY_KEY not in app.session_state
    assert not app.exception


def test_existing_saved_plan_is_retained_until_explicit_draft_replacement(environment):
    repo, create, _open, ai = environment
    row, app = create()
    generate_and_review(app)
    accept_and_save(app)
    saved_json = repo.load_selected_project(row["project_id"])["wedding_profile"]["smart_budget_plan"]
    assert app.button(key="wedding_budget_generate").disabled
    app.checkbox(key="wedding_budget_replace_draft").check().run()
    app.button(key="wedding_budget_generate").click().run()
    assert repo.load_selected_project(row["project_id"])["wedding_profile"]["smart_budget_plan"] == saved_json
    assert not app.session_state[BUDGET_DRAFT_KEY].accepted
    assert ai.call_count == 1
    assert not app.exception


def test_overview_budget_navigation_and_missing_requirements(environment):
    _repo, create, _open, _ai = environment
    _row, app = create(p=replace(profile(), budget_inr=None))
    assert any("Save a total wedding budget" in i.value for i in app.info)
    app.session_state["page"] = "step_by_step"
    app.run()
    assert app.button(key="wedding_budget_open").label == "Smart Wedding Budget"
    app.button(key="wedding_budget_open").click().run()
    assert app.title[0].value == "Smart Wedding Budget"
    assert not app.exception


def test_ai_failure_keeps_budget_review_available_without_automatic_retries(environment):
    from ai_event_planning_platform.budget_intelligence import BudgetIntelligenceError

    _repo, create, _open, ai = environment
    ai.side_effect = BudgetIntelligenceError("Groq unavailable; review rule-based suggestions.")
    _row, app = create()
    app.button(key="wedding_budget_generate").click().run()
    assert any("Groq unavailable" in i.value for i in app.info)
    app.run()
    app.checkbox(key="wedding_budget_replace_draft").check().run()
    app.button(key="wedding_budget_generate").click().run()
    assert ai.call_count == 1
    assert not app.exception


def test_changed_context_keeps_saved_budget_but_requires_regeneration(environment):
    repo, create, reopen, _ai = environment
    row, app = create()
    generate_and_review(app)
    accept_and_save(app)
    saved = app.session_state[BUDGET_SAVED_KEY]
    repo.update_project(row["project_id"], profile=profile(city="Jaipur"))
    app = reopen(row["project_id"])
    assert app.session_state[BUDGET_SAVED_KEY] == saved
    assert any("confirmed ceremonies changed" in w.value for w in app.warning)
    assert not any(b.key == "wedding_budget_save" for b in app.button)
    assert not app.exception


def test_one_expander_per_ceremony_with_all_rows_and_no_nested_expanders(environment):
    from uuid import uuid4
    from ai_event_planning_platform.wedding_ceremonies import Ceremony

    _repo, create, _open, _ai = environment
    custom = Ceremony(str(uuid4()), "Family welcome", "A family gathering.", is_custom=True, selected=True)
    ceremonies = [ceremony(), ceremony("reception"), custom]
    _row, app = create(ceremonies=ceremonies)
    generate_and_review(app)
    labels = [section.label for section in app.expander]
    assert sorted(labels) == sorted([c.name for c in ceremonies] + ["Review catering, services and assumptions", "How this budget was calculated"])
    for section in app.expander:
        assert len(section.expander) == 1  # Only the section itself; no descendant expanders.
        if section.label in {c.name for c in ceremonies}:
            c = next(c for c in ceremonies if c.name == section.label)
            expenses = [e for e in app.session_state[BUDGET_DRAFT_KEY].expenses if e.ceremony_id == c.ceremony_id]
            assert len(section.text_input) == len(expenses)
            assert {w.key for w in section.text_input} == {"wedding_budget_editor_name_" + e.key for e in expenses}
            assert len([b for b in section.button if b.label == "Recalculate Budget"]) == 1
            assert all(e.category != "photography" for e in expenses)
    assert not app.exception


def test_row_rename_and_amount_are_staged_until_local_recalculation(environment):
    repo, create, _open, ai = environment
    row, app = create(ceremonies=[ceremony(budget=200000)])
    generate_and_review(app)
    original = app.session_state[BUDGET_DRAFT_KEY]
    amount = next(e.allocated_inr for e in original.expenses if e.key == "haldi:catering")
    app.text_input(key="wedding_budget_editor_name_haldi:catering").set_value("Breakfast buffet").run()
    app.number_input(key="wedding_budget_editor_allocation_haldi:catering").set_value(amount + 2000).run()
    assert app.session_state[BUDGET_DRAFT_KEY] == original
    assert any("Unrecalculated edits" in i.value for i in app.info)
    assert app.button(key="wedding_budget_save").disabled
    with (patch.object(repo, "load_selected_project", wraps=repo.load_selected_project) as load,
          patch.object(repo, "save_budget_plan", wraps=repo.save_budget_plan) as save):
        app.button(key="wedding_budget_recalculate_haldi").click().run()
        load.assert_not_called()
        save.assert_not_called()
    changed = next(e for e in app.session_state[BUDGET_DRAFT_KEY].expenses if e.key == "haldi:catering")
    assert changed.name == "Breakfast buffet" and changed.allocated_inr == amount + 2000
    assert ai.call_count == 1
    assert "smart_budget_plan" not in repo.load_selected_project(row["project_id"])["wedding_profile"]
    assert not app.exception


def test_add_remove_undo_and_repeated_recalculation_preserve_custom_row(environment):
    _repo, create, _open, ai = environment
    _row, app = create(ceremonies=[ceremony(budget=200000)])
    generate_and_review(app)
    app.button(key="wedding_budget_editor_add_haldi").click().run()
    custom = next(e for e in app.session_state[WORKING_KEY].expenses if e.category == "custom")
    app.text_input(key="wedding_budget_editor_name_" + custom.key).set_value("Welcome drinks").run()
    app.number_input(key="wedding_budget_editor_allocation_" + custom.key).set_value(1500).run()
    app.button(key="wedding_budget_editor_remove_haldi:catering").click().run()
    assert not any(e.key == "haldi:catering" for e in app.session_state[WORKING_KEY].expenses)
    app.button(key="wedding_budget_recalculate_haldi").click().run()
    app.button(key="wedding_budget_recalculate_haldi").click().run()
    plan = app.session_state[BUDGET_DRAFT_KEY]
    assert any(e.name == "Welcome drinks" and e.allocated_inr == 1500 for e in plan.expenses)
    assert not any(e.key == "haldi:catering" for e in plan.expenses)
    assert any("estimate may be incomplete" in w.value for w in app.warning)
    app.button(key="wedding_budget_editor_undo_haldi:catering").click().run()
    app.button(key="wedding_budget_recalculate_haldi").click().run()
    assert any(e.key == "haldi:catering" for e in app.session_state[BUDGET_DRAFT_KEY].expenses)
    assert ai.call_count == 1
    assert not app.exception


def test_recalculating_one_ceremony_leaves_other_pending_rows_unchanged(environment):
    _repo, create, _open, _ai = environment
    _row, app = create(ceremonies=[ceremony(budget=200000), ceremony("reception", budget=200000)])
    generate_and_review(app)
    original = app.session_state[BUDGET_DRAFT_KEY]
    app.text_input(key="wedding_budget_editor_name_reception:catering").set_value("Reception buffet").run()
    app.text_input(key="wedding_budget_editor_name_haldi:catering").set_value("Haldi buffet").run()
    app.button(key="wedding_budget_recalculate_haldi").click().run()
    changed = app.session_state[BUDGET_DRAFT_KEY]
    assert [e for e in changed.expenses if e.ceremony_id != "haldi"] == [e for e in original.expenses if e.ceremony_id != "haldi"]
    assert next(e for e in app.session_state[WORKING_KEY].expenses if e.key == "reception:catering").name == "Reception buffet"
    assert app.button(key="wedding_budget_save").disabled
    app.button(key="wedding_budget_recalculate_reception").click().run()
    assert next(e for e in app.session_state[BUDGET_DRAFT_KEY].expenses if e.key == "reception:catering").name == "Reception buffet"
    assert not app.exception


def test_shared_costs_and_contingency_recalculate_separately(environment):
    _repo, create, _open, ai = environment
    _row, app = create()
    generate_and_review(app)
    original = app.session_state[BUDGET_DRAFT_KEY]
    assert original.assumptions.contingency_percent == 10
    app.number_input(key="wedding_budget_editor_allocation_shared:photography").set_value(30000).run()
    app.number_input(key="wedding_budget_editor_contingency").set_value(90000).run()
    assert app.session_state[BUDGET_DRAFT_KEY] == original
    app.button(key="wedding_budget_recalculate_shared").click().run()
    changed = app.session_state[BUDGET_DRAFT_KEY]
    assert changed.contingency_inr == 90000 and changed.assumptions.contingency_percent == 10
    assert [e for e in changed.expenses if e.ceremony_id] == [e for e in original.expenses if e.ceremony_id]
    assert sum(e.allocated_inr for e in changed.expenses if e.ceremony_id is None) == 30000
    assert ai.call_count == 1
    assert not app.exception


def test_blank_name_blocks_recalculation_and_preserves_previous_totals(environment):
    _repo, create, _open, _ai = environment
    _row, app = create()
    generate_and_review(app)
    original = app.session_state[BUDGET_DRAFT_KEY]
    app.text_input(key="wedding_budget_editor_name_haldi:catering").set_value(" ").run()
    app.button(key="wedding_budget_recalculate_haldi").click().run()
    assert app.session_state[BUDGET_DRAFT_KEY] == original
    assert any("Check expense names" in e.value for e in app.error)
    assert app.button(key="wedding_budget_save").disabled
    assert not app.exception


def test_draft_additions_and_removals_survive_navigation_without_recalculation(environment):
    _repo, create, _open, _ai = environment
    _row, app = create()
    generate_and_review(app)
    app.button(key="wedding_budget_editor_add_haldi").click().run()
    custom = next(e for e in app.session_state[WORKING_KEY].expenses if e.category == "custom")
    app.text_input(key="wedding_budget_editor_name_" + custom.key).set_value("Welcome drinks").run()
    app.button(key="wedding_budget_editor_remove_haldi:catering").click().run()
    app.session_state["page"] = "step_by_step"
    app.run()
    app.button(key="wedding_budget_open").click().run()
    assert app.text_input(key="wedding_budget_editor_name_" + custom.key).value == "Welcome drinks"
    assert not any(w.key == "wedding_budget_editor_name_haldi:catering" for w in app.text_input)
    assert any("Unrecalculated edits" in i.value for i in app.info)
    assert not app.exception


def test_saved_custom_and_removed_rows_restore_after_logout_login(environment):
    repo, create, _open, _ai = environment
    row, app = create(ceremonies=[ceremony(budget=200000)])
    generate_and_review(app)
    app.button(key="wedding_budget_editor_add_haldi").click().run()
    custom = next(e for e in app.session_state[WORKING_KEY].expenses if e.category == "custom")
    app.text_input(key="wedding_budget_editor_name_" + custom.key).set_value("Welcome drinks").run()
    app.number_input(key="wedding_budget_editor_allocation_" + custom.key).set_value(1500).run()
    app.button(key="wedding_budget_editor_remove_haldi:catering").click().run()
    app.button(key="wedding_budget_recalculate_haldi").click().run()
    accept_and_save(app)
    assert any("saved and read back" in s.value for s in app.success)
    with (patch("ai_event_planning_platform.home.logout_user", return_value=True),
          patch("ai_event_planning_platform.home.login_user", return_value=AuthSessionTokens("a", "r"))):
        app.button(key="logout").click().run()
        assert WORKING_KEY not in app.session_state
        app.text_input(key="login_email").set_value("planner@example.invalid")
        app.text_input(key="login_password").set_value("not-stored")
        app.button(key="login_submit").click().run()
        app.button(key=f"continue_wedding_project_{row['project_id']}").click().run()
    app.button(key="wedding_dashboard_budget").click().run()
    app.button(key="wedding_dashboard_edit_budget").click().run()
    assert app.text_input(key="wedding_budget_editor_name_" + custom.key).value == "Welcome drinks"
    assert app.number_input(key="wedding_budget_editor_allocation_" + custom.key).value == 1500
    assert not any(w.key == "wedding_budget_editor_name_haldi:catering" for w in app.text_input)
    assert app.button(key="wedding_budget_editor_undo_haldi:catering").label.startswith("Undo removal")
    second = repo.create_wedding_project("Other", profile(), [ceremony("reception")], planning_step="smart_wedding_budget")
    app.button(key="open_wedding_projects").click().run()
    app.button(key=f"continue_wedding_project_{second['project_id']}").click().run()
    assert WORKING_KEY not in app.session_state
    assert BUDGET_DRAFT_KEY not in app.session_state
    assert not app.exception


def test_failed_save_retains_edited_rows_and_previously_saved_budget(environment):
    repo, create, _open, _ai = environment
    row, app = create(ceremonies=[ceremony(budget=200000)])
    generate_and_review(app)
    accept_and_save(app)
    previous = app.session_state[BUDGET_SAVED_KEY]
    app.text_input(key="wedding_budget_editor_name_haldi:catering").set_value("Breakfast buffet").run()
    app.button(key="wedding_budget_recalculate_haldi").click().run()
    edited = app.session_state[BUDGET_DRAFT_KEY]
    with patch.object(repo, "save_budget_plan", side_effect=RuntimeError("failed readback")):
        accept_and_save(app)
    assert app.session_state[BUDGET_DRAFT_KEY] == edited
    assert app.session_state[BUDGET_SAVED_KEY] == previous
    assert app.text_input(key="wedding_budget_editor_name_haldi:catering").value == "Breakfast buffet"
    assert not app.success
    assert repo.load_selected_project(row["project_id"])["wedding_profile"]["smart_budget_plan"]["expenses"][0]["name"] is None
    accept_and_save(app)
    assert any("saved and read back" in s.value for s in app.success)
    assert not app.exception
