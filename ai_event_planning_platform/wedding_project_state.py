from collections.abc import MutableMapping, Sequence
from dataclasses import replace
from typing import Any

from ai_event_planning_platform.auth import ACCESS_TOKEN_KEY, REFRESH_TOKEN_KEY
from ai_event_planning_platform.navigation import TRAIL_KEY
from ai_event_planning_platform.budget_engine import BudgetPlan, budget_plan_from_json, budget_plan_to_json
from ai_event_planning_platform.wedding_timeline import timeline_from_json, timeline_to_json
from ai_event_planning_platform.invitations import invitations_from_json
from ai_event_planning_platform.ceremony_plan import CeremonyPlan, ceremony_plan_to_json
from ai_event_planning_platform.wedding_ceremonies import (
    CEREMONIES_CONFIRMED_KEY,
    CEREMONIES_DRAFT_IDS_KEY,
    CEREMONIES_SAVED_KEY,
    CEREMONIES_STATE_KEY,
    CUSTOM_CEREMONIES_STATE_KEY,
    Ceremony,
)
from ai_event_planning_platform.wedding_form import DRAFT_SAVED_STATE_KEY, DRAFT_STATE_KEY
from ai_event_planning_platform.wedding_profile import WeddingProfile
from ai_event_planning_platform.wedding_projects import (
    WeddingProjectRepository,
    ceremonies_from_json,
    ceremonies_to_json,
    profile_from_json,
    profile_to_json,
)


ACTIVE_PROJECT_ID_KEY = "active_wedding_project_id"
LOADED_PROJECT_ID_KEY = "loaded_wedding_project_id"
ACTIVE_PROJECT_NAME_KEY = "active_wedding_project_name"
PROJECT_DIRTY_KEY = "active_wedding_project_has_unsaved_changes"
PROJECT_ERROR_KEY = "wedding_project_error"
PROJECT_SUCCESS_KEY = "wedding_project_success"
PROJECT_RETURN_PAGE_KEY = "wedding_project_return_page"
PENDING_SAVE_KEY = "wedding_project_pending_save"
PENDING_PLAN_SAVES_KEY = "wedding_project_pending_plan_saves"
PROFILE_SAVED_KEY = "wedding_profile_saved_snapshot"
BUDGET_SAVED_KEY = "wedding_budget_saved_snapshot"
BUDGET_DRAFT_KEY = "wedding_budget_draft"
BUDGET_ERROR_KEY = "wedding_budget_restore_error"
PENDING_BUDGET_SAVE_KEY = "wedding_budget_pending_save"
TIMELINE_SAVED_KEY = "wedding_timeline_saved_snapshot"
TIMELINE_DRAFT_KEY = "wedding_timeline_draft"
TIMELINE_ERROR_KEY = "wedding_timeline_restore_error"
TIMELINE_BASELINE_KEY = "wedding_timeline_saved_json"
PENDING_TIMELINE_SAVE_KEY = "wedding_timeline_pending_save"
PENDING_NAMES_KEY = "wedding_couple_names_pending"
INVITATIONS_SAVED_KEY = "wedding_invitation_saved_snapshot"
INVITATIONS_ERROR_KEY = "wedding_invitation_restore_error"
PENDING_INVITATION_KEY = "wedding_invitation_pending_save"

PAGE_BY_PLANNING_STEP = {
    "requirements": "wedding_start",
    "wedding_start": "wedding_start",
    "step_by_step": "step_by_step",
    "describe_wedding": "describe_wedding",
    "wedding_followups": "wedding_followups",
    "wedding_ceremonies": "wedding_ceremonies",
    "ceremony_planning": "ceremony_planning",
    "smart_wedding_budget": "smart_wedding_budget",
    "wedding_timeline": "wedding_timeline",
    "wedding_dashboard": "wedding_dashboard",
    "budget_dashboard": "budget_dashboard",
    "wedding_invitations": "wedding_invitations",
}


def project_repository_from_state(
    state: MutableMapping[str, object],
) -> WeddingProjectRepository:
    access_token = state.get(ACCESS_TOKEN_KEY)
    refresh_token = state.get(REFRESH_TOKEN_KEY)
    if not isinstance(access_token, str) or not isinstance(refresh_token, str):
        raise ValueError("An authenticated Supabase session is required.")
    return WeddingProjectRepository.from_session_tokens(access_token, refresh_token)


