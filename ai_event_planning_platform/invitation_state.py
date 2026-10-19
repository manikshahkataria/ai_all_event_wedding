"""Project-isolated invitation workspaces and verified card-level saves."""

from ai_event_planning_platform.invitations import Invitation, invitation_to_json, invitations_from_json, new_invitation
from ai_event_planning_platform.wedding_ceremonies import CEREMONIES_SAVED_KEY
from ai_event_planning_platform.wedding_project_state import (
    ACTIVE_PROJECT_ID_KEY, LOADED_PROJECT_ID_KEY, PROFILE_SAVED_KEY, BUDGET_SAVED_KEY, TIMELINE_SAVED_KEY,
    BUDGET_ERROR_KEY, TIMELINE_ERROR_KEY, INVITATIONS_SAVED_KEY, INVITATIONS_ERROR_KEY,
    PENDING_SAVE_KEY, PENDING_PLAN_SAVES_KEY, PENDING_BUDGET_SAVE_KEY, PENDING_TIMELINE_SAVE_KEY,
    PENDING_NAMES_KEY, PENDING_INVITATION_KEY, PROJECT_DIRTY_KEY, PROJECT_ERROR_KEY,
    project_repository_from_state, restore_project_workspace,
)

WORKSPACE_KEY = "wedding_invitation_workspace"
ACTIVE_CARD_KEY = "wedding_invitation_active_card"
STAGE_KEY = "wedding_invitation_stage"
PHOTOS_KEY = "wedding_invitation_photos"
SOURCE_ROUTE_KEY = "wedding_invitation_source_route"


def active_card(state, ceremonies=()):
    workspace = {key: value for key, value in state.get(WORKSPACE_KEY, {}).items()
                 if isinstance(value, Invitation) and key == value.invitation_id}
    state[WORKSPACE_KEY] = workspace
    key = state.get(ACTIVE_CARD_KEY)
    if key not in workspace:
        saved = state.get(INVITATIONS_SAVED_KEY, {})
        card = next(iter(workspace.values()), None) or next(iter(saved.values()), None) or new_invitation(ceremonies)
        workspace[card.invitation_id] = card
        state[ACTIVE_CARD_KEY] = card.invitation_id
        state[WORKSPACE_KEY] = workspace
        state.setdefault("wedding_invitation_initial", {})[card.invitation_id] = card
        return card
    return workspace[key]


def save_invitation_draft(state, card):
    project_id = state.get(ACTIVE_PROJECT_ID_KEY)
    if not isinstance(project_id, str) or state.get(LOADED_PROJECT_ID_KEY) != project_id:
        raise ValueError("Open the active wedding before saving an invitation.")
    if any(state.get(k) for k in (PENDING_SAVE_KEY, PENDING_PLAN_SAVES_KEY, PENDING_BUDGET_SAVE_KEY, PENDING_TIMELINE_SAVE_KEY, PENDING_NAMES_KEY)):
        raise ValueError("Save other pending project changes first.")
    state[PENDING_INVITATION_KEY] = card
    try:
        baseline = state.get(INVITATIONS_SAVED_KEY, {}).get(card.invitation_id)
        saved = project_repository_from_state(state).save_invitation(project_id, card,
            expected_invitation=invitation_to_json(baseline) if baseline else None)
        cards = invitations_from_json(saved.get("wedding_profile", {}).get("wedding_invitations"))
        stored = next((c for c in cards if c.invitation_id == card.invitation_id), None)
        if saved.get("project_id") != project_id or stored != card:
            raise RuntimeError("The saved invitation could not be verified.")
    except Exception:
        state[PROJECT_DIRTY_KEY] = True
        raise
    state[INVITATIONS_SAVED_KEY] = {c.invitation_id: c for c in cards}
    for key in (PENDING_INVITATION_KEY, PROJECT_DIRTY_KEY, PROJECT_ERROR_KEY, INVITATIONS_ERROR_KEY):
        state.pop(key, None)
    return saved


def _load_invitation_context(state):
    project_id = state.get(ACTIVE_PROJECT_ID_KEY)
    if not isinstance(project_id, str) or state.get(LOADED_PROJECT_ID_KEY) != project_id:
        raise ValueError("Open a wedding project first.")
    row = project_repository_from_state(state).load_selected_project(project_id)
    if row is None or row.get("project_id") != project_id:
        raise RuntimeError("The wedding project is unavailable.")
    fresh = {}
    restore_project_workspace(fresh, row)
    return fresh


def refresh_invitation_sources(state):
    """Read saved source facts without replacing drafts or save-conflict baselines."""
    fresh = _load_invitation_context(state)
    for key in (PROFILE_SAVED_KEY, CEREMONIES_SAVED_KEY, TIMELINE_SAVED_KEY, TIMELINE_ERROR_KEY):
        if key in fresh:
            state[key] = fresh[key]
        else:
            state.pop(key, None)


def refresh_invitation_sources_on_entry(state, route):
    """Refresh once per studio entry, never during ordinary editor reruns.

    The caller supplies every rendered route so native navigation and returning
    to the same project are detected without clearing any invitation work.
    Record a failed attempt too: a failed network read should not cause repeated
    automatic reads on each widget interaction.
    """
    marker = (route, state.get(ACTIVE_PROJECT_ID_KEY))
    previous = state.get(SOURCE_ROUTE_KEY)
    state[SOURCE_ROUTE_KEY] = marker
    if route != "wedding_invitations" or marker == previous:
        return False
    refresh_invitation_sources(state)
    return True


def refresh_invitation_context(state):
    """Explicit comparison also adopts the latest saved invitation baseline."""
    fresh = _load_invitation_context(state)
    for key in (PROFILE_SAVED_KEY, CEREMONIES_SAVED_KEY, BUDGET_SAVED_KEY, TIMELINE_SAVED_KEY,
                INVITATIONS_SAVED_KEY, INVITATIONS_ERROR_KEY, BUDGET_ERROR_KEY, TIMELINE_ERROR_KEY):
        if key in fresh:
            state[key] = fresh[key]
        else:
            state.pop(key, None)
