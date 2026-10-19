import json
from datetime import date
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import patch
from dataclasses import replace

import pytest

from ai_event_planning_platform.wedding_ceremonies import CEREMONY_BY_ID, Ceremony
from ai_event_planning_platform.ceremony_plan import CeremonyPlan, CeremonyDateCertainty
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

    def delete(self) -> "FakeQuery":
        self.operation = "delete"
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
        if self.operation == "delete":
            self.rows[:] = [row for row in self.rows if row not in matched]
            return SimpleNamespace(data=matched)
        return SimpleNamespace(data=matched)


class FakeClient:
    def __init__(self) -> None:
        self.rows: list[dict[str, object]] = []
        self.session_tokens: tuple[str, str] | None = None

        def set_session(access: str, refresh: str) -> SimpleNamespace:
            self.session_tokens = (access, refresh)
            return SimpleNamespace(user=SimpleNamespace(id=OWNER_ONE))

        self.auth = SimpleNamespace(set_session=set_session)

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

    assert client.session_tokens == ("access", "refresh")
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


def test_repository_saves_profile_ceremonies_and_step_to_only_selected_project() -> None:
    client = FakeClient()
    repository = WeddingProjectRepository(client, OWNER_ONE)
    first = repository.create_wedding_project("First", WeddingProfile(), [])
    second = repository.create_wedding_project("Second", WeddingProfile(), [])
    profile = WeddingProfile(city="Jaipur", state="Rajasthan")
    reception = Ceremony(
        **{**CEREMONY_BY_ID["reception"].__dict__, "selected": True}
    )

    updated = repository.update_project(
        first["project_id"],
        profile=profile,
        selected_ceremonies=[reception],
        planning_step="wedding_followups",
    )

    assert updated is not None
    assert updated["wedding_profile"]["city"] == "Jaipur"
    assert updated["selected_ceremonies"][0]["ceremony_id"] == "reception"
    assert updated["planning_step"] == "wedding_followups"
    untouched = repository.load_selected_project(second["project_id"])
    assert untouched is not None
    assert untouched["wedding_profile"]["city"] is None
    assert untouched["selected_ceremonies"] == []
    assert untouched["planning_step"] == "requirements"


@pytest.mark.parametrize("reloaded", [None, {"selected_ceremonies": []}])
def test_ceremony_save_requires_matching_database_readback(reloaded):
    repository = WeddingProjectRepository(FakeClient(), OWNER_ONE)
    project = repository.create_wedding_project("Wedding", WeddingProfile(), [])
    reception = Ceremony(**{**CEREMONY_BY_ID["reception"].__dict__, "selected": True})
    with (
        patch.object(repository, "load_selected_project", return_value=reloaded),
        pytest.raises(RuntimeError, match="read back"),
    ):
        repository.update_project(project["project_id"], selected_ceremonies=[reception])


def test_repository_rejects_stale_update_response(monkeypatch):
    repository = WeddingProjectRepository(FakeClient(), OWNER_ONE)
    project = repository.create_wedding_project("Wedding", WeddingProfile(), [])
    monkeypatch.setattr(FakeQuery, "execute", lambda self: SimpleNamespace(data=[dict(project)]))
    reception = Ceremony(**{**CEREMONY_BY_ID["reception"].__dict__, "selected": True})
    with pytest.raises(RuntimeError, match="response could not be verified"):
        repository.update_project(project["project_id"], selected_ceremonies=[reception])


