from copy import deepcopy
from dataclasses import replace
from datetime import date, time
import json
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest

from ai_event_planning_platform.invitations import invitation_to_json, invitations_from_json, new_invitation
from ai_event_planning_platform.invitation_state import (
    ACTIVE_CARD_KEY, WORKSPACE_KEY, PHOTOS_KEY, active_card, save_invitation_draft, refresh_invitation_context,
    refresh_invitation_sources, refresh_invitation_sources_on_entry,
)
from ai_event_planning_platform.wedding_project_state import (
    INVITATIONS_SAVED_KEY, INVITATIONS_ERROR_KEY, PENDING_INVITATION_KEY, PROJECT_DIRTY_KEY,
    PROFILE_SAVED_KEY, persist_active_project, restore_project_workspace,
    TIMELINE_SAVED_KEY,
)
from ai_event_planning_platform.wedding_projects import WeddingProjectRepository, ceremonies_to_json, profile_to_json
from ai_event_planning_platform.wedding_timeline import fallback_timeline, timeline_to_json
from ai_event_planning_platform.wedding_ceremonies import CEREMONIES_SAVED_KEY
from ai_event_planning_platform.wedding_profile import WeddingDateStatus
from ai_event_planning_platform.wedding_form import DRAFT_STATE_KEY
from test_budget_engine import reviewed_plan
from test_invitations import ready_card
from test_wedding_projects import FakeClient, FakeQuery, OWNER_ONE, OWNER_TWO


def setup_project():
    client = FakeClient()
    repo = WeddingProjectRepository(client, OWNER_ONE)
    p, cs, card = ready_card()
    row = repo.create_wedding_project("Our custom project name", p, cs)
    budget = replace(reviewed_plan(p, cs), accepted=True)
    repo.save_budget_plan(row["project_id"], budget)
    repo.save_timeline_plan(row["project_id"], replace(fallback_timeline(p, cs, budget), accepted=True))
    return repo, client, row, card


def test_multiple_cards_preserve_all_existing_project_data_and_reopen():
    repo, client, row, first = setup_project()
    before = deepcopy(client.rows[0])
    repo.save_invitation(row["project_id"], first)
    second = replace(new_invitation(), invitation_type="bride_family", family={"hosts": "Bride-side hosts"})
    saved = repo.save_invitation(row["project_id"], second)
    assert invitations_from_json(saved["wedding_profile"]["wedding_invitations"]) == (first, second)
    assert saved["selected_ceremonies"] == before["selected_ceremonies"] and saved["project_name"] == before["project_name"]
    assert {k: v for k, v in saved["wedding_profile"].items() if k != "wedding_invitations"} == before["wedding_profile"]
    state = {}
    restore_project_workspace(state, saved)
    assert state[INVITATIONS_SAVED_KEY] == {first.invitation_id: first, second.invitation_id: second}
    assert active_card(state).invitation_id == first.invitation_id


def test_other_owners_projects_and_session_photos_are_isolated():
    repo, client, row, card = setup_project()
    other = repo.create_wedding_project("Other wedding", ready_card()[0], [])
    with pytest.raises(RuntimeError, match="unavailable"):
        WeddingProjectRepository(client, OWNER_TWO).save_invitation(row["project_id"], card)
    repo.save_invitation(row["project_id"], card)
    assert "wedding_invitations" not in repo.load_selected_project(other["project_id"])["wedding_profile"]
    state = {}
    restore_project_workspace(state, repo.load_selected_project(row["project_id"]))
    active_card(state)
    state[PHOTOS_KEY] = {card.invitation_id: b"local photo bytes"}
    restore_project_workspace(state, other)
    assert not state[INVITATIONS_SAVED_KEY] and PHOTOS_KEY not in state and WORKSPACE_KEY not in state


def test_same_card_concurrency_is_rejected_but_independent_cards_merge():
    repo, _client, row, card = setup_project()
    repo.save_invitation(row["project_id"], card)
    changed = replace(card, wording="My reviewed wording")
    with pytest.raises(ValueError, match="another session"):
        repo.save_invitation(row["project_id"], changed)
    repo.save_invitation(row["project_id"], changed, expected_invitation=invitation_to_json(card))
    other = new_invitation()
    saved = repo.save_invitation(row["project_id"], other)
    assert invitations_from_json(saved["wedding_profile"]["wedding_invitations"]) == (changed, other)


