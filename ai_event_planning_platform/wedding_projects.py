from collections.abc import Mapping, Sequence
from datetime import date, datetime
from dataclasses import replace
from enum import Enum
from typing import Any
from uuid import UUID

from ai_event_planning_platform.budget_engine import (
    BudgetPlan, assess_budget, budget_plan_from_json, budget_plan_to_json,
)
from ai_event_planning_platform.supabase_client import create_supabase_client
from ai_event_planning_platform.ceremony_plan import (
    CeremonyPlan, ceremony_plan_from_json, ceremony_plan_to_json,
    validate_ceremony_plan,
)
from ai_event_planning_platform.wedding_ceremonies import (
    CEREMONY_BY_ID,
    Ceremony,
)
from ai_event_planning_platform.wedding_profile import (
    CulturalBackground,
    TraditionPreference,
    WeddingDateStatus,
    WeddingLocationPreference,
    WeddingProfile,
    WeddingStyle,
    validate_wedding_profile,
)


PROJECT_STATUSES = frozenset({"planning", "completed", "archived"})
PROJECT_SUMMARY_COLUMNS = (
    "project_id, owner_id, project_name, wedding_profile, planning_step, status, created_at, updated_at"
)


class ProjectSerializationError(ValueError):
    """Raised when stored project data is not in the supported JSON shape."""


