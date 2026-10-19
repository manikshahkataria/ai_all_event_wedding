from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from streamlit.testing.v1 import AppTest

from ai_event_planning_platform.auth import ACCESS_TOKEN_KEY, REFRESH_TOKEN_KEY, AuthSessionTokens, store_auth_session
from ai_event_planning_platform.home import DELETE_TARGET_KEY
from ai_event_planning_platform.wedding_project_state import (
    ACTIVE_PROJECT_ID_KEY, BUDGET_DRAFT_KEY, LOADED_PROJECT_ID_KEY, PROFILE_SAVED_KEY,
    delete_project_from_state, restore_project_workspace,
)
from ai_event_planning_platform.wedding_profile import WeddingDateStatus, WeddingProfile
from ai_event_planning_platform.wedding_projects import WeddingProjectRepository, profile_to_json
from test_wedding_projects import FakeClient, FakeQuery, OWNER_ONE, OWNER_TWO


@pytest.fixture
def deletion_environment(monkeypatch):
    client = FakeClient()
    repo = WeddingProjectRepository(client, OWNER_ONE)
    monkeypatch.setattr("ai_event_planning_platform.home.restore_authenticated_session", lambda a, r: AuthSessionTokens(a, r))
    monkeypatch.setattr("ai_event_planning_platform.home.project_repository_from_state", lambda state: repo)
    monkeypatch.setattr("ai_event_planning_platform.wedding_project_state.project_repository_from_state", lambda state: repo)

    def dashboard():
        app = AppTest.from_file("../app.py", default_timeout=10)
        store_auth_session(app.session_state, AuthSessionTokens("mock-access", "mock-refresh"))
        app.session_state["page"] = "wedding_projects"
        return app.run()

    return repo, client, dashboard


def create(repo, name="Our wedding", city="Jaipur"):
    from datetime import date
    return repo.create_wedding_project(name, WeddingProfile(city=city, state="Rajasthan",
        wedding_date_status=WeddingDateStatus.FIXED_DATE, wedding_date=date(2027, 11, 20)), [],
        planning_step="step_by_step")


def confirm(app, project):
    app.checkbox(key=f"project_delete_confirm_{project['project_id']}").check().run()
    return app.button(key=f"project_delete_execute_{project['project_id']}").click().run()


def test_repository_requires_explicit_confirmation_before_any_request():
    repo = WeddingProjectRepository(FakeClient(), OWNER_ONE)
    with patch.object(repo, "load_selected_project") as load:
        for confirmed in (False, None, 1, "yes"):
            with pytest.raises(ValueError, match="Explicit confirmation"):
                repo.delete_wedding_project(str(uuid4()), confirmed=confirmed)
        load.assert_not_called()


def test_repository_deletes_only_selected_uuid_even_with_duplicate_names():
    client = FakeClient()
    repo = WeddingProjectRepository(client, OWNER_ONE)
    first, second = create(repo, "Same name"), create(repo, "Same name", city="Manali")
    foreign = create(WeddingProjectRepository(client, OWNER_TWO), "Same name")
    repo.delete_wedding_project(second["project_id"], confirmed=True)
    assert repo.load_selected_project(second["project_id"]) is None
    assert repo.load_selected_project(first["project_id"]) == first
    assert {r["project_id"] for r in client.rows} == {first["project_id"], foreign["project_id"]}


def test_foreign_project_cannot_be_deleted():
    client = FakeClient()
    project = create(WeddingProjectRepository(client, OWNER_ONE))
    with pytest.raises(RuntimeError, match="unavailable"):
        WeddingProjectRepository(client, OWNER_TWO).delete_wedding_project(project["project_id"], confirmed=True)
    assert len(client.rows) == 1


def test_stale_dialog_version_rejects_delete():
    repo = WeddingProjectRepository(FakeClient(), OWNER_ONE)
    row = create(repo)
    with pytest.raises(RuntimeError, match="changed"):
        repo.delete_wedding_project(row["project_id"], confirmed=True, expected_updated_at="old-version")
    assert repo.load_selected_project(row["project_id"]) is not None