@pytest.mark.parametrize("kind", ("response", "readback", "race"))
def test_verification_and_version_failures_never_report_success(monkeypatch, kind):
    repo, client, row, card = setup_project()
    before, execute = deepcopy(client.rows[0]), FakeQuery.execute
    if kind == "readback":
        with patch.object(repo, "load_selected_project", side_effect=[before, None]):
            with pytest.raises(RuntimeError, match="read back"):
                repo.save_invitation(row["project_id"], card)
        # A retry after an uncertain response is idempotent, with no duplicate card.
        saved = repo.save_invitation(row["project_id"], card)
        assert len(saved["wedding_profile"]["wedding_invitations"]["invitations"]) == 1
        return
    def respond(query):
        if query.operation == "update":
            if kind == "response":
                return SimpleNamespace(data=[before])
            client.rows[0]["updated_at"] = "2026-10-20T00:00:00Z"
            client.rows[0]["wedding_profile"]["concurrent_field"] = "preserve"
        return execute(query)
    monkeypatch.setattr(FakeQuery, "execute", respond)
    with pytest.raises(RuntimeError):
        repo.save_invitation(row["project_id"], card)
    if kind == "race":
        assert client.rows[0]["wedding_profile"]["concurrent_field"] == "preserve"
        assert "wedding_invitations" not in client.rows[0]["wedding_profile"]


def test_failed_saves_retain_drafts_photos_and_dirty_flag_during_other_saves(monkeypatch):
    repo, _client, row, card = setup_project()
    state = {}
    restore_project_workspace(state, repo.load_selected_project(row["project_id"]))
    state[WORKSPACE_KEY], state[ACTIVE_CARD_KEY] = {card.invitation_id: card}, card.invitation_id
    state[PHOTOS_KEY] = {card.invitation_id: b"session-only private image"}
    monkeypatch.setattr("ai_event_planning_platform.invitation_state.project_repository_from_state", lambda s: repo)
    monkeypatch.setattr("ai_event_planning_platform.wedding_project_state.project_repository_from_state", lambda s: repo)
    with patch.object(repo, "save_invitation", side_effect=RuntimeError("readback failure")):
        with pytest.raises(RuntimeError):
            save_invitation_draft(state, card)
    assert state[PENDING_INVITATION_KEY] == card and state[PROJECT_DIRTY_KEY]
    persist_active_project(state, planning_step="wedding_invitations")
    assert state[PROJECT_DIRTY_KEY] and state[PHOTOS_KEY][card.invitation_id]
    save_invitation_draft(state, card)
    assert PROJECT_DIRTY_KEY not in state and PENDING_INVITATION_KEY not in state
    assert "base64" not in json.dumps(repo.load_selected_project(row["project_id"])["wedding_profile"])


def test_legacy_and_malformed_saved_collections_do_not_damage_other_data():
    repo, client, row, card = setup_project()
    state = {}
    restore_project_workspace(state, repo.load_selected_project(row["project_id"]))
    assert state[INVITATIONS_SAVED_KEY] == {}
    client.rows[0]["wedding_profile"]["wedding_invitations"] = {"version": 99}
    restore_project_workspace(state, repo.load_selected_project(row["project_id"]))
    assert INVITATIONS_ERROR_KEY in state and state[PROFILE_SAVED_KEY].bride_name == "Priya"
    assert state["wedding_budget_saved_snapshot"].accepted and state["wedding_timeline_saved_snapshot"].accepted
    with pytest.raises(ValueError):
        repo.save_invitation(row["project_id"], card)
    assert client.rows[0]["wedding_profile"]["wedding_invitations"] == {"version": 99}