class WeddingProjectRepository:
    """Owner-scoped Supabase operations using the current user's access token."""

    def __init__(self, client: Any, owner_id: str | UUID) -> None:
        self._client = client
        self._owner_id = _uuid_text(owner_id, "owner_id")

    @classmethod
    def from_session_tokens(
        cls, access_token: str, refresh_token: str
    ) -> "WeddingProjectRepository":
        if not access_token or not refresh_token:
            raise ValueError("An authenticated Supabase session is required.")
        client = create_supabase_client()
        response = client.auth.set_session(access_token, refresh_token)
        user = getattr(response, "user", None)
        user_id = getattr(user, "id", None)
        if not user_id:
            raise ValueError("The Supabase session has no verified user.")
        return cls(client, user_id)

    def create_wedding_project(
        self,
        project_name: str,
        profile: WeddingProfile,
        selected_ceremonies: Sequence[Ceremony],
        *,
        planning_step: str = "requirements",
        status: str = "planning",
    ) -> dict[str, Any]:
        payload = {
            "owner_id": self._owner_id,
            "project_name": _required_text(project_name, "project_name", 120),
            "wedding_profile": profile_to_json(profile),
            "selected_ceremonies": ceremonies_to_json(selected_ceremonies),
            "planning_step": _required_text(planning_step, "planning_step", 80),
            "status": _project_status(status),
        }
        response = (
            self._client.table("wedding_projects")
            .insert(payload)
            .select("*")
            .execute()
        )
        rows = _response_rows(response)
        if not rows:
            raise RuntimeError("The wedding project was not returned after creation.")
        created = rows[0]
        if any(created.get(key) != value for key, value in payload.items()):
            raise RuntimeError("The created wedding project response could not be verified.")
        reloaded = self.load_selected_project(created.get("project_id"))
        if reloaded is None or any(reloaded.get(key) != value for key, value in payload.items()):
            raise RuntimeError("The created wedding project could not be read back.")
        return reloaded

    def list_user_projects(self) -> list[dict[str, Any]]:
        response = (
            self._client.table("wedding_projects")
            .select(PROJECT_SUMMARY_COLUMNS)
            .eq("owner_id", self._owner_id)
            .order("updated_at", desc=True)
            .execute()
        )
        return _response_rows(response)

    def delete_wedding_project(self, project_id: str | UUID, *, confirmed: bool = False,
                               expected_updated_at: str | None = None) -> None:
        """Delete one confirmed, owner-scoped UUID and independently verify absence."""
        if confirmed is not True:
            raise ValueError("Explicit confirmation is required to permanently delete a project.")
        project_key = _uuid_text(project_id, "project_id")
        existing = self.load_selected_project(project_key)
        if (existing is None or existing.get("project_id") != project_key
                or existing.get("owner_id") != self._owner_id):
            raise RuntimeError("This project is unavailable for your account. Refresh the project list.")
        version = existing.get("updated_at")
        if not isinstance(version, str) or (expected_updated_at is not None and version != expected_updated_at):
            raise RuntimeError("The project changed. Cancel, refresh, and review it before deleting again.")
        response = (self._client.table("wedding_projects").delete()
                    .eq("project_id", project_key).eq("owner_id", self._owner_id)
                    .eq("updated_at", version).execute())
        rows = _response_rows(response)
        if (len(rows) != 1 or rows[0].get("project_id") != project_key
                or rows[0].get("owner_id") != self._owner_id):
            raise RuntimeError("Deletion could not be confirmed. Refresh the project list and retry if it is still present.")
        if self.load_selected_project(project_key) is not None:
            raise RuntimeError("The project is still present. Refresh the project list and retry deletion.")

    def load_selected_project(self, project_id: str | UUID) -> dict[str, Any] | None:
        project_key = _uuid_text(project_id, "project_id")
        response = (
            self._client.table("wedding_projects")
            .select("*")
            .eq("project_id", project_key)
            .eq("owner_id", self._owner_id)
            .limit(1)
            .execute()
        )
        rows = _response_rows(response)
        return rows[0] if rows else None

    def update_project(
        self,
        project_id: str | UUID,
        *,
        project_name: str | None = None,
        profile: WeddingProfile | None = None,
        selected_ceremonies: Sequence[Ceremony] | None = None,
        planning_step: str | None = None,
        status: str | None = None,
        expected_updated_at: str | None = None,
    ) -> dict[str, Any] | None:
        changes: dict[str, object] = {}
        if project_name is not None:
            changes["project_name"] = _required_text(project_name, "project_name", 120)
        if profile is not None:
            current = self.load_selected_project(project_id)
            if current is None:
                return None
            # Preserve versioned JSONB extensions, including the accepted budget.
            changes["wedding_profile"] = {
                **current.get("wedding_profile", {}), **profile_to_json(profile),
            }
            if expected_updated_at is None:
                expected_updated_at = current.get("updated_at")
        if selected_ceremonies is not None:
            changes["selected_ceremonies"] = ceremonies_to_json(selected_ceremonies)
        if planning_step is not None:
            changes["planning_step"] = _required_text(
                planning_step, "planning_step", 80
            )
        if status is not None:
            changes["status"] = _project_status(status)
        if not changes:
            raise ValueError("Provide at least one project field to update.")

        project_key = _uuid_text(project_id, "project_id")
        query = (
            self._client.table("wedding_projects")
            .update(changes)
            .eq("project_id", project_key)
            .eq("owner_id", self._owner_id)
        )
        if expected_updated_at is not None:
            query = query.eq("updated_at", expected_updated_at)
        response = query.select("*").execute()
        rows = _response_rows(response)
        if not rows:
            return None
        updated = rows[0]
        if updated.get("project_id") != project_key or updated.get("owner_id") != self._owner_id:
            raise RuntimeError("The update returned an unexpected wedding project.")
        json_fields = [key for key in ("wedding_profile", "selected_ceremonies") if key in changes]
        if json_fields:
            if any(updated.get(key) != changes[key] for key in json_fields):
                raise RuntimeError("The project JSON update response could not be verified.")
            reloaded = self.load_selected_project(project_key)
            if reloaded is None or any(reloaded.get(key) != changes[key] for key in json_fields):
                raise RuntimeError("The saved project JSON could not be read back.")
            return reloaded
        return updated

    def save_couple_names(self, project_id, bride_name, groom_name, *, expected_names):
        if any(v is not None and not isinstance(v, str) for v in (bride_name, groom_name)):
            raise ValueError("Couple names must be text.")
        project_key = _uuid_text(project_id, "project_id")
        project = self.load_selected_project(project_key)
        if project is None:
            raise RuntimeError("The wedding project is unavailable for this account.")
        raw = project["wedding_profile"]
        current = profile_from_json(raw)
        names = ((bride_name or "").strip() or None, (groom_name or "").strip() or None)
        if (current.bride_name, current.groom_name) not in (tuple(expected_names), names):
            raise ValueError("Names changed in another session. Reopen and review before replacing them.")
        candidate = replace(current, bride_name=names[0], groom_name=names[1])
        profile_to_json(candidate)  # Validate optional fields without changing other saved requirements.
        version = project.get("updated_at")
        if not isinstance(version, str) or not version:
            raise RuntimeError("The project version could not be verified.")
        merged = {**raw, "bride_name": names[0], "groom_name": names[1]}
        response = (self._client.table("wedding_projects").update({"wedding_profile": merged})
            .eq("project_id", project_key).eq("owner_id", self._owner_id)
            .eq("updated_at", version).select("*").execute())
        rows = _response_rows(response)
        if not rows:
            raise RuntimeError("The project changed or is unavailable. Reload and retry.")
        updated = rows[0]
        if (updated.get("project_id") != project_key or updated.get("owner_id") != self._owner_id
                or updated.get("wedding_profile") != merged):
            raise RuntimeError("The names update response could not be verified.")
        saved = self.load_selected_project(project_key)
        if (saved is None or saved.get("project_id") != project_key or saved.get("owner_id") != self._owner_id
                or saved.get("wedding_profile") != merged):
            raise RuntimeError("The saved names could not be read back.")
        return saved

    def save_budget_plan(self, project_id: str | UUID, plan: BudgetPlan) -> dict[str, Any]:
        project = self.load_selected_project(project_id)
        if project is None:
            raise RuntimeError("The wedding project is unavailable for this account.")
        if not plan.accepted or not all(n.reviewed for n in plan.needs):
            raise ValueError("Review and explicitly accept the budget before saving.")
        profile = profile_from_json(project.get("wedding_profile"))
        ceremonies = ceremonies_from_json(project.get("selected_ceremonies"))
        assessment = assess_budget(plan, profile, ceremonies)
        if not assessment.feasible:
            raise ValueError("Resolve budget feasibility issues before saving.")
        version = project.get("updated_at")
        if not isinstance(version, str):
            raise RuntimeError("The project version could not be verified.")
        profile_json = {**project["wedding_profile"], "smart_budget_plan": budget_plan_to_json(plan)}
        project_key = _uuid_text(project_id, "project_id")
        response = (
            self._client.table("wedding_projects")
            .update({"wedding_profile": profile_json, "planning_step": "smart_wedding_budget"})
            .eq("project_id", project_key).eq("owner_id", self._owner_id)
            .eq("updated_at", version).select("*").execute()
        )
        rows = _response_rows(response)
        if not rows:
            raise RuntimeError("The project changed or is unavailable. Reload it and retry.")
        updated = rows[0]
        if (updated.get("project_id") != project_key or updated.get("owner_id") != self._owner_id
                or updated.get("wedding_profile") != profile_json):
            raise RuntimeError("The budget update response could not be verified.")
        reloaded = self.load_selected_project(project_key)
        if (reloaded is None or reloaded.get("project_id") != project_key
                or reloaded.get("owner_id") != self._owner_id
                or reloaded.get("wedding_profile") != profile_json):
            raise RuntimeError("The saved budget could not be read back.")
        return reloaded

    def save_invitation(self, project_id, card, *, expected_invitation=None):
        from ai_event_planning_platform.invitations import (
            invitation_to_json, invitations_from_json, invitations_to_json,
        )
        value = invitation_to_json(card)
        project_key = _uuid_text(project_id, "project_id")
        project = self.load_selected_project(project_key)
        if project is None:
            raise RuntimeError("The wedding project is unavailable for this account.")
        raw = project["wedding_profile"]
        cards = invitations_from_json(raw.get("wedding_invitations"))
        current = next((c for c in cards if c.invitation_id == card.invitation_id), None)
        if (invitation_to_json(current) if current else None) not in (expected_invitation, value):
            raise ValueError("This invitation changed in another session. Load the latest saved details and review before replacing it.")
        ceremonies = ceremonies_from_json(project.get("selected_ceremonies"))
        if not set(card.selected_ceremony_ids) <= {c.ceremony_id for c in ceremonies}:
            raise ValueError("Only confirmed ceremonies can be included in an invitation.")
        version = project.get("updated_at")
        if not isinstance(version, str) or not version:
            raise RuntimeError("The project version could not be verified.")
        merged_cards = tuple(card if c.invitation_id == card.invitation_id else c for c in cards)
        if current is None:
            merged_cards = (*merged_cards, card)
        profile_json = {**raw, "wedding_invitations": invitations_to_json(merged_cards)}
        response = (self._client.table("wedding_projects")
            .update({"wedding_profile": profile_json, "planning_step": "wedding_invitations"})
            .eq("project_id", project_key).eq("owner_id", self._owner_id)
            .eq("updated_at", version).select("*").execute())
        rows = _response_rows(response)
        if (not rows or rows[0].get("project_id") != project_key or rows[0].get("owner_id") != self._owner_id
                or rows[0].get("wedding_profile") != profile_json):
            raise RuntimeError("The invitation update changed concurrently or its response could not be verified.")
        saved = self.load_selected_project(project_key)
        if (saved is None or saved.get("project_id") != project_key or saved.get("owner_id") != self._owner_id
                or saved.get("wedding_profile") != profile_json):
            raise RuntimeError("The saved invitation could not be read back.")
        return saved

    def save_timeline_plan(self, project_id, plan, *, expected_timeline=None):
        from ai_event_planning_platform.wedding_timeline import (
            rebase_timeline, schedule_issues, timeline_to_json, validate_timeline,
        )
        validate_timeline(plan)
        if not plan.accepted or any(i.severity == "error" for i in schedule_issues(plan)):
            raise ValueError("Review the timeline and resolve blocking errors before saving.")
        project_key = _uuid_text(project_id, "project_id")
        project = self.load_selected_project(project_key)
        if project is None:
            raise RuntimeError("The wedding project is unavailable for this account.")
        raw_profile = project.get("wedding_profile", {})
        stored_timeline = raw_profile.get("wedding_timeline")
        if stored_timeline not in (expected_timeline, timeline_to_json(plan)):
            raise ValueError("Another session changed the saved timeline. Reload and review it before replacing it.")
        profile = profile_from_json(raw_profile)
        ceremonies = ceremonies_from_json(project.get("selected_ceremonies"))
        budget = budget_plan_from_json(raw_profile["smart_budget_plan"]) if raw_profile.get("smart_budget_plan") is not None else None
        # Verify both context and authoritative ceremony fields; never trust client dates.
        if replace(rebase_timeline(plan, profile, ceremonies, budget), accepted=True) != plan:
            raise ValueError("Saved wedding details changed. Refresh and review the timeline before saving.")
        version = project.get("updated_at")
        if not isinstance(version, str) or not version:
            raise RuntimeError("The project version could not be verified.")
        profile_json = {**raw_profile, "wedding_timeline": timeline_to_json(plan)}
        response = (self._client.table("wedding_projects")
            .update({"wedding_profile": profile_json, "planning_step": "wedding_timeline"})
            .eq("project_id", project_key).eq("owner_id", self._owner_id)
            .eq("updated_at", version).select("*").execute())
        rows = _response_rows(response)
        if not rows:
            raise RuntimeError("The project changed or is unavailable. Reload it and retry.")
        updated = rows[0]
        if (updated.get("project_id") != project_key or updated.get("owner_id") != self._owner_id
                or updated.get("wedding_profile") != profile_json):
            raise RuntimeError("The timeline update response could not be verified.")
        reloaded = self.load_selected_project(project_key)
        if (reloaded is None or reloaded.get("project_id") != project_key
                or reloaded.get("owner_id") != self._owner_id
                or reloaded.get("wedding_profile") != profile_json):
            raise RuntimeError("The saved timeline could not be read back.")
        return reloaded

    def save_ceremony_plan(
        self, project_id: str | UUID, plan: CeremonyPlan
    ) -> dict[str, Any]:
        if validate_ceremony_plan(plan):
            raise ValueError("Ceremony plan failed validation.")
        project = self.load_selected_project(project_id)
        if project is None:
            raise RuntimeError("The wedding project is unavailable for this account.")
        ceremonies = ceremonies_from_json(project.get("selected_ceremonies"))
        if not any(item.ceremony_id == plan.ceremony_id for item in ceremonies):
            raise ValueError("Plans can only be saved for confirmed ceremonies.")
        updated_at = project.get("updated_at")
        if not isinstance(updated_at, str):
            raise RuntimeError("The project version could not be verified.")
        updated = self.update_project(
            project_id,
            selected_ceremonies=[
                replace(item, plan=replace(plan, ceremony_name=item.name))
                if item.ceremony_id == plan.ceremony_id else item
                for item in ceremonies
            ],
            planning_step="ceremony_planning",
            expected_updated_at=updated_at,
        )
        if updated is None:
            raise RuntimeError("The project changed or is unavailable. Reload it and retry.")
        return updated

    def save_planning_progress(
        self,
        project_id: str | UUID,
        planning_step: str,
        *,
        status: str | None = None,
    ) -> dict[str, Any] | None:
        changes: dict[str, object] = {
            "planning_step": _required_text(planning_step, "planning_step", 80)
        }
        if status is not None:
            changes["status"] = _project_status(status)
        response = (
            self._client.table("wedding_projects")
            .update(changes)
            .eq("project_id", _uuid_text(project_id, "project_id"))
            .eq("owner_id", self._owner_id)
            .select("*")
            .execute()
        )
        rows = _response_rows(response)
        return rows[0] if rows else None


