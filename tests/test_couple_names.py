from copy import deepcopy
from dataclasses import replace
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from ai_event_planning_platform.wedding_profile import WeddingProfile, validate_wedding_profile
from ai_event_planning_platform.wedding_project_state import (
    PENDING_NAMES_KEY, PROFILE_SAVED_KEY, PROJECT_DIRTY_KEY, persist_couple_names,
    reload_couple_names, restore_project_workspace,
)
from ai_event_planning_platform.wedding_projects import WeddingProjectRepository, profile_from_json, profile_to_json
from ai_event_planning_platform.wedding_timeline import fallback_timeline
from test_budget_engine import profile, reviewed_plan
from test_wedding_projects import FakeClient, FakeQuery, OWNER_ONE, OWNER_TWO
from test_wedding_timeline import dated_ceremony


def setup_project():
    client = FakeClient()
    repo = WeddingProjectRepository(client, OWNER_ONE)
    p, cs = profile(), [dated_ceremony()]
    row = repo.create_wedding_project("Keep my custom project name", p, cs)
    budget = replace(reviewed_plan(p, cs), accepted=True)
    repo.save_budget_plan(row["project_id"], budget)
    repo.save_timeline_plan(row["project_id"], replace(fallback_timeline(p, cs, budget), accepted=True))
    return repo, client, row


def test_optional_names_roundtrip_legacy_and_validation():
    assert profile_from_json({}).bride_name is None
    p = WeddingProfile(bride_name="Priya", groom_name="Rahul")
    assert profile_from_json(profile_to_json(p)) == p
    assert not validate_wedding_profile(WeddingProfile(bride_name="Priya"))
    for value in (123, "x" * 121):
        assert validate_wedding_profile(WeddingProfile(bride_name=value))
        with pytest.raises(ValueError):
            profile_from_json({"groom_name": value})


def test_new_project_names_require_verified_database_readback():
    repo = WeddingProjectRepository(FakeClient(), OWNER_ONE)
    with patch.object(repo, "load_selected_project", return_value=None):
        with pytest.raises(RuntimeError, match="read back"):
            repo.create_wedding_project("New", WeddingProfile(bride_name="A", groom_name="B"), [])


def test_name_only_save_preserves_other_profile_data_budget_timeline_and_project_title():
    repo, client, row = setup_project()
    before = deepcopy(client.rows[0])
    saved = repo.save_couple_names(row["project_id"], " Priya ", "Rahul", expected_names=(None, None))
    assert saved["project_name"] == before["project_name"]
    assert saved["selected_ceremonies"] == before["selected_ceremonies"]
    assert {k: v for k, v in saved["wedding_profile"].items() if k not in {"bride_name", "groom_name"}} == {k: v for k, v in before["wedding_profile"].items() if k not in {"bride_name", "groom_name"}}
    state = {}
    restore_project_workspace(state, saved)
    assert state[PROFILE_SAVED_KEY].bride_name == "Priya"
    assert state["wedding_budget_saved_snapshot"].accepted and state["wedding_timeline_saved_snapshot"].accepted
    repo.save_couple_names(row["project_id"], "", "", expected_names=("Priya", "Rahul"))
    assert repo.load_selected_project(row["project_id"])["wedding_profile"]["bride_name"] is None


def test_names_owner_and_project_isolation():
    repo, client, row = setup_project()
    other = repo.create_wedding_project("Other", WeddingProfile(), [])
    with pytest.raises(RuntimeError, match="unavailable"):
        WeddingProjectRepository(client, OWNER_TWO).save_couple_names(row["project_id"], "A", "B", expected_names=(None, None))
    repo.save_couple_names(row["project_id"], "A", "B", expected_names=(None, None))
    assert repo.load_selected_project(other["project_id"])["wedding_profile"]["bride_name"] is None