def clear_project_workspace(
    state: MutableMapping[str, object], *, clear_active_project: bool = False
) -> None:
    for key in tuple(state):
        if key.startswith(("wedding_", "ai_review_wedding_", "custom_ceremony_")):
            state.pop(key, None)
    for key in (
        LOADED_PROJECT_ID_KEY,
        ACTIVE_PROJECT_NAME_KEY,
        PROJECT_DIRTY_KEY,
        PROJECT_RETURN_PAGE_KEY,
        TRAIL_KEY,
    ):
        state.pop(key, None)
    if clear_active_project:
        state.pop(ACTIVE_PROJECT_ID_KEY, None)


def delete_project_from_state(state: MutableMapping[str, object], project_id: str, *,
                              confirmed: bool, expected_updated_at: str | None = None) -> None:
    project_repository_from_state(state).delete_wedding_project(
        project_id, confirmed=confirmed, expected_updated_at=expected_updated_at,
    )
    if state.get(ACTIVE_PROJECT_ID_KEY) == project_id:
        clear_project_workspace(state, clear_active_project=True)
    state["page"] = "wedding_projects"


def restore_project_workspace(
    state: MutableMapping[str, object], project: dict[str, Any]
) -> None:
    project_id = str(project["project_id"])
    profile = profile_from_json(project.get("wedding_profile", {}))
    ceremonies = ceremonies_from_json(project.get("selected_ceremonies", []))
    clear_project_workspace(state)
    state[ACTIVE_PROJECT_ID_KEY] = project_id
    state[LOADED_PROJECT_ID_KEY] = project_id
    state[ACTIVE_PROJECT_NAME_KEY] = str(project.get("project_name", "Wedding"))
    state[DRAFT_STATE_KEY] = profile
    state[PROFILE_SAVED_KEY] = profile
    state[DRAFT_SAVED_STATE_KEY] = True
    state[CEREMONIES_STATE_KEY] = ceremonies
    state[CUSTOM_CEREMONIES_STATE_KEY] = [item for item in ceremonies if item.is_custom]
    state[CEREMONIES_CONFIRMED_KEY] = bool(ceremonies)
    state[CEREMONIES_SAVED_KEY] = list(ceremonies)
    state[CEREMONIES_DRAFT_IDS_KEY] = [item.ceremony_id for item in ceremonies]
    raw_budget = project.get("wedding_profile", {}).get("smart_budget_plan")
    if raw_budget is not None:
        try:
            budget = budget_plan_from_json(raw_budget)
            if not budget.accepted or not all(n.reviewed for n in budget.needs):
                raise ValueError("Stored budget was not accepted.")
        except ValueError:
            state[BUDGET_ERROR_KEY] = "The saved budget could not be restored. Your wedding details and ceremony plans are still available."
        else:
            state[BUDGET_SAVED_KEY] = budget
            state[BUDGET_DRAFT_KEY] = budget
    raw_timeline = project.get("wedding_profile", {}).get("wedding_timeline")
    state[TIMELINE_BASELINE_KEY] = raw_timeline
    if raw_timeline is not None:
        try:
            timeline = timeline_from_json(raw_timeline)
            if not timeline.accepted:
                raise ValueError("Stored timeline was not accepted.")
        except ValueError:
            state[TIMELINE_ERROR_KEY] = "The saved timeline could not be restored. Wedding details, ceremonies and budget remain available."
        else:
            state[TIMELINE_SAVED_KEY] = timeline
            state[TIMELINE_DRAFT_KEY] = timeline
    try:
        cards = invitations_from_json(project.get("wedding_profile", {}).get("wedding_invitations"))
    except ValueError:
        state[INVITATIONS_ERROR_KEY] = "Saved invitation data could not be restored. Wedding details, ceremonies, budget and timeline remain available; existing invitation data will not be overwritten."
    else:
        state[INVITATIONS_SAVED_KEY] = {c.invitation_id: c for c in cards}
    state["page"] = PAGE_BY_PLANNING_STEP.get(
        str(project.get("planning_step", "requirements")), "wedding_start"
    )


def activate_created_project(
    state: MutableMapping[str, object], project: dict[str, Any]
) -> None:
    restore_project_workspace(state, project)
    state["page"] = "wedding_dashboard"


