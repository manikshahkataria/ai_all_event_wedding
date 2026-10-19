from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest

from ai_event_planning_platform.wedding_project_state import (
    BUDGET_SAVED_KEY, PROFILE_SAVED_KEY, TIMELINE_DRAFT_KEY, TIMELINE_SAVED_KEY,
    TIMELINE_ERROR_KEY, PENDING_TIMELINE_SAVE_KEY, PROJECT_DIRTY_KEY,
    persist_active_project, persist_budget_plan, persist_timeline_plan, reload_timeline_context, restore_project_workspace,
)
from ai_event_planning_platform.wedding_projects import WeddingProjectRepository, ceremonies_to_json, profile_to_json
from ai_event_planning_platform.wedding_timeline import fallback_timeline, timeline_from_json, timeline_to_json
from test_budget_engine import profile, reviewed_plan
from test_wedding_projects import FakeClient, FakeQuery, OWNER_ONE, OWNER_TWO
from test_wedding_timeline import dated_ceremony


def setup_project():
    client = FakeClient()
    repo = WeddingProjectRepository(client, OWNER_ONE)
    p, ceremonies = profile(), [dated_ceremony()]
    row = repo.create_wedding_project("Timeline wedding", p, ceremonies)
    budget = replace(reviewed_plan(p, ceremonies), accepted=True)
    repo.save_budget_plan(row["project_id"], budget)
    return repo, client, row, budget, replace(fallback_timeline(p, ceremonies, budget), accepted=True)


def test_roundtrip_preserves_budget_profile_selected_ceremonies_and_other_extensions():
    repo, client, row, budget, plan = setup_project()
    client.rows[0]["wedding_profile"]["unrelated_extension"] = {"version": 4, "value": "keep"}
    before = deepcopy(client.rows[0])
    saved = repo.save_timeline_plan(row["project_id"], plan)
    assert saved["selected_ceremonies"] == before["selected_ceremonies"]
    assert {k: v for k, v in saved["wedding_profile"].items() if k != "wedding_timeline"} == before["wedding_profile"]
    state = {}
    restore_project_workspace(state, saved)
    assert state[TIMELINE_SAVED_KEY] == state[TIMELINE_DRAFT_KEY] == plan
    assert state[BUDGET_SAVED_KEY] == budget
    assert state["page"] == "wedding_timeline"
    repo.update_project(row["project_id"], profile=profile(city="Pune"))
    assert timeline_from_json(repo.load_selected_project(row["project_id"])["wedding_profile"]["wedding_timeline"]) == plan


def test_owner_and_project_isolation_and_legacy_restore():
    repo, client, row, _budget, plan = setup_project()
    second = repo.create_wedding_project("Second", profile(), [dated_ceremony()])
    with pytest.raises(RuntimeError, match="unavailable"):
        WeddingProjectRepository(client, OWNER_TWO).save_timeline_plan(row["project_id"], plan)
    repo.save_timeline_plan(row["project_id"], plan)
    state = {}
    restore_project_workspace(state, repo.load_selected_project(row["project_id"]))
    state["wedding_calendar_selected"] = dated_ceremony().plan.ceremony_date
    restore_project_workspace(state, second)
    assert TIMELINE_SAVED_KEY not in state and TIMELINE_DRAFT_KEY not in state
    assert "wedding_calendar_selected" not in state
    assert "wedding_timeline" not in second["wedding_profile"]


@pytest.mark.parametrize("kind", ["unaccepted", "stale", "forged_date", "invalid_time", "missing_dependency"])
def test_invalid_timeline_cannot_be_saved(kind):
    from datetime import date, time
    repo, client, row, _budget, plan = setup_project()
    if kind == "unaccepted":
        plan = replace(plan, accepted=False)
    elif kind == "stale":
        repo.update_project(row["project_id"], profile=profile(city="Pune"))
    elif kind == "forged_date":
        plan = replace(plan, schedules=(replace(plan.schedules[0], ceremony_date=date(2028, 1, 1)),))
    elif kind == "invalid_time":
        plan = replace(plan, schedules=(replace(plan.schedules[0], start_time=time(12), end_time=time(9)),))
    else:
        plan = replace(plan, tasks=(replace(plan.tasks[0], dependency_ids=("missing",)),))
    with pytest.raises(ValueError):
        repo.save_timeline_plan(row["project_id"], plan)
    assert "wedding_timeline" not in client.rows[0]["wedding_profile"]


@pytest.mark.parametrize("kind", ["no_readback", "wrong_readback", "stale_response", "version_race"])
def test_false_success_and_concurrent_updates_are_rejected(monkeypatch, kind):
    repo, client, row, _budget, plan = setup_project()
    before, execute = deepcopy(client.rows[0]), FakeQuery.execute
    if kind in {"no_readback", "wrong_readback"}:
        results = iter([before, None if kind == "no_readback" else before])
        monkeypatch.setattr(repo, "load_selected_project", lambda _id: next(results))
    else:
        def response(query):
            if query.operation == "update":
                if kind == "stale_response":
                    return SimpleNamespace(data=[before])
                client.rows[0]["updated_at"] = "2026-10-18T00:00:00Z"
                client.rows[0]["wedding_profile"]["concurrent_field"] = "keep"
            return execute(query)
        monkeypatch.setattr(FakeQuery, "execute", response)
    with pytest.raises(RuntimeError):
        repo.save_timeline_plan(row["project_id"], plan)
    if kind == "version_race":
        assert client.rows[0]["wedding_profile"]["concurrent_field"] == "keep"
        assert "wedding_timeline" not in client.rows[0]["wedding_profile"]