def test_explicit_comparison_refresh_keeps_local_overrides_and_photo(monkeypatch):
    repo, _client, row, card = setup_project()
    state = {}
    restore_project_workspace(state, repo.load_selected_project(row["project_id"]))
    local = replace(card, wording="Local wording", overrides={"bride_name": "Card-only name"})
    state[WORKSPACE_KEY] = {card.invitation_id: local}
    state[PHOTOS_KEY] = {card.invitation_id: b"local pixels"}
    remote = replace(card, wording="Remote wording")
    repo.save_invitation(row["project_id"], remote)
    monkeypatch.setattr("ai_event_planning_platform.invitation_state.project_repository_from_state", lambda s: repo)
    refresh_invitation_context(state)
    assert state[INVITATIONS_SAVED_KEY][card.invitation_id] == remote
    assert state[WORKSPACE_KEY][card.invitation_id] == local and state[PHOTOS_KEY][card.invitation_id] == b"local pixels"
    save_invitation_draft(state, local)
    assert state[INVITATIONS_SAVED_KEY][card.invitation_id] == local


def test_timeline_comparison_does_not_clear_unsaved_invitation_work_or_photos(monkeypatch):
    from ai_event_planning_platform.wedding_project_state import reload_timeline_context
    repo, _client, row, card = setup_project()
    state = {}
    restore_project_workspace(state, repo.load_selected_project(row["project_id"]))
    state[WORKSPACE_KEY] = {card.invitation_id: card}
    state[PHOTOS_KEY] = {card.invitation_id: b"local image pixels"}
    monkeypatch.setattr("ai_event_planning_platform.wedding_project_state.project_repository_from_state", lambda s: repo)
    reload_timeline_context(state)
    assert state[WORKSPACE_KEY][card.invitation_id] == card
    assert state[PHOTOS_KEY][card.invitation_id] == b"local image pixels"


def test_studio_entry_refreshes_saved_sources_without_replacing_local_work_or_save_baseline(monkeypatch):
    repo, client, row, card = setup_project()
    repo.save_invitation(row["project_id"], card)
    state = {}
    restore_project_workspace(state, repo.load_selected_project(row["project_id"]))
    local = replace(card, wording="Keep unsaved wording", overrides={"bride_name": "Invitation-only name"})
    local_profile = replace(state[PROFILE_SAVED_KEY], city="Unsaved profile city")
    state[WORKSPACE_KEY], state[ACTIVE_CARD_KEY] = {card.invitation_id: local}, card.invitation_id
    state[PHOTOS_KEY], state[DRAFT_STATE_KEY] = {card.invitation_id: b"private local pixels"}, local_profile
    widget_key = "wedding_invitation_widget_" + card.invitation_id + "_card_wording"
    state[widget_key] = local.wording
    latest = replace(state[PROFILE_SAVED_KEY], bride_name="Esha", groom_name="Vishal", city="Manali",
                     wedding_date_status=WeddingDateStatus.FIXED_DATE, wedding_date=date(2027, 11, 18))
    repo.update_project(row["project_id"], profile=latest)
    latest_plan = replace(state[CEREMONIES_SAVED_KEY][0].plan, ceremony_date=date(2027, 11, 19), venue_notes="New saved ceremony venue")
    repo.save_ceremony_plan(row["project_id"], latest_plan)
    timeline = state[TIMELINE_SAVED_KEY]
    latest_timeline = replace(timeline, schedules=(replace(timeline.schedules[0], start_time=time(14, 30)),))
    client.rows[0]["wedding_profile"]["wedding_timeline"] = timeline_to_json(latest_timeline)
    remote = replace(card, wording="Remote concurrent wording")
    repo.save_invitation(row["project_id"], remote, expected_invitation=invitation_to_json(card))
    monkeypatch.setattr("ai_event_planning_platform.invitation_state.project_repository_from_state", lambda s: repo)
    with patch.object(repo, "load_selected_project", wraps=repo.load_selected_project) as load:
        assert not refresh_invitation_sources_on_entry(state, "wedding_dashboard")
        assert refresh_invitation_sources_on_entry(state, "wedding_invitations")
        assert load.call_count == 1
        assert not refresh_invitation_sources_on_entry(state, "wedding_invitations")
        assert load.call_count == 1
        assert not refresh_invitation_sources_on_entry(state, "wedding_dashboard")
        assert refresh_invitation_sources_on_entry(state, "wedding_invitations")
        assert load.call_count == 2
    assert state[PROFILE_SAVED_KEY] == latest
    assert state[CEREMONIES_SAVED_KEY][0].plan == latest_plan
    assert state[TIMELINE_SAVED_KEY] == latest_timeline
    assert state[WORKSPACE_KEY][card.invitation_id] == local
    assert state[PHOTOS_KEY][card.invitation_id] == b"private local pixels"
    assert state[DRAFT_STATE_KEY] == local_profile and state[widget_key] == local.wording
    assert state[INVITATIONS_SAVED_KEY][card.invitation_id] == card
    with pytest.raises(ValueError, match="another session"):
        save_invitation_draft(state, local)
    assert state[PENDING_INVITATION_KEY] == local