def persist_active_project(
    state: MutableMapping[str, object],
    *,
    profile: WeddingProfile | None = None,
    selected_ceremonies: Sequence[Ceremony] | None = None,
    planning_step: str | None = None,
) -> dict[str, Any]:
    project_id = state.get(ACTIVE_PROJECT_ID_KEY)
    if not isinstance(project_id, str):
        raise ValueError("No wedding project is active.")
    if selected_ceremonies is not None and state.get(PENDING_PLAN_SAVES_KEY):
        raise ValueError("Retry failed ceremony plan saves before changing the selection.")
    pending = dict(state.get(PENDING_SAVE_KEY, {}))
    for key, value in (
        ("profile", profile),
        ("selected_ceremonies", selected_ceremonies),
        ("planning_step", planning_step),
    ):
        if value is not None:
            pending[key] = value
    state[PENDING_SAVE_KEY] = pending
    try:
        updated = project_repository_from_state(state).update_project(
            project_id, **pending
        )
        if updated is None:
            raise RuntimeError("The selected wedding project could not be updated.")
        if updated.get("project_id") != project_id:
            raise RuntimeError("The update returned a different wedding project.")
        if "profile" in pending:
            stored_profile = profile_from_json(updated.get("wedding_profile"))
            if stored_profile != profile_from_json(profile_to_json(pending["profile"])):
                raise RuntimeError("The saved wedding profile could not be verified.")
        if "selected_ceremonies" in pending:
            expected = ceremonies_to_json(pending["selected_ceremonies"])
            if updated.get("selected_ceremonies") != expected:
                raise RuntimeError("The ceremony update could not be verified.")
    except Exception:
        state[PROJECT_DIRTY_KEY] = True
        raise
    state.pop(PENDING_SAVE_KEY, None)
    if "selected_ceremonies" in pending:
        state[CEREMONIES_SAVED_KEY] = ceremonies_from_json(updated["selected_ceremonies"])
    if "profile" in pending:
        state[DRAFT_STATE_KEY] = pending["profile"]
        state[PROFILE_SAVED_KEY] = stored_profile
        state[DRAFT_SAVED_STATE_KEY] = True
    if not any(state.get(k) for k in (PENDING_PLAN_SAVES_KEY, PENDING_BUDGET_SAVE_KEY, PENDING_TIMELINE_SAVE_KEY, PENDING_NAMES_KEY, PENDING_INVITATION_KEY)):
        state.pop(PROJECT_DIRTY_KEY, None)
        state.pop(PROJECT_ERROR_KEY, None)
    return updated


def saved_project_ceremonies(state: MutableMapping[str, object]) -> list[Ceremony]:
    project_id = state.get(ACTIVE_PROJECT_ID_KEY)
    saved = state.get(CEREMONIES_SAVED_KEY, None if project_id else [])
    if (
        (project_id and state.get(LOADED_PROJECT_ID_KEY) != project_id)
        or not isinstance(saved, list)
        or any(not isinstance(item, Ceremony) or not item.selected for item in saved)
    ):
        raise ValueError("Saved ceremony state is unavailable for this project.")
    return saved


def persist_ceremony_plan(
    state: MutableMapping[str, object], plan: CeremonyPlan
) -> dict[str, Any]:
    project_id = state.get(ACTIVE_PROJECT_ID_KEY)
    if not isinstance(project_id, str) or state.get(LOADED_PROJECT_ID_KEY) != project_id:
        raise ValueError("Load a wedding project before saving its ceremony plans.")
    if "selected_ceremonies" in state.get(PENDING_SAVE_KEY, {}):
        raise ValueError("Save the pending ceremony selections before editing plans.")
    pending = dict(state.get(PENDING_PLAN_SAVES_KEY, {}))
    pending[plan.ceremony_id] = plan
    state[PENDING_PLAN_SAVES_KEY] = pending
    try:
        updated = project_repository_from_state(state).save_ceremony_plan(project_id, plan)
        if updated.get("project_id") != project_id:
            raise RuntimeError("The update returned a different wedding project.")
        ceremonies = ceremonies_from_json(updated.get("selected_ceremonies"))
        stored = next((item.plan for item in ceremonies if item.ceremony_id == plan.ceremony_id), None)
        if stored is None or ceremony_plan_to_json(stored) != ceremony_plan_to_json(plan):
            raise RuntimeError("The saved ceremony plan could not be verified.")
    except Exception:
        state[PROJECT_DIRTY_KEY] = True
        raise
    state[CEREMONIES_SAVED_KEY] = ceremonies
    by_id = {item.ceremony_id: item for item in ceremonies}
    for key in (CEREMONIES_STATE_KEY, CUSTOM_CEREMONIES_STATE_KEY):
        state[key] = [
            replace(item, plan=by_id[item.ceremony_id].plan)
            if item.ceremony_id in by_id else item
            for item in state.get(key, [])
        ]
    pending.pop(plan.ceremony_id, None)
    state[PENDING_PLAN_SAVES_KEY] = pending
    if not pending and not any(state.get(k) for k in (PENDING_SAVE_KEY, PENDING_BUDGET_SAVE_KEY, PENDING_TIMELINE_SAVE_KEY, PENDING_NAMES_KEY, PENDING_INVITATION_KEY)):
        state.pop(PROJECT_DIRTY_KEY, None)
        state.pop(PROJECT_ERROR_KEY, None)
    return updated