def test_real_sdk_sends_selected_ceremonies_and_owner_scoped_readback():
    import httpx
    from supabase import create_client
    from supabase.lib.client_options import SyncClientOptions

    project_id = str(uuid4())
    stored = {
        "project_id": project_id, "owner_id": OWNER_ONE,
        "wedding_profile": profile_to_json(WeddingProfile(city="Pune")),
        "selected_ceremonies": [],
    }
    calls = []

    def respond(request):
        calls.append(request.method)
        assert request.url.path == "/rest/v1/wedding_projects"
        assert request.url.params["project_id"] == f"eq.{project_id}"
        assert request.url.params["owner_id"] == f"eq.{OWNER_ONE}"
        assert request.headers["Authorization"] == "Bearer test-user-access"
        if request.method == "PATCH":
            assert "return=representation" in request.headers["Prefer"]
            payload = json.loads(request.content)
            assert set(payload) == {"selected_ceremonies"}
            stored.update(payload)
        return httpx.Response(200, json=[stored])

    with httpx.Client(transport=httpx.MockTransport(respond)) as transport:
        client = create_client(
            "https://project.example.supabase.co", "test-publishable-key",
            options=SyncClientOptions(
                httpx_client=transport, auto_refresh_token=False, persist_session=False,
            ),
        )
        client.postgrest.auth("test-user-access")
        repository = WeddingProjectRepository(client, OWNER_ONE)
        choices = [
            Ceremony(**{**CEREMONY_BY_ID["reception"].__dict__, "selected": True}),
            Ceremony(str(uuid4()), "Family welcome", "Our own ceremony", is_custom=True, selected=True),
        ]
        choices[0] = replace(choices[0], plan=CeremonyPlan("reception", "Reception", guest_count=80))
        updated = repository.update_project(project_id, selected_ceremonies=choices)
    assert calls == ["PATCH", "GET"]
    assert updated["selected_ceremonies"] == ceremonies_to_json(choices)
    assert updated["wedding_profile"]["city"] == "Pune"


def test_profile_only_update_does_not_erase_saved_ceremonies():
    repository = WeddingProjectRepository(FakeClient(), OWNER_ONE)
    custom = Ceremony(str(uuid4()), "Family welcome", "Our own ceremony", is_custom=True, selected=True)
    project = repository.create_wedding_project("Wedding", WeddingProfile(), [custom])
    repository.update_project(project["project_id"], profile=WeddingProfile(city="Pune"))
    reloaded = repository.load_selected_project(project["project_id"])
    assert reloaded["selected_ceremonies"] == ceremonies_to_json([custom])
    assert reloaded["wedding_profile"]["city"] == "Pune"


def test_nested_ceremony_plans_are_backward_compatible_and_preserve_custom_names():
    custom_id = str(uuid4())
    legacy = [Ceremony(**{**CEREMONY_BY_ID["reception"].__dict__, "selected": True})]
    assert "plan" not in ceremonies_to_json(legacy)[0]
    assert ceremonies_from_json(ceremonies_to_json(legacy)) == legacy
    custom = Ceremony(custom_id, "Family welcome", "Family gathering", is_custom=True, selected=True,
                      plan=CeremonyPlan(custom_id, "Family welcome", guest_count=30))
    restored = ceremonies_from_json(ceremonies_to_json([*legacy, custom]))
    assert restored == [*legacy, custom]
    renamed = replace(custom, name="Family blessings")
    restored = ceremonies_from_json(ceremonies_to_json([renamed]))[0]
    assert restored.name == restored.plan.ceremony_name == "Family blessings"
    assert restored.plan.guest_count == 30


def test_save_individual_plan_preserves_other_ceremonies_plans_and_profile():
    repository = WeddingProjectRepository(FakeClient(), OWNER_ONE)
    reception = Ceremony(**{**CEREMONY_BY_ID["reception"].__dict__, "selected": True})
    haldi = Ceremony(**{**CEREMONY_BY_ID["haldi"].__dict__, "selected": True})
    haldi = replace(haldi, plan=CeremonyPlan("haldi", haldi.name, guest_count=40))
    project = repository.create_wedding_project("Wedding", WeddingProfile(city="Pune"), [reception, haldi])
    plan = CeremonyPlan("reception", "Reception", guest_count=80, ceremony_date=date(2027, 10, 24),
                        date_certainty=CeremonyDateCertainty.TENTATIVE)
    repository.save_ceremony_plan(project["project_id"], plan)
    edited = replace(plan, guest_count=100, date_certainty=CeremonyDateCertainty.FIXED)
    updated = repository.save_ceremony_plan(project["project_id"], edited)
    saved = ceremonies_from_json(updated["selected_ceremonies"])
    assert [item.ceremony_id for item in saved] == ["reception", "haldi"]
    assert saved[0].plan == edited
    assert saved[1].plan == haldi.plan
    assert updated["wedding_profile"]["city"] == "Pune"
    repository.update_project(project["project_id"], profile=WeddingProfile(city="Jaipur"))
    assert ceremonies_from_json(repository.load_selected_project(project["project_id"])["selected_ceremonies"])[0].plan == edited