@pytest.mark.parametrize("kind", ["version_race", "response", "readback", "name_race"])
def test_concurrency_and_verification_failures_are_not_success(monkeypatch, kind):
    repo, client, row = setup_project()
    before, execute = deepcopy(client.rows[0]), FakeQuery.execute
    if kind == "readback":
        with patch.object(repo, "load_selected_project", side_effect=[before, None]):
            with pytest.raises(RuntimeError, match="read back"):
                repo.save_couple_names(row["project_id"], "A", "B", expected_names=(None, None))
        return
    if kind == "name_race":
        repo.save_couple_names(row["project_id"], "Other A", "Other B", expected_names=(None, None))
        with pytest.raises(ValueError, match="another session"):
            repo.save_couple_names(row["project_id"], "A", "B", expected_names=(None, None))
        return
    def respond(query):
        if query.operation == "update":
            if kind == "response":
                return SimpleNamespace(data=[before])
            client.rows[0]["updated_at"] = "2026-10-20T00:00:00Z"
        return execute(query)
    monkeypatch.setattr(FakeQuery, "execute", respond)
    with pytest.raises(RuntimeError):
        repo.save_couple_names(row["project_id"], "A", "B", expected_names=(None, None))


def test_failed_name_saves_keep_pending_entries_and_allow_explicit_comparison(monkeypatch):
    repo, _client, row = setup_project()
    state = {}
    restore_project_workspace(state, repo.load_selected_project(row["project_id"]))
    monkeypatch.setattr("ai_event_planning_platform.wedding_project_state.project_repository_from_state", lambda s: repo)
    repo.save_couple_names(row["project_id"], "Other", "Saved", expected_names=(None, None))
    with pytest.raises(ValueError):
        persist_couple_names(state, "Priya", "Rahul")
    assert state[PENDING_NAMES_KEY] == ("Priya", "Rahul") and state[PROJECT_DIRTY_KEY]
    reload_couple_names(state)
    assert state[PROFILE_SAVED_KEY].bride_name == "Other" and state[PENDING_NAMES_KEY] == ("Priya", "Rahul")
    persist_couple_names(state, "Priya", "Rahul")
    assert PROJECT_DIRTY_KEY not in state and PENDING_NAMES_KEY not in state


def test_actual_supabase_sdk_preserves_extensions_and_verifies_names_with_user_token():
    import json
    from uuid import uuid4
    import httpx
    from supabase import create_client
    from supabase.lib.client_options import SyncClientOptions

    project_id = str(uuid4())
    raw = {**profile_to_json(profile()), "unrelated_extension": {"version": 9, "keep": True}}
    stored = {"project_id": project_id, "owner_id": OWNER_ONE, "project_name": "Keep this name",
        "wedding_profile": raw, "selected_ceremonies": [], "updated_at": "2026-10-20T00:00:00Z"}
    methods = []
    def respond(request):
        methods.append(request.method)
        assert request.headers["Authorization"] == "Bearer test-user-access"
        assert request.url.params["owner_id"] == "eq." + OWNER_ONE
        assert request.url.params["project_id"] == "eq." + project_id
        if request.method == "PATCH":
            assert request.url.params["updated_at"] == "eq.2026-10-20T00:00:00Z"
            payload = json.loads(request.content)
            assert set(payload) == {"wedding_profile"}
            assert payload["wedding_profile"]["unrelated_extension"] == raw["unrelated_extension"]
            stored.update(payload)
        return httpx.Response(200, json=[stored])
    with httpx.Client(transport=httpx.MockTransport(respond)) as transport:
        client = create_client("https://project.example.supabase.co", "publishable-test-key",
            options=SyncClientOptions(httpx_client=transport, auto_refresh_token=False, persist_session=False))
        client.postgrest.auth("test-user-access")
        saved = WeddingProjectRepository(client, OWNER_ONE).save_couple_names(project_id, "Priya", "Rahul", expected_names=(None, None))
    assert methods == ["GET", "PATCH", "GET"] and saved["project_name"] == "Keep this name"