def profile_to_json(profile: WeddingProfile) -> dict[str, object]:
    errors = validate_wedding_profile(profile, today=date(1900, 1, 1))
    if errors:
        raise ProjectSerializationError("Wedding profile is invalid.")
    wedding_date = profile.wedding_date
    if isinstance(wedding_date, datetime):
        wedding_date = wedding_date.date()
    return {
        "wedding_date_status": profile.wedding_date_status.value,
        "wedding_date": (
            wedding_date.isoformat() if isinstance(wedding_date, date) else wedding_date
        ),
        "city": profile.city,
        "state": profile.state,
        "location_preference": (
            profile.location_preference.value if profile.location_preference else None
        ),
        "guest_count": profile.guest_count,
        "budget_inr": profile.budget_inr,
        "style": profile.style.value if profile.style else None,
        "partner_1_culture": _culture_to_json(profile.partner_1_culture),
        "partner_2_culture": _culture_to_json(profile.partner_2_culture),
        "traditions": [tradition.value for tradition in profile.traditions],
        "additional_preferences": profile.additional_preferences,
        "bride_name": profile.bride_name,
        "groom_name": profile.groom_name,
    }


def profile_from_json(value: object) -> WeddingProfile:
    if not isinstance(value, Mapping):
        raise ProjectSerializationError("Wedding profile JSON must be an object.")
    try:
        status = WeddingDateStatus(
            value.get("wedding_date_status", WeddingDateStatus.NOT_DECIDED_YET.value)
        )
        raw_date = value.get("wedding_date")
        wedding_date = None
        if raw_date is not None:
            if not isinstance(raw_date, str):
                raise ValueError
            normalized_date = (
                f"{raw_date}-01"
                if status is WeddingDateStatus.APPROXIMATE_DATE and len(raw_date) == 7
                else raw_date
            )
            wedding_date = date.fromisoformat(normalized_date)
        location = _optional_enum(
            WeddingLocationPreference, value.get("location_preference")
        )
        style = _optional_enum(WeddingStyle, value.get("style"))
        traditions_value = value.get("traditions", [])
        if not isinstance(traditions_value, (list, tuple)):
            raise ValueError
        traditions = tuple(TraditionPreference(item) for item in traditions_value)
        profile = WeddingProfile(
            wedding_date_status=status,
            wedding_date=wedding_date,
            city=_optional_text(value.get("city")),
            state=_optional_text(value.get("state")),
            location_preference=location,
            guest_count=_optional_integer(value.get("guest_count")),
            budget_inr=_optional_integer(value.get("budget_inr")),
            style=style,
            partner_1_culture=_culture_from_json(value.get("partner_1_culture")),
            partner_2_culture=_culture_from_json(value.get("partner_2_culture")),
            traditions=traditions,
            additional_preferences=_optional_text(value.get("additional_preferences")),
            bride_name=_optional_text(value.get("bride_name")),
            groom_name=_optional_text(value.get("groom_name")),
        )
    except (TypeError, ValueError):
        raise ProjectSerializationError("Wedding profile JSON has invalid fields.") from None
    if validate_wedding_profile(profile, today=date(1900, 1, 1)):
        raise ProjectSerializationError("Wedding profile JSON failed validation.")
    return profile