def persist_budget_plan(state: MutableMapping[str, object], plan: BudgetPlan) -> dict[str, Any]:
    project_id = state.get(ACTIVE_PROJECT_ID_KEY)
    if not isinstance(project_id, str) or state.get(LOADED_PROJECT_ID_KEY) != project_id:
        raise ValueError("Load a wedding project before saving its budget.")
    if state.get(PENDING_SAVE_KEY) or state.get(PENDING_PLAN_SAVES_KEY):
        raise ValueError("Save pending wedding details and ceremony plans first.")
    state[PENDING_BUDGET_SAVE_KEY] = plan
    try:
        updated = project_repository_from_state(state).save_budget_plan(project_id, plan)
        raw = updated.get("wedding_profile", {}).get("smart_budget_plan")
        if updated.get("project_id") != project_id or raw != budget_plan_to_json(plan):
            raise RuntimeError("The saved budget could not be verified.")
        saved = budget_plan_from_json(raw)
    except Exception:
        state[PROJECT_DIRTY_KEY] = True
        raise
    state[BUDGET_SAVED_KEY] = saved
    state[BUDGET_DRAFT_KEY] = saved
    state.pop(PENDING_BUDGET_SAVE_KEY, None)
    state.pop(BUDGET_ERROR_KEY, None)
    if not state.get(PENDING_TIMELINE_SAVE_KEY) and not state.get(PENDING_NAMES_KEY) and not state.get(PENDING_INVITATION_KEY):
        state.pop(PROJECT_DIRTY_KEY, None)
        state.pop(PROJECT_ERROR_KEY, None)
    return updated


def persist_timeline_plan(state, plan):
    project_id = state.get(ACTIVE_PROJECT_ID_KEY)
    if not isinstance(project_id, str) or state.get(LOADED_PROJECT_ID_KEY) != project_id:
        raise ValueError("Load a wedding project before saving its timeline.")
    if state.get(PENDING_SAVE_KEY) or state.get(PENDING_PLAN_SAVES_KEY) or state.get(PENDING_BUDGET_SAVE_KEY):
        raise ValueError("Save pending wedding details, ceremony plans and budget first.")
    state[PENDING_TIMELINE_SAVE_KEY] = plan
    try:
        baseline = state.get(TIMELINE_SAVED_KEY)
        updated = project_repository_from_state(state).save_timeline_plan(
            project_id, plan, expected_timeline=state.get(TIMELINE_BASELINE_KEY, timeline_to_json(baseline) if baseline else None),
        )
        raw = updated.get("wedding_profile", {}).get("wedding_timeline")
        if updated.get("project_id") != project_id or raw != timeline_to_json(plan):
            raise RuntimeError("The saved timeline could not be verified.")
        saved = timeline_from_json(raw)
    except Exception:
        state[PROJECT_DIRTY_KEY] = True
        raise
    state[TIMELINE_SAVED_KEY] = saved
    state[TIMELINE_DRAFT_KEY] = saved
    state[TIMELINE_BASELINE_KEY] = raw
    for key in (PENDING_TIMELINE_SAVE_KEY, TIMELINE_ERROR_KEY, PROJECT_DIRTY_KEY, PROJECT_ERROR_KEY):
        state.pop(key, None)
    if state.get(PENDING_NAMES_KEY) or state.get(PENDING_INVITATION_KEY):
        state[PROJECT_DIRTY_KEY] = True
    return updated


