from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest

from ai_event_planning_platform.budget_engine import budget_plan_from_json, budget_plan_to_json
from ai_event_planning_platform.wedding_project_state import (
    BUDGET_DRAFT_KEY, BUDGET_ERROR_KEY, BUDGET_SAVED_KEY, PENDING_BUDGET_SAVE_KEY,
    PROJECT_DIRTY_KEY, persist_active_project, persist_budget_plan, restore_project_workspace,
)
from ai_event_planning_platform.wedding_projects import WeddingProjectRepository, ceremonies_from_json, ceremonies_to_json, profile_to_json
from test_budget_engine import ceremony, profile, reviewed_plan
from test_wedding_projects import FakeClient, FakeQuery, OWNER_ONE, OWNER_TWO


def setup_project():
    client = FakeClient()
    repo = WeddingProjectRepository(client, OWNER_ONE)
    p, ceremonies = profile(), [ceremony(), ceremony("reception")]
    row = repo.create_wedding_project("Our wedding", p, ceremonies)
    return repo, client, row, replace(reviewed_plan(p, ceremonies), accepted=True)


def test_budget_save_readback_restores_without_changing_profile_or_ceremony_plans():
    repo, _client, row, plan = setup_project()
    before = deepcopy(row)
    saved = repo.save_budget_plan(row["project_id"], plan)
    assert saved["selected_ceremonies"] == before["selected_ceremonies"]
    assert {k: v for k, v in saved["wedding_profile"].items() if k != "smart_budget_plan"} == before["wedding_profile"]
    assert budget_plan_from_json(saved["wedding_profile"]["smart_budget_plan"]) == plan
    fresh_state = {}
    restore_project_workspace(fresh_state, repo.load_selected_project(row["project_id"]))
    assert fresh_state[BUDGET_SAVED_KEY] == fresh_state[BUDGET_DRAFT_KEY] == plan
    assert fresh_state["page"] == "smart_wedding_budget"


def test_profile_and_ceremony_saves_preserve_accepted_budget_extension():
    repo, _client, row, plan = setup_project()
    repo.save_budget_plan(row["project_id"], plan)
    repo.update_project(row["project_id"], profile=profile(city="Pune"))
    c = ceremonies_from_json(row["selected_ceremonies"])[0]
    repo.save_ceremony_plan(row["project_id"], replace(c.plan, guest_count=45))
    reloaded = repo.load_selected_project(row["project_id"])
    assert budget_plan_from_json(reloaded["wedding_profile"]["smart_budget_plan"]) == plan
    assert reloaded["wedding_profile"]["city"] == "Pune"


def test_multiple_projects_and_owners_cannot_share_or_steal_saved_budgets():
    repo, client, row, plan = setup_project()
    second = repo.create_wedding_project("Other project", profile(), [ceremony()])
    repo.save_budget_plan(row["project_id"], plan)
    assert "smart_budget_plan" not in repo.load_selected_project(second["project_id"])["wedding_profile"]
    with pytest.raises(RuntimeError, match="unavailable"):
        WeddingProjectRepository(client, OWNER_TWO).save_budget_plan(row["project_id"], plan)
    state = {}
    restore_project_workspace(state, repo.load_selected_project(row["project_id"]))
    assert BUDGET_SAVED_KEY in state
    restore_project_workspace(state, second)
    assert BUDGET_SAVED_KEY not in state and BUDGET_DRAFT_KEY not in state


@pytest.mark.parametrize("kind", ["unaccepted", "unreviewed", "stale", "over_budget"])
def test_invalid_acceptance_never_writes_budget(kind):
    repo, _client, row, plan = setup_project()
    if kind == "unaccepted":
        plan = replace(plan, accepted=False)
    elif kind == "unreviewed":
        plan = replace(plan, needs=tuple(replace(n, reviewed=False) for n in plan.needs))
    elif kind == "stale":
        repo.update_project(row["project_id"], profile=profile(city="Pune"))
    else:
        plan = replace(plan, contingency_inr=2000000)
    with pytest.raises(ValueError):
        repo.save_budget_plan(row["project_id"], plan)
    assert "smart_budget_plan" not in row["wedding_profile"]


@pytest.mark.parametrize("bad_readback", [None, "different_budget"])
def test_success_requires_a_matching_separate_database_readback(bad_readback):
    repo, _client, row, plan = setup_project()
    initial = deepcopy(row)
    wrong = deepcopy(initial)
    with patch.object(repo, "load_selected_project", side_effect=[initial, None if bad_readback is None else wrong]):
        with pytest.raises(RuntimeError, match="read back"):
            repo.save_budget_plan(row["project_id"], plan)