def ceremonies_to_json(ceremonies: Sequence[Ceremony]) -> list[dict[str, object]]:
    serialized: list[dict[str, object]] = []
    seen_ids: set[str] = set()
    for ceremony in ceremonies:
        if not isinstance(ceremony, Ceremony):
            raise ProjectSerializationError("Ceremony data has an invalid format.")
        if not ceremony.selected:
            continue
        if ceremony.ceremony_id in seen_ids:
            raise ProjectSerializationError("Ceremony IDs must be unique.")
        seen_ids.add(ceremony.ceremony_id)
        if ceremony.is_custom:
            _uuid_text(ceremony.ceremony_id, "custom ceremony ID")
            name = _required_text(ceremony.name, "ceremony name", 80)
            description = _required_text(ceremony.description, "ceremony description", 500)
            association = _optional_text(ceremony.cultural_association)
        else:
            catalog_ceremony = CEREMONY_BY_ID.get(ceremony.ceremony_id)
            if catalog_ceremony is None:
                raise ProjectSerializationError("Unknown catalog ceremony ID.")
            name = catalog_ceremony.name
            description = catalog_ceremony.description
            association = catalog_ceremony.cultural_association
        serialized.append(
            {
                "ceremony_id": ceremony.ceremony_id,
                "name": name,
                "description": description,
                "cultural_association": association,
                "is_custom": ceremony.is_custom,
                "selected": True,
            }
        )
        if ceremony.plan is not None:
            if ceremony.plan.ceremony_id != ceremony.ceremony_id:
                raise ProjectSerializationError("The plan belongs to a different ceremony.")
            serialized[-1]["plan"] = ceremony_plan_to_json(ceremony.plan)
    return serialized