def reload_timeline_context(state):
    """Explicit read after a conflict; retain the local draft for comparison/review."""
    project_id = state.get(ACTIVE_PROJECT_ID_KEY)
    if not isinstance(project_id, str) or state.get(LOADED_PROJECT_ID_KEY) != project_id:
        raise ValueError("Load a wedding project first.")
    if any(state.get(k) for k in (PENDING_SAVE_KEY, PENDING_PLAN_SAVES_KEY, PENDING_BUDGET_SAVE_KEY, PENDING_NAMES_KEY, PENDING_INVITATION_KEY)):
        raise ValueError("Save pending wedding details, ceremony plans and budget first.")
    row = project_repository_from_state(state).load_selected_project(project_id)
    if row is None or row.get("project_id") != project_id:
        raise RuntimeError("The wedding project is unavailable.")
    # Validate the row before clearing any live state.
    validated = {}
    restore_project_workspace(validated, row)
    preserved = {key: state[key] for key in (TIMELINE_DRAFT_KEY, PENDING_TIMELINE_SAVE_KEY,
        "wedding_timeline_ai_cache", "wedding_timeline_view", TRAIL_KEY) if key in state}
    preserved.update({key: state[key] for key in state if key.startswith("wedding_invitation_")
                      and key not in {INVITATIONS_SAVED_KEY, INVITATIONS_ERROR_KEY}})
    restore_project_workspace(state, row)
    state.update(preserved)
    if state.get(PENDING_TIMELINE_SAVE_KEY):
        state[PROJECT_DIRTY_KEY] = True
    state["page"] = "wedding_timeline"


def reload_couple_names(state):
    project_id = state.get(ACTIVE_PROJECT_ID_KEY)
    baseline = state.get(PROFILE_SAVED_KEY)
    if not isinstance(project_id, str) or state.get(LOADED_PROJECT_ID_KEY) != project_id or not isinstance(baseline, WeddingProfile):
        raise ValueError("Load a wedding project first.")
    row = project_repository_from_state(state).load_selected_project(project_id)
    if row is None or row.get("project_id") != project_id:
        raise RuntimeError("The wedding project is unavailable.")
    latest = profile_from_json(row.get("wedding_profile"))
    state[PROFILE_SAVED_KEY] = replace(baseline, bride_name=latest.bride_name, groom_name=latest.groom_name)


def persist_couple_names(state, bride_name, groom_name):
    project_id = state.get(ACTIVE_PROJECT_ID_KEY)
    baseline = state.get(PROFILE_SAVED_KEY)
    if not isinstance(project_id, str) or state.get(LOADED_PROJECT_ID_KEY) != project_id or not isinstance(baseline, WeddingProfile):
        raise ValueError("Load a wedding project before saving names.")
    if any(state.get(k) for k in (PENDING_SAVE_KEY, PENDING_PLAN_SAVES_KEY, PENDING_BUDGET_SAVE_KEY, PENDING_TIMELINE_SAVE_KEY, PENDING_INVITATION_KEY)):
        raise ValueError("Save other pending project changes first.")
    state[PENDING_NAMES_KEY] = (bride_name, groom_name)
    try:
        saved = project_repository_from_state(state).save_couple_names(project_id, bride_name, groom_name,
            expected_names=(baseline.bride_name, baseline.groom_name))
        profile = profile_from_json(saved.get("wedding_profile"))
        if saved.get("project_id") != project_id or (profile.bride_name, profile.groom_name) != ((bride_name or "").strip() or None, (groom_name or "").strip() or None):
            raise RuntimeError("The saved names could not be verified.")
    except Exception:
        state[PROJECT_DIRTY_KEY] = True
        raise
    state[PROFILE_SAVED_KEY] = profile
    draft = state.get(DRAFT_STATE_KEY, profile)
    state[DRAFT_STATE_KEY] = replace(draft, bride_name=profile.bride_name, groom_name=profile.groom_name)
    for key in tuple(state):
        if key.startswith("wedding_") and key.endswith(("_bride_name_value", "_groom_name_value")):
            state[key] = profile.bride_name or "" if key.endswith("_bride_name_value") else profile.groom_name or ""
    state.pop(PENDING_NAMES_KEY, None)
    state.pop(PROJECT_DIRTY_KEY, None)
    state.pop(PROJECT_ERROR_KEY, None)
    return saved


def navigate_project_to_page(
    state: MutableMapping[str, object], page: str
) -> None:
    if isinstance(state.get(ACTIVE_PROJECT_ID_KEY), str):
        planning_step = next(
            (
                step
                for step, mapped_page in PAGE_BY_PLANNING_STEP.items()
                if mapped_page == page
            ),
            None,
        )
        if planning_step is not None:
            try:
                persist_active_project(state, planning_step=planning_step)
            except Exception:
                state[PROJECT_DIRTY_KEY] = True
                state[PROJECT_ERROR_KEY] = (
                    "Your planning progress could not be saved. Your current edits "
                    "are still available in this session."
                )
    state["page"] = page