def test_source_refresh_failure_preserves_all_existing_state_and_does_not_loop(monkeypatch):
    repo, _client, row, card = setup_project()
    state = {}
    restore_project_workspace(state, repo.load_selected_project(row["project_id"]))
    state[WORKSPACE_KEY], state[PHOTOS_KEY] = {card.invitation_id: card}, {card.invitation_id: b"private local pixels"}
    before = deepcopy(state)
    monkeypatch.setattr("ai_event_planning_platform.invitation_state.project_repository_from_state", lambda s: repo)
    with patch.object(repo, "load_selected_project", side_effect=RuntimeError("offline")) as load:
        with pytest.raises(RuntimeError, match="offline"):
            refresh_invitation_sources_on_entry(state, "wedding_invitations")
        assert all(state[k] == value for k, value in before.items())
        assert not refresh_invitation_sources_on_entry(state, "wedding_invitations")
        assert load.call_count == 1


def test_source_refresh_rejects_another_project_without_exposing_its_data(monkeypatch):
    repo, _client, row, card = setup_project()
    other = repo.create_wedding_project("Another project", replace(ready_card()[0], bride_name="Other bride"), [])
    state = {}
    restore_project_workspace(state, repo.load_selected_project(row["project_id"]))
    state[WORKSPACE_KEY], state[PHOTOS_KEY] = {card.invitation_id: card}, {card.invitation_id: b"private local pixels"}
    before = deepcopy(state)
    monkeypatch.setattr("ai_event_planning_platform.invitation_state.project_repository_from_state", lambda s: repo)
    with patch.object(repo, "load_selected_project", return_value=other):
        with pytest.raises(RuntimeError, match="unavailable"):
            refresh_invitation_sources(state)
    assert state == before


def test_sdk_uses_owner_token_version_and_independent_readback():
    import httpx
    from supabase import create_client
    from supabase.lib.client_options import SyncClientOptions
    p, cs, card = ready_card()
    project_id = str(uuid4())
    stored = {"project_id": project_id, "owner_id": OWNER_ONE, "project_name": "Wedding",
        "wedding_profile": {**profile_to_json(p), "other_extension": {"keep": True}},
        "selected_ceremonies": ceremonies_to_json(cs), "updated_at": "2026-10-19T12:00:00Z"}
    methods = []
    def respond(request):
        methods.append(request.method)
        assert request.headers["Authorization"] == "Bearer test-access"
        assert request.url.params["owner_id"] == f"eq.{OWNER_ONE}" and request.url.params["project_id"] == f"eq.{project_id}"
        if request.method == "PATCH":
            assert request.url.params["updated_at"] == "eq.2026-10-19T12:00:00Z"
            payload = json.loads(request.content)
            assert set(payload) == {"wedding_profile", "planning_step"}
            assert payload["wedding_profile"]["other_extension"] == {"keep": True}
            assert payload["wedding_profile"]["wedding_invitations"]["invitations"][0] == invitation_to_json(card)
            stored.update(payload)
        return httpx.Response(200, json=[stored])
    with httpx.Client(transport=httpx.MockTransport(respond)) as transport:
        client = create_client("https://project.example.supabase.co", "test-publishable-key",
            options=SyncClientOptions(httpx_client=transport, auto_refresh_token=False, persist_session=False))
        client.postgrest.auth("test-access")
        WeddingProjectRepository(client, OWNER_ONE).save_invitation(project_id, card)
    assert methods == ["GET", "PATCH", "GET"]