def ceremonies_from_json(value: object) -> list[Ceremony]:
    if not isinstance(value, list):
        raise ProjectSerializationError("Selected ceremonies JSON must be an array.")
    ceremonies: list[Ceremony] = []
    seen_ids: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            raise ProjectSerializationError("Ceremony JSON must contain objects.")
        try:
            ceremony_id = _required_text(item.get("ceremony_id"), "ceremony ID", 64)
            if ceremony_id in seen_ids or item.get("selected") is not True:
                raise ValueError
            seen_ids.add(ceremony_id)
            is_custom = item.get("is_custom")
            if type(is_custom) is not bool:
                raise ValueError
            if is_custom:
                _uuid_text(ceremony_id, "custom ceremony ID")
                ceremony = Ceremony(
                    ceremony_id=ceremony_id,
                    name=_required_text(item.get("name"), "ceremony name", 80),
                    description=_required_text(
                        item.get("description"), "ceremony description", 500
                    ),
                    cultural_association=_optional_text(
                        item.get("cultural_association")
                    ),
                    is_custom=True,
                    selected=True,
                )
            else:
                catalog_ceremony = CEREMONY_BY_ID.get(ceremony_id)
                if catalog_ceremony is None:
                    raise ValueError
                ceremony = Ceremony(
                    ceremony_id=catalog_ceremony.ceremony_id,
                    name=catalog_ceremony.name,
                    description=catalog_ceremony.description,
                    cultural_association=catalog_ceremony.cultural_association,
                    selected=True,
                )
            if "plan" in item:
                ceremony = replace(ceremony, plan=ceremony_plan_from_json(
                    item["plan"], ceremony_id=ceremony.ceremony_id,
                    ceremony_name=ceremony.name,
                ))
        except (TypeError, ValueError):
            raise ProjectSerializationError("Ceremony JSON has invalid fields.") from None
        ceremonies.append(ceremony)
    return ceremonies


