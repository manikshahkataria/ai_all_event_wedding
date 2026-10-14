import json
from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import pytest

from ai_event_planning_platform.wedding_ceremonies import CEREMONY_BY_ID, Ceremony
from ai_event_planning_platform.wedding_profile import (
    CulturalBackground,
    TraditionPreference,
    WeddingDateStatus,
    WeddingLocationPreference,
    WeddingProfile,
    WeddingStyle,
)
from ai_event_planning_platform.wedding_projects import (
    ProjectSerializationError,
    WeddingProjectRepository,
    ceremonies_from_json,
    ceremonies_to_json,
    profile_from_json,
    profile_to_json,
)


OWNER_ONE = "4f4d06ab-f5dc-40f8-b62a-8f740c81542e"
OWNER_TWO = "f822eb70-59f1-4c4a-9b4d-b15b888c4661"


def test_profile_json_round_trip_preserves_dates_enums_and_cultural_preferences() -> None:
    profile = WeddingProfile(
        wedding_date_status=WeddingDateStatus.APPROXIMATE_DATE,
        wedding_date=date(2027, 11, 1),
        city="Jaipur",
        state="Rajasthan",
        location_preference=WeddingLocationPreference.DESTINATION,
        guest_count=250,
        budget_inr=1300000,
        style=WeddingStyle.MIXED,
        partner_1_culture=CulturalBackground("Punjabi", "Punjab"),
        traditions=(TraditionPreference.RELIGIOUS, TraditionPreference.CUSTOM),
        additional_preferences="Honor both families' preferences.",
    )

    stored = profile_to_json(profile)
    restored = profile_from_json(json.loads(json.dumps(stored)))

    assert restored == profile
    assert stored["wedding_date"] == "2027-11-01"


def test_ceremony_json_stores_only_selected_catalog_and_custom_entries() -> None:
    custom_id = str(uuid4())
    choices = [
        Ceremony(**{**CEREMONY_BY_ID["reception"].__dict__, "selected": True}),
        Ceremony(custom_id, "Family welcome", "A family-defined gathering.", is_custom=True, selected=True),
        CEREMONY_BY_ID["engagement"],
    ]

    stored = ceremonies_to_json(choices)
    restored = ceremonies_from_json(json.loads(json.dumps(stored)))

    assert [item.ceremony_id for item in restored] == ["reception", custom_id]
    assert restored[0].name == "Reception"
    assert restored[1].is_custom
    assert all(item.selected for item in restored)


def test_profile_and_ceremony_restoration_reject_malformed_json() -> None:
    with pytest.raises(ProjectSerializationError):
        profile_from_json({"wedding_date_status": "unknown"})
    with pytest.raises(ProjectSerializationError):
        ceremonies_from_json([{"ceremony_id": "unknown", "selected": True}])


class FakeQuery:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.filters: list[tuple[str, object]] = []
        self.operation = "select"
        self.payload: dict[str, object] = {}

    def select(self, _columns: str = "*") -> "FakeQuery":
        return self

    def insert(self, payload: dict[str, object]) -> "FakeQuery":
        self.operation = "insert"
        self.payload = payload
        return self

    def update(self, payload: dict[str, object]) -> "FakeQuery":
        self.operation = "update"
        self.payload = payload
        return self

    def eq(self, key: str, value: object) -> "FakeQuery":
        self.filters.append((key, value))
        return self

    def order(self, _key: str, *, desc: bool = False) -> "FakeQuery":
        return self

    def limit(self, _count: int) -> "FakeQuery":
        return self

    def execute(self) -> SimpleNamespace:
        if self.operation == "insert":
            row = dict(self.payload)
            row["project_id"] = str(uuid4())
            row["created_at"] = "2026-10-15T00:00:00+00:00"
            row["updated_at"] = row["created_at"]
            self.rows.append(row)
            return SimpleNamespace(data=[row])

        matched = [
            row
            for row in self.rows
            if all(row.get(key) == value for key, value in self.filters)
        ]
        if self.operation == "update":
            for row in matched:
                row.update(self.payload)
            return SimpleNamespace(data=matched)
        return SimpleNamespace(data=matched)


class FakeClient:
    def __init__(self) -> None:
        self.rows: list[dict[str, object]] = []
        self.auth = SimpleNamespace(
            set_session=lambda _access, _refresh: SimpleNamespace(
                user=SimpleNamespace(id=OWNER_ONE)
            )
        )

    def table(self, table_name: str) -> FakeQuery:
        assert table_name == "wedding_projects"
        return FakeQuery(self.rows)


def test_repository_derives_owner_from_authenticated_supabase_session(monkeypatch) -> None:
    client = FakeClient()
    monkeypatch.setattr(
        "ai_event_planning_platform.wedding_projects.create_supabase_client",
        lambda: client,
    )

    repository = WeddingProjectRepository.from_session_tokens("access", "refresh")
    created = repository.create_wedding_project("Our wedding", WeddingProfile(), [])

    assert created["owner_id"] == OWNER_ONE


def test_project_operations_are_isolated_by_owner() -> None:
    client = FakeClient()
    owner_one = WeddingProjectRepository(client, OWNER_ONE)
    owner_two = WeddingProjectRepository(client, OWNER_TWO)
    first = owner_one.create_wedding_project("Partner one", WeddingProfile(), [])
    second = owner_two.create_wedding_project("Partner two", WeddingProfile(), [])

    assert [item["project_name"] for item in owner_one.list_user_projects()] == [
        "Partner one"
    ]
    assert [item["project_name"] for item in owner_two.list_user_projects()] == [
        "Partner two"
    ]
    assert owner_two.load_selected_project(first["project_id"]) is None
    assert owner_one.update_project(first["project_id"], project_name="Updated") is not None
    assert owner_two.update_project(first["project_id"], project_name="Stolen") is None
    assert owner_one.load_selected_project(first["project_id"])["project_name"] == "Updated"
    assert owner_two.load_selected_project(second["project_id"])["project_name"] == "Partner two"


def test_repository_saves_planning_progress_without_changing_other_projects() -> None:
    client = FakeClient()
    repository = WeddingProjectRepository(client, OWNER_ONE)
    project = repository.create_wedding_project("Our wedding", WeddingProfile(), [])

    updated = repository.save_planning_progress(
        project["project_id"], "wedding_ceremonies", status="planning"
    )

    assert updated is not None
    assert updated["planning_step"] == "wedding_ceremonies"
    assert updated["status"] == "planning"