def test_concurrent_edit_prevents_database_delete(monkeypatch):
    client = FakeClient()
    repo = WeddingProjectRepository(client, OWNER_ONE)
    row = create(repo)
    execute = FakeQuery.execute

    def race(query):
        if query.operation == "delete":
            client.rows[0]["updated_at"] = "2026-10-17T20:00:00Z"
        return execute(query)

    monkeypatch.setattr(FakeQuery, "execute", race)
    with pytest.raises(RuntimeError, match="could not be confirmed"):
        repo.delete_wedding_project(row["project_id"], confirmed=True)
    assert len(client.rows) == 1


@pytest.mark.parametrize("kind", ["still_present", "wrong_response", "readback_error"])
def test_deletion_is_not_successful_until_readback_verifies_absence(kind, monkeypatch):
    client = FakeClient()
    repo = WeddingProjectRepository(client, OWNER_ONE)
    row = create(repo)
    if kind == "wrong_response":
        execute = FakeQuery.execute
        monkeypatch.setattr(FakeQuery, "execute", lambda q: SimpleNamespace(data=[]) if q.operation == "delete" else execute(q))
        with pytest.raises(RuntimeError, match="could not be confirmed"):
            repo.delete_wedding_project(row["project_id"], confirmed=True)
    else:
        second = deepcopy(row) if kind == "still_present" else RuntimeError("mock network read failure")
        with patch.object(repo, "load_selected_project", side_effect=[deepcopy(row), second]):
            with pytest.raises(RuntimeError):
                repo.delete_wedding_project(row["project_id"], confirmed=True)


def test_each_card_has_delete_and_cancel_never_deletes(deletion_environment):
    repo, _client, dashboard = deletion_environment
    first, second = create(repo), create(repo, city="Manali")
    app = dashboard()
    for row in (first, second):
        assert app.button(key=f"delete_wedding_project_{row['project_id']}").label == "Delete"
    with patch.object(repo, "delete_wedding_project", wraps=repo.delete_wedding_project) as delete:
        app.button(key=f"delete_wedding_project_{second['project_id']}").click().run()
        assert app.button(key=f"project_delete_execute_{second['project_id']}").disabled
        assert any("Manali" in t.value for t in app.text)
        assert any("20 November 2027" in t.value for t in app.text)
        assert any("permanent" in w.value for w in app.warning)
        assert app.session_state[DELETE_TARGET_KEY]["project_id"] == second["project_id"]
        app.checkbox(key=f"project_delete_confirm_{second['project_id']}").check().run()
        delete.assert_not_called()
        app.button(key="project_delete_cancel").click().run()
        delete.assert_not_called()
    assert len(repo.list_user_projects()) == 2
    assert DELETE_TARGET_KEY not in app.session_state
    assert not app.exception


def test_confirmation_removes_correct_duplicate_name_card_and_preserves_auth(deletion_environment):
    repo, _client, dashboard = deletion_environment
    first, second = create(repo, "Same name"), create(repo, "Same name", city="Manali")
    app = dashboard()
    app.button(key=f"delete_wedding_project_{second['project_id']}").click().run()
    confirm(app, second)
    assert any("removal verified" in s.value for s in app.success)
    assert app.button(key=f"delete_wedding_project_{first['project_id']}").label == "Delete"
    assert not any(b.key == f"delete_wedding_project_{second['project_id']}" for b in app.button)
    assert app.session_state[ACCESS_TOKEN_KEY] == "mock-access"
    assert app.session_state[REFRESH_TOKEN_KEY] == "mock-refresh"
    assert repo.load_selected_project(first["project_id"]) is not None
    assert not app.exception