def _culture_to_json(value: CulturalBackground | None) -> dict[str, object] | None:
    if value is None:
        return None
    return {"background": value.background, "region": value.region}


def _culture_from_json(value: object) -> CulturalBackground | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError
    return CulturalBackground(
        background=_optional_text(value.get("background")),
        region=_optional_text(value.get("region")),
    )


def _optional_enum(enum_type: type[Enum], value: object) -> Any:
    return None if value is None else enum_type(value)


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError
    return value


def _required_text(value: object, field_name: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be text.")
    cleaned = value.strip()
    if not cleaned or len(cleaned) > max_length:
        raise ValueError(f"{field_name} must be between 1 and {max_length} characters.")
    return cleaned


def _optional_integer(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int:
        raise ValueError
    return value


def _uuid_text(value: str | UUID, field_name: str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        raise ValueError(f"{field_name} must be a UUID.") from None


def _project_status(value: str) -> str:
    if value not in PROJECT_STATUSES:
        raise ValueError("Project status is not supported.")
    return value


def _response_rows(response: object) -> list[dict[str, Any]]:
    data = getattr(response, "data", None)
    if isinstance(data, Mapping):
        return [dict(data)]
    if isinstance(data, list):
        return [dict(item) for item in data if isinstance(item, Mapping)]
    return []