def test_stale_update_response_is_not_reported_as_saved(monkeypatch):
    repo, _client, row, plan = setup_project()
    original, before = FakeQuery.execute, deepcopy(row)

    def stale(query):
        return SimpleNamespace(data=[before]) if query.operation == "update" else original(query)

    monkeypatch.setattr(FakeQuery, "execute", stale)
    with pytest.raises(RuntimeError, match="response could not be verified"):
        repo.save_budget_plan(row["project_id"], plan)


def test_concurrent_change_rejects_budget_without_overwriting_latest_data(monkeypatch):
    repo, client, row, plan = setup_project()
    original = FakeQuery.execute

    def concurrent(query):
        if query.operation == "update":
            client.rows[0]["updated_at"] = "2026-10-17T12:00:00Z"
            client.rows[0]["wedding_profile"]["city"] = "Mumbai"
        return original(query)

    monkeypatch.setattr(FakeQuery, "execute", concurrent)
    with pytest.raises(RuntimeError, match="changed"):
        repo.save_budget_plan(row["project_id"], plan)
    assert row["wedding_profile"]["city"] == "Mumbai"
    assert "smart_budget_plan" not in row["wedding_profile"]


def test_failed_budget_save_stays_dirty_through_navigation_and_retries(monkeypatch):
    repo, _client, row, plan = setup_project()
    state = {}
    restore_project_workspace(state, row)
    monkeypatch.setattr("ai_event_planning_platform.wedding_project_state.project_repository_from_state", lambda state: repo)
    with patch.object(repo, "save_budget_plan", side_effect=RuntimeError("failed")):
        with pytest.raises(RuntimeError):
            persist_budget_plan(state, plan)
    persist_active_project(state, planning_step="step_by_step")
    assert state[PROJECT_DIRTY_KEY] and state[PENDING_BUDGET_SAVE_KEY] == plan
    assert BUDGET_SAVED_KEY not in state
    persist_budget_plan(state, plan)
    assert not state.get(PROJECT_DIRTY_KEY)
    assert state[BUDGET_SAVED_KEY] == plan


def test_legacy_and_malformed_budget_do_not_lose_existing_wedding_details():
    repo, _client, row, _plan = setup_project()
    state = {}
    restore_project_workspace(state, row)
    assert BUDGET_SAVED_KEY not in state
    row["wedding_profile"]["smart_budget_plan"] = {"version": 99}
    restore_project_workspace(state, row)
    assert BUDGET_ERROR_KEY in state
    assert state["wedding_ceremonies_saved_snapshot"][0].plan is not None
    assert repo.load_selected_project(row["project_id"])["wedding_profile"]["budget_inr"] == 1000000


def test_real_supabase_sdk_sends_versioned_jsonb_and_owner_scoped_readback():
    import httpx
    from supabase import create_client
    from supabase.lib.client_options import SyncClientOptions

    p, ceremonies = profile(), [ceremony()]
    plan = replace(reviewed_plan(p, ceremonies), accepted=True)
    project_id = str(uuid4())
    stored = {"project_id": project_id, "owner_id": OWNER_ONE,
        "wedding_profile": profile_to_json(p), "selected_ceremonies": ceremonies_to_json(ceremonies),
        "updated_at": "2026-10-17T12:00:00Z"}
    methods = []

    def respond(request):
        methods.append(request.method)
        assert request.url.params["owner_id"] == f"eq.{OWNER_ONE}"
        assert request.url.params["project_id"] == f"eq.{project_id}"
        assert request.headers["Authorization"] == "Bearer test-user-access"
        if request.method == "PATCH":
            assert request.url.params["updated_at"] == "eq.2026-10-17T12:00:00Z"
            payload = json.loads(request.content)
            assert set(payload) == {"wedding_profile", "planning_step"}
            assert payload["wedding_profile"]["smart_budget_plan"] == budget_plan_to_json(plan)
            stored.update(payload)
        return httpx.Response(200, json=[stored])

    with httpx.Client(transport=httpx.MockTransport(respond)) as transport:
        client = create_client("https://project.example.supabase.co", "test-publishable-key",
            options=SyncClientOptions(httpx_client=transport, auto_refresh_token=False, persist_session=False))
        client.postgrest.auth("test-user-access")
        result = WeddingProjectRepository(client, OWNER_ONE).save_budget_plan(project_id, plan)
    assert methods == ["GET", "PATCH", "GET"]
    assert result["selected_ceremonies"] == ceremonies_to_json(ceremonies)