def test_plan_updates_are_owner_scoped_and_require_confirmed_ids():
    client = FakeClient()
    repository = WeddingProjectRepository(client, OWNER_ONE)
    selected = Ceremony(**{**CEREMONY_BY_ID["reception"].__dict__, "selected": True})
    project = repository.create_wedding_project("Wedding", WeddingProfile(), [selected])
    with pytest.raises(ValueError, match="confirmed"):
        repository.save_ceremony_plan(project["project_id"], CeremonyPlan("haldi", "Haldi"))
    with pytest.raises(RuntimeError, match="unavailable"):
        WeddingProjectRepository(client, OWNER_TWO).save_ceremony_plan(project["project_id"], CeremonyPlan("reception", "Reception"))
    assert ceremonies_from_json(repository.load_selected_project(project["project_id"])["selected_ceremonies"])[0].plan is None


def test_plan_save_detects_concurrent_edits_without_overwriting_them(monkeypatch):
    client = FakeClient()
    repository = WeddingProjectRepository(client, OWNER_ONE)
    reception = Ceremony(**{**CEREMONY_BY_ID["reception"].__dict__, "selected": True})
    project = repository.create_wedding_project("Wedding", WeddingProfile(), [reception])
    execute = FakeQuery.execute

    def concurrent_edit(query):
        if query.operation == "update":
            client.rows[0]["updated_at"] = "2026-10-16T12:30:00+00:00"
            client.rows[0]["wedding_profile"] = profile_to_json(WeddingProfile(city="Jaipur"))
        return execute(query)

    monkeypatch.setattr(FakeQuery, "execute", concurrent_edit)
    with pytest.raises(RuntimeError, match="changed"):
        repository.save_ceremony_plan(project["project_id"], CeremonyPlan("reception", "Reception", guest_count=80))
    assert client.rows[0]["wedding_profile"]["city"] == "Jaipur"
    assert ceremonies_from_json(client.rows[0]["selected_ceremonies"])[0].plan is None


def test_invalid_nested_plan_is_rejected_without_erasing_ceremonies():
    row = ceremonies_to_json([Ceremony(**{**CEREMONY_BY_ID["reception"].__dict__, "selected": True})])
    row[0]["plan"] = {"version": 1, "estimated_budget_inr": float("nan")}
    with pytest.raises(ProjectSerializationError):
        ceremonies_from_json(row)


def test_real_sdk_plan_save_merges_and_uses_the_project_version():
    import httpx
    from supabase import create_client
    from supabase.lib.client_options import SyncClientOptions

    project_id = str(uuid4())
    original_timestamp = "2026-10-15T12:30:00+00:00"
    reception = replace(CEREMONY_BY_ID["reception"], selected=True)
    custom = Ceremony(str(uuid4()), "Family welcome", "Our own ceremony", is_custom=True, selected=True)
    row = {
        "project_id": project_id, "owner_id": OWNER_ONE,
        "selected_ceremonies": ceremonies_to_json([reception, custom]),
        "updated_at": original_timestamp,
    }
    methods = []

    def respond(request):
        methods.append(request.method)
        assert request.url.params["owner_id"] == f"eq.{OWNER_ONE}"
        assert request.url.params["project_id"] == f"eq.{project_id}"
        if request.method == "PATCH":
            assert request.url.params["updated_at"] == f"eq.{original_timestamp}"
            payload = json.loads(request.content)
            assert payload["selected_ceremonies"][0]["plan"]["guest_count"] == 80
            assert payload["selected_ceremonies"][1]["name"] == "Family welcome"
            assert payload["planning_step"] == "ceremony_planning"
            row.update(payload)
            row["updated_at"] = "2026-10-16T12:30:00+00:00"
        return httpx.Response(200, json=[row])

    with httpx.Client(transport=httpx.MockTransport(respond)) as transport:
        client = create_client(
            "https://project.example.supabase.co", "test-publishable-key",
            options=SyncClientOptions(httpx_client=transport, auto_refresh_token=False, persist_session=False),
        )
        repository = WeddingProjectRepository(client, OWNER_ONE)
        updated = repository.save_ceremony_plan(project_id, CeremonyPlan("reception", "Reception", guest_count=80))
    assert methods == ["GET", "PATCH", "GET"]
    assert len(updated["selected_ceremonies"]) == 2
