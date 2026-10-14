from collections.abc import Mapping, Sequence
from datetime import date, datetime
from enum import Enum
from typing import Any
from uuid import UUID

from ai_event_planning_platform.supabase_client import create_supabase_client
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
    "project_id, owner_id, project_name, planning_step, status, created_at, updated_at"
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
        return rows[0]

    def list_user_projects(self) -> list[dict[str, Any]]:
        response = (
            self._client.table("wedding_projects")
            .select(PROJECT_SUMMARY_COLUMNS)
            .eq("owner_id", self._owner_id)
            .order("updated_at", desc=True)
            .execute()
        )
        return _response_rows(response)

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
        status: str | None = None,
    ) -> dict[str, Any] | None:
        changes: dict[str, object] = {}
        if project_name is not None:
            changes["project_name"] = _required_text(project_name, "project_name", 120)
        if profile is not None:
            changes["wedding_profile"] = profile_to_json(profile)
        if selected_ceremonies is not None:
            changes["selected_ceremonies"] = ceremonies_to_json(selected_ceremonies)
        if status is not None:
            changes["status"] = _project_status(status)
        if not changes:
            raise ValueError("Provide at least one project field to update.")

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
        "wedding_date": wedding_date.isoformat() if wedding_date else None,
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