def test_existing_concurrent_timeline_requires_comparison_before_overwrite():
    repo, _client, row, _budget, plan = setup_project()
    repo.save_timeline_plan(row["project_id"], plan)
    edited = replace(plan, tasks=(replace(plan.tasks[0], title="My different work"), *plan.tasks[1:]))
    with pytest.raises(ValueError, match="Another session"):
        repo.save_timeline_plan(row["project_id"], edited)
    repo.save_timeline_plan(row["project_id"], edited, expected_timeline=timeline_to_json(plan))


def test_explicit_context_reload_keeps_draft_and_exposes_latest_timeline(monkeypatch):
    repo, _client, row, _budget, plan = setup_project()
    state = {}
    restore_project_workspace(state, repo.load_selected_project(row["project_id"]))
    local = replace(plan, accepted=False, tasks=(replace(plan.tasks[0], title="Local work"),))
    state[TIMELINE_DRAFT_KEY] = local
    state[PENDING_TIMELINE_SAVE_KEY] = replace(local, accepted=True)
    repo.save_timeline_plan(row["project_id"], plan)
    monkeypatch.setattr("ai_event_planning_platform.wedding_project_state.project_repository_from_state", lambda s: repo)
    reload_timeline_context(state)
    assert state[TIMELINE_SAVED_KEY] == plan and state[TIMELINE_DRAFT_KEY] == local
    assert state[PROJECT_DIRTY_KEY]
    persist_timeline_plan(state, replace(local, accepted=True))
    assert state[TIMELINE_SAVED_KEY].tasks[0].title == "Local work"


def test_failed_save_keeps_draft_and_pending_flag_through_other_successes(monkeypatch):
    repo, _client, row, budget, plan = setup_project()
    state = {}
    restore_project_workspace(state, repo.load_selected_project(row["project_id"]))
    state[TIMELINE_DRAFT_KEY] = replace(plan, accepted=False)
    monkeypatch.setattr("ai_event_planning_platform.wedding_project_state.project_repository_from_state", lambda s: repo)
    with patch.object(repo, "save_timeline_plan", side_effect=RuntimeError("failure")):
        with pytest.raises(RuntimeError):
            persist_timeline_plan(state, plan)
    assert state[TIMELINE_DRAFT_KEY] == replace(plan, accepted=False)
    assert state[PENDING_TIMELINE_SAVE_KEY] == plan and state[PROJECT_DIRTY_KEY]
    persist_active_project(state, planning_step="wedding_timeline")
    persist_budget_plan(state, budget)
    assert state[PROJECT_DIRTY_KEY]
    persist_timeline_plan(state, plan)
    assert PROJECT_DIRTY_KEY not in state and PENDING_TIMELINE_SAVE_KEY not in state
    assert state[TIMELINE_SAVED_KEY] == plan


def test_malformed_saved_timeline_is_isolated_and_can_be_replaced_after_review(monkeypatch):
    repo, client, row, budget, plan = setup_project()
    client.rows[0]["wedding_profile"]["wedding_timeline"] = {"version": 99}
    state = {}
    restore_project_workspace(state, repo.load_selected_project(row["project_id"]))
    assert TIMELINE_ERROR_KEY in state and state[BUDGET_SAVED_KEY] == budget
    assert state[PROFILE_SAVED_KEY].budget_inr == 1000000
    monkeypatch.setattr("ai_event_planning_platform.wedding_project_state.project_repository_from_state", lambda s: repo)
    persist_timeline_plan(state, plan)
    assert TIMELINE_ERROR_KEY not in state


def test_supabase_sdk_patch_is_owner_token_version_scoped_with_readback():
    import httpx
    from supabase import create_client
    from supabase.lib.client_options import SyncClientOptions

    p, cs = profile(), [dated_ceremony()]
    plan = replace(fallback_timeline(p, cs), accepted=True)
    project_id = str(uuid4())
    stored = {"project_id": project_id, "owner_id": OWNER_ONE, "wedding_profile": profile_to_json(p),
              "selected_ceremonies": ceremonies_to_json(cs), "updated_at": "2026-10-17T12:00:00Z"}
    methods = []

    def respond(request):
        methods.append(request.method)
        assert request.headers["Authorization"] == "Bearer test-user-access"
        assert request.url.params["owner_id"] == f"eq.{OWNER_ONE}"
        assert request.url.params["project_id"] == f"eq.{project_id}"
        if request.method == "PATCH":
            assert request.url.params["updated_at"] == "eq.2026-10-17T12:00:00Z"
            body = json.loads(request.content)
            assert set(body) == {"wedding_profile", "planning_step"}
            assert body["wedding_profile"]["wedding_timeline"] == timeline_to_json(plan)
            stored.update(body)
        return httpx.Response(200, json=[stored])

    with httpx.Client(transport=httpx.MockTransport(respond)) as transport:
        client = create_client("https://project.example.supabase.co", "test-publishable-key",
            options=SyncClientOptions(httpx_client=transport, auto_refresh_token=False, persist_session=False))
        client.postgrest.auth("test-user-access")
        WeddingProjectRepository(client, OWNER_ONE).save_timeline_plan(project_id, plan)
    assert methods == ["GET", "PATCH", "GET"]