def test_delete_failure_keeps_card_and_active_drafts(deletion_environment):
    repo, _client, dashboard = deletion_environment
    row = create(repo)
    app = dashboard()
    app.button(key=f"continue_wedding_project_{row['project_id']}").click().run()
    app.session_state["wedding_budget_test_draft"] = "unsaved mock draft"
    app.button(key="open_wedding_projects").click().run()
    app.button(key=f"delete_wedding_project_{row['project_id']}").click().run()
    with patch.object(repo, "delete_wedding_project", side_effect=RuntimeError("mock failure")):
        confirm(app, row)
    assert any("Check your connection and retry" in e.value for e in app.error)
    assert not app.success
    assert app.button(key=f"delete_wedding_project_{row['project_id']}").label == "Delete"
    assert app.session_state["wedding_budget_test_draft"] == "unsaved mock draft"
    assert app.session_state[ACTIVE_PROJECT_ID_KEY] == row["project_id"]
    assert repo.load_selected_project(row["project_id"]) is not None
    assert not app.exception


def test_deleting_active_project_clears_its_workspace_without_logout(deletion_environment):
    repo, _client, dashboard = deletion_environment
    row = create(repo)
    other = create(repo, city="Manali")
    app = dashboard()
    app.button(key=f"continue_wedding_project_{row['project_id']}").click().run()
    app.session_state["wedding_budget_test_draft"] = "unsaved mock draft"
    app.button(key="open_wedding_projects").click().run()
    app.button(key=f"delete_wedding_project_{row['project_id']}").click().run()
    confirm(app, row)
    for key in (ACTIVE_PROJECT_ID_KEY, LOADED_PROJECT_ID_KEY, PROFILE_SAVED_KEY, "wedding_budget_test_draft"):
        assert key not in app.session_state
    assert ACCESS_TOKEN_KEY in app.session_state
    assert app.session_state["page"] == "wedding_projects"
    assert repo.load_selected_project(other["project_id"]) is not None
    assert not app.exception


def test_deleting_inactive_project_preserves_active_workspace(deletion_environment):
    repo, _client, _dashboard = deletion_environment
    active, inactive = create(repo), create(repo, city="Manali")
    state = {}
    restore_project_workspace(state, active)
    state[BUDGET_DRAFT_KEY] = "mock active draft"
    delete_project_from_state(state, inactive["project_id"], confirmed=True,
                              expected_updated_at=inactive["updated_at"])
    assert state[ACTIVE_PROJECT_ID_KEY] == active["project_id"]
    assert state[BUDGET_DRAFT_KEY] == "mock active draft"


def test_real_sdk_deletion_uses_user_token_owner_uuid_and_version_then_reads_back():
    import httpx
    from supabase import create_client
    from supabase.lib.client_options import SyncClientOptions

    project_id, calls = str(uuid4()), []
    stored = {"project_id": project_id, "owner_id": OWNER_ONE, "updated_at": "2026-10-17T20:00:00Z",
              "wedding_profile": profile_to_json(WeddingProfile())}
    present = True

    def respond(request):
        nonlocal present
        calls.append(request.method)
        assert request.url.params["project_id"] == f"eq.{project_id}"
        assert request.url.params["owner_id"] == f"eq.{OWNER_ONE}"
        assert request.headers["Authorization"] == "Bearer mock-user-access"
        if request.method == "DELETE":
            assert request.url.params["updated_at"] == "eq.2026-10-17T20:00:00Z"
            present = False
            return httpx.Response(200, json=[stored])
        return httpx.Response(200, json=[stored] if present else [])

    with httpx.Client(transport=httpx.MockTransport(respond)) as transport:
        client = create_client("https://project.example.supabase.co", "mock-publishable-key",
            options=SyncClientOptions(httpx_client=transport, auto_refresh_token=False, persist_session=False))
        client.postgrest.auth("mock-user-access")
        WeddingProjectRepository(client, OWNER_ONE).delete_wedding_project(project_id, confirmed=True)
    assert calls == ["GET", "DELETE", "GET"]
