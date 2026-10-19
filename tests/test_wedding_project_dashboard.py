from datetime import date
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from streamlit.testing.v1 import AppTest

from ai_event_planning_platform.auth import AuthSessionTokens, store_auth_session
from ai_event_planning_platform.home import PROJECTS_PAGE
from ai_event_planning_platform.wedding_ceremonies import (
    CEREMONY_BY_ID, CEREMONY_CHECKBOX_PREFIX, CEREMONIES_STATE_KEY,
    CEREMONIES_SAVED_KEY, CUSTOM_CEREMONIES_STATE_KEY, Ceremony,
)
from ai_event_planning_platform.wedding_form import DRAFT_SAVED_STATE_KEY, DRAFT_STATE_KEY
from ai_event_planning_platform.wedding_profile import (
    WeddingDateStatus,
    WeddingProfile,
)
from ai_event_planning_platform.wedding_project_state import (
    ACTIVE_PROJECT_ID_KEY,
    LOADED_PROJECT_ID_KEY,
    PROJECT_DIRTY_KEY,
)
from ai_event_planning_platform.wedding_projects import (
    ceremonies_to_json,
    profile_to_json,
)


OWNER_ID = "4f4d06ab-f5dc-40f8-b62a-8f740c81542e"
OTHER_OWNER_ID = "f822eb70-59f1-4c4a-9b4d-b15b888c4661"
TOKENS = AuthSessionTokens("test-access-token", "test-refresh-token")


class MemoryProjects:
    def __init__(self, owner_id: str = OWNER_ID) -> None:
        self.owner_id = owner_id
        self.rows: list[dict[str, object]] = []
        self.fail_updates = False

    def create_wedding_project(
        self,
        project_name: str,
        profile: WeddingProfile,
        selected_ceremonies: list[Ceremony],
        *,
        planning_step: str = "requirements",
        status: str = "planning",
    ) -> dict[str, object]:
        row: dict[str, object] = {
            "project_id": str(uuid4()),
            "owner_id": self.owner_id,
            "project_name": project_name,
            "wedding_profile": profile_to_json(profile),
            "selected_ceremonies": ceremonies_to_json(selected_ceremonies),
            "planning_step": planning_step,
            "status": status,
            "created_at": "2026-10-15T00:00:00+00:00",
            "updated_at": "2026-10-15T00:00:00+00:00",
        }
        self.rows.append(row)
        return dict(row)

    def list_user_projects(self) -> list[dict[str, object]]:
        return [dict(row) for row in self.rows if row["owner_id"] == self.owner_id]

    def load_selected_project(self, project_id: str) -> dict[str, object] | None:
        return next(
            (
                dict(row)
                for row in self.rows
                if row["project_id"] == project_id
                and row["owner_id"] == self.owner_id
            ),
            None,
        )

    def update_project(
        self,
        project_id: str,
        *,
        profile: WeddingProfile | None = None,
        selected_ceremonies: list[Ceremony] | None = None,
        planning_step: str | None = None,
    ) -> dict[str, object] | None:
        if self.fail_updates:
            raise RuntimeError("mock database failure")
        row = next(
            (
                item
                for item in self.rows
                if item["project_id"] == project_id
                and item["owner_id"] == self.owner_id
            ),
            None,
        )
        if row is None:
            return None
        if profile is not None:
            row["wedding_profile"] = profile_to_json(profile)
        if selected_ceremonies is not None:
            row["selected_ceremonies"] = ceremonies_to_json(selected_ceremonies)
        if planning_step is not None:
            row["planning_step"] = planning_step
        row["updated_at"] = "2026-10-16T00:00:00+00:00"
        return dict(row)


@pytest.fixture

def app_environment(monkeypatch: pytest.MonkeyPatch):
    repository = MemoryProjects()
    monkeypatch.setattr(
        "ai_event_planning_platform.home.restore_authenticated_session",
        lambda access, refresh: AuthSessionTokens(access, refresh),
    )
    monkeypatch.setattr(
        "ai_event_planning_platform.home.project_repository_from_state",
        lambda _state: repository,
    )
    monkeypatch.setattr(
        "ai_event_planning_platform.wedding_project_state.project_repository_from_state",
        lambda _state: repository,
    )

    def create_app(page: str = PROJECTS_PAGE) -> AppTest:
        app = AppTest.from_file("../app.py")
        app.session_state["page"] = page
        store_auth_session(app.session_state, TOKENS)
        app.run()
        return app

    return repository, create_app


def _seed_project(
    repository: MemoryProjects,
    *,
    name: str = "Jaipur wedding",
    owner_id: str = OWNER_ID,
    profile: WeddingProfile | None = None,
) -> dict[str, object]:
    project = repository.create_wedding_project(
        name,
        profile or WeddingProfile(),
        [],
        planning_step="wedding_start",
    )
    project["owner_id"] = owner_id
    for row in repository.rows:
        if row["project_id"] == project["project_id"]:
            row["owner_id"] = owner_id
    return project


def _open_ceremonies(app_environment):
    repository, create_app = app_environment
    project = _seed_project(repository)
    repository.update_project(str(project["project_id"]), planning_step="wedding_ceremonies")
    app = create_app()
    app.button(key=f"continue_wedding_project_{project['project_id']}").click().run()
    app.session_state["page"] = "wedding_ceremonies"
    app.run()
    return repository, project, app


def test_unconfirmed_edits_do_not_claim_database_persistence(app_environment):
    repository, _project, app = _open_ceremonies(app_environment)
    app.checkbox(key=f"{CEREMONY_CHECKBOX_PREFIX}reception").check().run()
    app.button(key="confirm_wedding_ceremonies").click().run()
    assert any("saved to this wedding project" in item.value for item in app.success)
    app.checkbox(key=f"{CEREMONY_CHECKBOX_PREFIX}haldi").check().run()
    assert not app.success
    assert any("confirm" in item.value.lower() for item in app.warning)
    assert [item["ceremony_id"] for item in repository.rows[0]["selected_ceremonies"]] == ["reception"]


def test_stale_database_response_is_not_reported_as_saved(app_environment, monkeypatch):
    repository, project, app = _open_ceremonies(app_environment)
    monkeypatch.setattr(repository, "update_project", lambda *args, **kwargs: dict(repository.rows[0]))
    app.checkbox(key=f"{CEREMONY_CHECKBOX_PREFIX}reception").check().run()
    app.button(key="confirm_wedding_ceremonies").click().run()
    assert not app.success
    assert app.error
    assert app.session_state[PROJECT_DIRTY_KEY]
    assert app.checkbox(key=f"{CEREMONY_CHECKBOX_PREFIX}reception").value
    assert repository.rows[0]["selected_ceremonies"] == []


def test_confirmed_ceremonies_restore_checkboxes_in_a_new_session(app_environment):
    repository, project, app = _open_ceremonies(app_environment)
    for ceremony_id in ("reception", "haldi"):
        app.checkbox(key=f"{CEREMONY_CHECKBOX_PREFIX}{ceremony_id}").check().run()
    app.button(key="confirm_wedding_ceremonies").click().run()
    assert {item["ceremony_id"] for item in repository.rows[0]["selected_ceremonies"]} == {"haldi", "reception"}

    _repository, create_app = app_environment
    reopened = create_app()
    reopened.session_state[f"{CEREMONY_CHECKBOX_PREFIX}haldi"] = False
    reopened.session_state[f"{CEREMONY_CHECKBOX_PREFIX}engagement"] = True
    reopened.button(key=f"continue_wedding_project_{project['project_id']}").click().run()
    reopened.session_state["page"] = "wedding_ceremonies"
    reopened.run()
    assert reopened.checkbox(key=f"{CEREMONY_CHECKBOX_PREFIX}haldi").value
    assert reopened.checkbox(key=f"{CEREMONY_CHECKBOX_PREFIX}reception").value
    assert not reopened.checkbox(key=f"{CEREMONY_CHECKBOX_PREFIX}engagement").value
    assert not reopened.exception


def test_custom_ceremony_and_edited_name_round_trip(app_environment):
    repository, project, app = _open_ceremonies(app_environment)
    app.text_input(key="custom_ceremony_new_name").set_value("Family welcome")
    app.button(key="add_custom_ceremony_submit").click().run()
    custom = app.session_state[CUSTOM_CEREMONIES_STATE_KEY][0]
    app.checkbox(key=f"{CEREMONY_CHECKBOX_PREFIX}{custom.ceremony_id}").check().run()
    app.button(key="confirm_wedding_ceremonies").click().run()
    app.text_input(key=f"custom_ceremony_name_{custom.ceremony_id}").set_value("Family blessings")
    app.button(key=f"save_custom_ceremony_{custom.ceremony_id}").click().run()
    assert not app.success
    assert repository.rows[0]["selected_ceremonies"][0]["name"] == "Family welcome"
    app.button(key="confirm_wedding_ceremonies").click().run()

    _repository, create_app = app_environment
    reopened = create_app()
    reopened.button(key=f"continue_wedding_project_{project['project_id']}").click().run()
    reopened.session_state["page"] = "wedding_ceremonies"
    reopened.run()
    assert reopened.session_state[CUSTOM_CEREMONIES_STATE_KEY][0].name == "Family blessings"
    assert reopened.checkbox(key=f"{CEREMONY_CHECKBOX_PREFIX}{custom.ceremony_id}").value
    assert reopened.text_input(key=f"custom_ceremony_name_{custom.ceremony_id}").value == "Family blessings"
    assert not reopened.exception


def test_editing_ceremonies_and_saving_empty_selection(app_environment):
    repository, project, app = _open_ceremonies(app_environment)
    app.checkbox(key=f"{CEREMONY_CHECKBOX_PREFIX}reception").check().run()
    app.button(key="confirm_wedding_ceremonies").click().run()
    app.checkbox(key=f"{CEREMONY_CHECKBOX_PREFIX}reception").uncheck().run()
    app.checkbox(key=f"{CEREMONY_CHECKBOX_PREFIX}haldi").check().run()
    app.button(key="confirm_wedding_ceremonies").click().run()
    assert [item["ceremony_id"] for item in repository.rows[0]["selected_ceremonies"]] == ["haldi"]
    app.checkbox(key=f"{CEREMONY_CHECKBOX_PREFIX}haldi").uncheck().run()
    app.button(key="confirm_wedding_ceremonies").click().run()
    assert repository.rows[0]["selected_ceremonies"] == []
    assert any("saved to this wedding project" in item.value for item in app.success)


def test_profile_save_preserves_ceremonies_and_unconfirmed_checkbox_edits(app_environment):
    repository, project, app = _open_ceremonies(app_environment)
    app.checkbox(key=f"{CEREMONY_CHECKBOX_PREFIX}reception").check().run()
    app.button(key="confirm_wedding_ceremonies").click().run()
    app.checkbox(key=f"{CEREMONY_CHECKBOX_PREFIX}haldi").check().run()
    app.session_state["page"] = "step_by_step"
    app.run()
    app.text_input(key=f"wedding_{project['project_id']}_city_value").set_value("Pune")
    app.button(key=f"wedding_{project['project_id']}_save_profile").click().run()
    assert repository.rows[0]["wedding_profile"]["city"] == "Pune"
    assert [item["ceremony_id"] for item in repository.rows[0]["selected_ceremonies"]] == ["reception"]
    app.button(key="choose_ceremonies_from_profile").click().run()
    assert app.checkbox(key=f"{CEREMONY_CHECKBOX_PREFIX}haldi").value
    assert app.checkbox(key=f"{CEREMONY_CHECKBOX_PREFIX}reception").value
    assert not app.success
    app.button(key="confirm_wedding_ceremonies").click().run()
    assert {item["ceremony_id"] for item in repository.rows[0]["selected_ceremonies"]} == {"reception", "haldi"}
    assert not app.exception


def test_ceremony_save_failure_preserves_choices_and_allows_retry(app_environment):
    repository, project, app = _open_ceremonies(app_environment)
    repository.fail_updates = True
    app.checkbox(key=f"{CEREMONY_CHECKBOX_PREFIX}reception").check().run()
    app.button(key="confirm_wedding_ceremonies").click().run()
    assert not app.success
    assert any("could not be saved" in item.value for item in app.error)
    assert app.session_state[PROJECT_DIRTY_KEY]
    assert app.checkbox(key=f"{CEREMONY_CHECKBOX_PREFIX}reception").value
    assert app.session_state[CEREMONIES_STATE_KEY][0].ceremony_id == "reception"
    assert repository.rows[0]["selected_ceremonies"] == []
    app.button(key="open_wedding_projects").click().run()
    assert app.button(key="start_new_wedding").disabled
    repository.fail_updates = False
    app.button(key=f"continue_wedding_project_{project['project_id']}").click().run()
    app.button(key="confirm_wedding_ceremonies").click().run()
    assert not app.error
    assert PROJECT_DIRTY_KEY not in app.session_state
    assert [item["ceremony_id"] for item in repository.rows[0]["selected_ceremonies"]] == ["reception"]
    assert any("saved to this wedding project" in item.value for item in app.success)


def test_ceremony_drafts_and_saved_choices_are_isolated_between_projects(app_environment):
    repository, first, app = _open_ceremonies(app_environment)
    second = _seed_project(repository, name="Second wedding")
    repository.update_project(str(second["project_id"]), planning_step="wedding_ceremonies")
    app.checkbox(key=f"{CEREMONY_CHECKBOX_PREFIX}reception").check().run()
    app.button(key="confirm_wedding_ceremonies").click().run()
    app.checkbox(key=f"{CEREMONY_CHECKBOX_PREFIX}haldi").check().run()
    app.button(key="open_wedding_projects").click().run()
    app.button(key=f"continue_wedding_project_{second['project_id']}").click().run()
    app.session_state["page"] = "wedding_ceremonies"
    app.run()
    assert not app.checkbox(key=f"{CEREMONY_CHECKBOX_PREFIX}reception").value
    assert not app.checkbox(key=f"{CEREMONY_CHECKBOX_PREFIX}haldi").value
    app.checkbox(key=f"{CEREMONY_CHECKBOX_PREFIX}engagement").check().run()
    app.button(key="confirm_wedding_ceremonies").click().run()
    app.button(key="open_wedding_projects").click().run()
    app.button(key=f"continue_wedding_project_{first['project_id']}").click().run()
    app.session_state["page"] = "wedding_ceremonies"
    app.run()
    assert app.checkbox(key=f"{CEREMONY_CHECKBOX_PREFIX}reception").value
    assert not app.checkbox(key=f"{CEREMONY_CHECKBOX_PREFIX}engagement").value
    assert not app.checkbox(key=f"{CEREMONY_CHECKBOX_PREFIX}haldi").value
    assert [item["ceremony_id"] for item in repository.rows[1]["selected_ceremonies"]] == ["engagement"]
    assert not app.exception


def test_confirmed_custom_and_catalog_ceremonies_restore_after_logout_login(app_environment):
    repository, project, app = _open_ceremonies(app_environment)
    app.text_input(key="custom_ceremony_new_name").set_value("Family welcome")
    app.button(key="add_custom_ceremony_submit").click().run()
    custom_id = app.session_state[CUSTOM_CEREMONIES_STATE_KEY][0].ceremony_id
    for ceremony_id in (custom_id, "reception"):
        app.checkbox(key=f"{CEREMONY_CHECKBOX_PREFIX}{ceremony_id}").check().run()
    app.button(key="confirm_wedding_ceremonies").click().run()
    with (
        patch("ai_event_planning_platform.home.logout_user", return_value=True),
        patch("ai_event_planning_platform.home.login_user", return_value=TOKENS),
    ):
        app.button(key="logout").click().run()
        assert CEREMONIES_STATE_KEY not in app.session_state
        app.text_input(key="login_email").set_value("planner@example.invalid")
        app.text_input(key="login_password").set_value("not-stored")
        app.button(key="login_submit").click().run()
        app.button(key=f"continue_wedding_project_{project['project_id']}").click().run()
    app.session_state["page"] = "wedding_ceremonies"
    app.run()
    assert app.checkbox(key=f"{CEREMONY_CHECKBOX_PREFIX}{custom_id}").value
    assert app.checkbox(key=f"{CEREMONY_CHECKBOX_PREFIX}reception").value
    assert app.session_state[CUSTOM_CEREMONIES_STATE_KEY][0].name == "Family welcome"
    assert any("saved to this wedding project" in item.value for item in app.success)
    assert not app.exception


def _open_requirements(app_environment, ceremonies=()):
    repository, create_app = app_environment
    profile = WeddingProfile(city="Pune", state="Maharashtra", guest_count=150)
    project = _seed_project(repository, profile=profile)
    repository.update_project(
        str(project["project_id"]), selected_ceremonies=list(ceremonies),
        planning_step="step_by_step",
    )
    app = create_app()
    app.button(key=f"continue_wedding_project_{project['project_id']}").click().run()
    app.button(key="wedding_dashboard_details").click().run()
    return repository, project, app


def _selected_ceremony(ceremony_id):
    return Ceremony(**{**CEREMONY_BY_ID[ceremony_id].__dict__, "selected": True})


def test_unified_summary_displays_saved_catalog_and_custom_ceremonies(app_environment):
    reception = _selected_ceremony("reception")
    custom = Ceremony(str(uuid4()), "Family welcome", "A gathering for both families.", is_custom=True, selected=True)
    repository, project, app = _open_requirements(app_environment, [reception, custom])
    assert "Your Confirmed Wedding Ceremonies" in [item.value for item in app.subheader]
    assert "**Reception**" in [item.value for item in app.markdown]
    assert "**Family welcome**" in [item.value for item in app.markdown]
    assert reception.description in [item.value for item in app.caption]
    assert custom.description in [item.value for item in app.caption]
    assert "City: Pune" in [item.value for item in app.text]
    assert "State: Maharashtra" in [item.value for item in app.text]
    assert "Guest count: 150" in [item.value for item in app.text]
    assert app.session_state[DRAFT_STATE_KEY].city == "Pune"
    assert app.button(key="choose_ceremonies_from_profile").label == "Edit Ceremonies"
    app.button(key="choose_ceremonies_from_profile").click().run()
    assert app.session_state["page"] == "wedding_ceremonies"
    assert app.checkbox(key=f"{CEREMONY_CHECKBOX_PREFIX}reception").value
    assert app.checkbox(key=f"{CEREMONY_CHECKBOX_PREFIX}{custom.ceremony_id}").value
    assert not app.exception


def test_unified_summary_empty_state(app_environment):
    _repository, _project, app = _open_requirements(app_environment)
    assert "No ceremonies selected yet." in [item.value for item in app.markdown]
    assert not app.error
    assert not app.exception


def test_unified_summary_save_and_reload(app_environment):
    repository, project, app = _open_requirements(app_environment)
    app.button(key="choose_ceremonies_from_profile").click().run()
    app.checkbox(key=f"{CEREMONY_CHECKBOX_PREFIX}reception").check().run()
    app.button(key="confirm_wedding_ceremonies").click().run()
    app.session_state["page"] = "step_by_step"
    app.run()
    app.text_input(key=f"wedding_{project['project_id']}_city_value").set_value("Jaipur")
    app.button(key=f"wedding_{project['project_id']}_save_profile").click().run()
    assert "**Reception**" in [item.value for item in app.markdown]
    assert repository.rows[0]["wedding_profile"]["city"] == "Jaipur"
    assert [item["ceremony_id"] for item in repository.rows[0]["selected_ceremonies"]] == ["reception"]
    _repository, create_app = app_environment
    reopened = create_app()
    reopened.button(key=f"continue_wedding_project_{project['project_id']}").click().run()
    assert reopened.session_state["page"] == "wedding_dashboard"
    reopened.button(key="wedding_dashboard_details").click().run()
    assert reopened.session_state["page"] == "step_by_step"
    assert "**Reception**" in [item.value for item in reopened.markdown]
    assert "City: Jaipur" in [item.value for item in reopened.text]
    assert not reopened.exception


def test_unified_summary_switches_between_projects(app_environment):
    repository, first, app = _open_requirements(app_environment, [_selected_ceremony("reception")])
    second = _seed_project(repository, name="Second wedding", profile=WeddingProfile(city="Jaipur"))
    repository.update_project(str(second["project_id"]), selected_ceremonies=[_selected_ceremony("haldi")], planning_step="step_by_step")
    app.button(key="open_wedding_projects").click().run()
    app.button(key=f"continue_wedding_project_{second['project_id']}").click().run()
    app.button(key="wedding_dashboard_details").click().run()
    assert "**Haldi / Halad**" in [item.value for item in app.markdown]
    assert "**Reception**" not in [item.value for item in app.markdown]
    assert "City: Jaipur" in [item.value for item in app.text]
    app.button(key="open_wedding_projects").click().run()
    app.button(key=f"continue_wedding_project_{first['project_id']}").click().run()
    app.button(key="wedding_dashboard_details").click().run()
    assert "**Reception**" in [item.value for item in app.markdown]
    assert "**Haldi / Halad**" not in [item.value for item in app.markdown]
    assert "City: Pune" in [item.value for item in app.text]
    assert not app.exception


def test_unified_summary_excludes_unconfirmed_and_failed_ceremony_edits(app_environment):
    repository, _project, app = _open_requirements(app_environment, [_selected_ceremony("reception")])
    app.button(key="choose_ceremonies_from_profile").click().run()
    app.checkbox(key=f"{CEREMONY_CHECKBOX_PREFIX}reception").uncheck().run()
    app.checkbox(key=f"{CEREMONY_CHECKBOX_PREFIX}haldi").check().run()
    app.session_state["page"] = "step_by_step"
    app.run()
    assert "**Reception**" in [item.value for item in app.markdown]
    assert "**Haldi / Halad**" not in [item.value for item in app.markdown]
    app.button(key="choose_ceremonies_from_profile").click().run()
    repository.fail_updates = True
    app.button(key="confirm_wedding_ceremonies").click().run()
    app.session_state["page"] = "step_by_step"
    app.run()
    assert "**Reception**" in [item.value for item in app.markdown]
    assert "**Haldi / Halad**" not in [item.value for item in app.markdown]
    assert app.session_state[CEREMONIES_STATE_KEY][0].ceremony_id == "haldi"
    assert app.session_state[PROJECT_DIRTY_KEY]
    assert not app.exception


def test_unified_summary_does_not_show_unconfirmed_custom_name(app_environment):
    custom = Ceremony(str(uuid4()), "Family welcome", "A family gathering.", is_custom=True, selected=True)
    _repository, _project, app = _open_requirements(app_environment, [custom])
    app.button(key="choose_ceremonies_from_profile").click().run()
    app.text_input(key=f"custom_ceremony_name_{custom.ceremony_id}").set_value("Unconfirmed family name")
    app.button(key=f"save_custom_ceremony_{custom.ceremony_id}").click().run()
    app.session_state["page"] = "step_by_step"
    app.run()
    assert "**Family welcome**" in [item.value for item in app.markdown]
    assert "**Unconfirmed family name**" not in [item.value for item in app.markdown]
    assert not app.exception


@pytest.mark.parametrize("invalid_state", [None, "invalid", ["invalid"], [CEREMONY_BY_ID["haldi"]]])
def test_unified_summary_reports_missing_or_invalid_saved_state(app_environment, invalid_state):
    _repository, _project, app = _open_requirements(app_environment)
    if invalid_state is None:
        del app.session_state[CEREMONIES_SAVED_KEY]
    else:
        app.session_state[CEREMONIES_SAVED_KEY] = invalid_state
    app.run()
    assert any("saved ceremonies could not be loaded" in item.value for item in app.error)
    assert "No ceremonies selected yet." not in [item.value for item in app.markdown]
    assert "City: Pune" in [item.value for item in app.text]
    assert app.button(key="choose_ceremonies_from_profile").label == "Edit Ceremonies"
    assert not app.exception


def test_unified_summary_does_not_reload_project_on_reruns(app_environment):
    repository, _project, app = _open_requirements(app_environment, [_selected_ceremony("reception")])
    with (
        patch.object(repository, "load_selected_project", wraps=repository.load_selected_project) as load,
        patch.object(repository, "update_project", wraps=repository.update_project) as update,
    ):
        app.run()
        app.selectbox(key=f"wedding_{app.session_state[ACTIVE_PROJECT_ID_KEY]}_date_status").set_value("Approximate Date").run()
        app.run()
    load.assert_not_called()
    update.assert_not_called()
    assert "**Reception**" in [item.value for item in app.markdown]
    assert not app.exception


def test_dashboard_shows_friendly_empty_state(app_environment) -> None:
    _repository, create_app = app_environment
    app = create_app()

    assert app.title[0].value == "My Wedding Projects"
    assert any("create your first project" in item.value for item in app.info)
    assert app.button(key="start_new_wedding").label == "Start New Wedding"
    assert not app.exception


def test_navigation_retries_failed_details_before_clearing_dirty_state(app_environment):
    from ai_event_planning_platform.wedding_project_state import (
        navigate_project_to_page, persist_active_project,
    )

    repository, _ = app_environment
    project = _seed_project(repository)
    state = {ACTIVE_PROJECT_ID_KEY: project["project_id"]}
    edited = WeddingProfile(city="Bareilly")
    repository.fail_updates = True
    with pytest.raises(RuntimeError):
        persist_active_project(state, profile=edited)
    assert state[PROJECT_DIRTY_KEY]
    repository.fail_updates = False
    navigate_project_to_page(state, "wedding_ceremonies")
    assert repository.rows[0]["wedding_profile"]["city"] == "Bareilly"
    assert not state.get(PROJECT_DIRTY_KEY)


def test_malformed_project_does_not_clear_current_workspace(app_environment):
    from ai_event_planning_platform.wedding_project_state import restore_project_workspace
    from ai_event_planning_platform.wedding_projects import ProjectSerializationError

    repository, _ = app_environment
    project = _seed_project(repository)
    state = {DRAFT_STATE_KEY: WeddingProfile(city="Pune")}
    project["selected_ceremonies"] = "invalid"
    with pytest.raises(ProjectSerializationError):
        restore_project_workspace(state, project)
    assert state[DRAFT_STATE_KEY].city == "Pune"


def test_switching_projects_clears_failed_pending_save(app_environment):
    from ai_event_planning_platform.wedding_project_state import (
        PENDING_SAVE_KEY, persist_active_project, restore_project_workspace,
    )

    repository, _ = app_environment
    first = _seed_project(repository)
    second = _seed_project(repository, name="Second")
    state = {ACTIVE_PROJECT_ID_KEY: first["project_id"]}
    repository.fail_updates = True
    with pytest.raises(RuntimeError):
        persist_active_project(state, profile=WeddingProfile(city="Pune"))
    restore_project_workspace(state, second)
    assert PENDING_SAVE_KEY not in state
    repository.fail_updates = False
    persist_active_project(state, planning_step="step_by_step")
    assert repository.rows[1]["wedding_profile"]["city"] is None


def test_start_new_wedding_creates_an_independent_project(app_environment) -> None:
    repository, create_app = app_environment
    app = create_app()

    app.text_input(key="new_wedding_project_name").set_value("First wedding")
    app.button(key="start_new_wedding").click().run()
    first_id = app.session_state[ACTIVE_PROJECT_ID_KEY]
    assert app.session_state["page"] == "wedding_dashboard"

    app.button(key="open_wedding_projects").click().run()
    app.text_input(key="new_wedding_project_name").set_value("Second wedding")
    app.button(key="start_new_wedding").click().run()
    second_id = app.session_state[ACTIVE_PROJECT_ID_KEY]

    assert first_id != second_id
    assert len(repository.rows) == 2
    assert {row["project_name"] for row in repository.rows} == {
        "First wedding",
        "Second wedding",
    }
    assert app.session_state["page"] == "wedding_dashboard"
    assert not app.exception


def test_continue_planning_restores_profile_ceremonies_and_progress(app_environment) -> None:
    repository, create_app = app_environment
    profile = WeddingProfile(
        wedding_date_status=WeddingDateStatus.FIXED_DATE,
        wedding_date=date(2027, 11, 20),
        city="Jaipur",
        state="Rajasthan",
    )
    project = _seed_project(repository, profile=profile)
    reception = Ceremony(
        **{**CEREMONY_BY_ID["reception"].__dict__, "selected": True}
    )
    repository.update_project(
        str(project["project_id"]),
        profile=profile,
        selected_ceremonies=[reception],
        planning_step="wedding_ceremonies",
    )
    _seed_project(repository, name="Someone else's wedding", owner_id=OTHER_OWNER_ID)
    app = create_app()
    app.session_state["wedding_followup_answered"] = ["city"]
    app.session_state["wedding_followup_city_value"] = "Old project city"

    app.button(key=f"continue_wedding_project_{project['project_id']}").click().run()

    assert app.session_state["page"] == "wedding_dashboard"
    assert app.session_state[DRAFT_STATE_KEY] == profile
    assert [item.ceremony_id for item in app.session_state["wedding_ceremonies_selected"]] == [
        "reception"
    ]
    assert "wedding_followup_answered" not in app.session_state
    assert "wedding_followup_city_value" not in app.session_state
    assert not app.exception


def test_project_form_save_failure_keeps_edits_without_success_message(
    app_environment,
) -> None:
    repository, create_app = app_environment
    project = _seed_project(repository)
    repository.fail_updates = True
    app = create_app("step_by_step")
    app.session_state[ACTIVE_PROJECT_ID_KEY] = project["project_id"]
    app.session_state[LOADED_PROJECT_ID_KEY] = project["project_id"]
    app.session_state[DRAFT_STATE_KEY] = WeddingProfile()
    app.session_state[DRAFT_SAVED_STATE_KEY] = False
    app.run()

    app.text_input(key=f"wedding_{project['project_id']}_city_value").set_value(
        "Bareilly"
    )
    app.button(key=f"wedding_{project['project_id']}_save_profile").click().run()

    assert app.session_state[DRAFT_STATE_KEY].city == "Bareilly"
    assert app.session_state[PROJECT_DIRTY_KEY] is True
    assert any("could not be saved to the database" in item.value for item in app.error)
    assert not app.success
    assert repository.rows[0]["wedding_profile"]["city"] is None
    assert not app.exception


def test_unauthorized_active_project_is_not_restored(app_environment) -> None:
    repository, create_app = app_environment
    foreign = _seed_project(repository, owner_id=OTHER_OWNER_ID)
    app = create_app("step_by_step")
    app.session_state[ACTIVE_PROJECT_ID_KEY] = foreign["project_id"]
    app.run()

    assert app.session_state["page"] == PROJECTS_PAGE
    assert ACTIVE_PROJECT_ID_KEY not in app.session_state
    assert DRAFT_STATE_KEY not in app.session_state
    assert any("unavailable for your account" in item.value for item in app.error)
    assert not app.exception


def test_logout_then_login_keeps_database_project_and_restores_it(app_environment, monkeypatch) -> None:
    repository, create_app = app_environment
    profile = WeddingProfile(city="Pune", state="Maharashtra")
    project = _seed_project(repository, profile=profile)
    app = create_app()
    app.session_state[ACTIVE_PROJECT_ID_KEY] = project["project_id"]
    app.session_state[LOADED_PROJECT_ID_KEY] = project["project_id"]
    app.session_state[DRAFT_STATE_KEY] = profile
    app.run()

    with (
        patch("ai_event_planning_platform.home.logout_user", return_value=True),
        patch("ai_event_planning_platform.home.login_user", return_value=TOKENS),
    ):
        app.button(key="logout").click().run()
        assert ACTIVE_PROJECT_ID_KEY not in app.session_state
        assert DRAFT_STATE_KEY not in app.session_state
        assert repository.rows[0]["project_id"] == project["project_id"]

        app.text_input(key="login_email").set_value("planner@example.invalid")
        app.text_input(key="login_password").set_value("not-stored")
        app.button(key="login_submit").click().run()
        app.button(key=f"continue_wedding_project_{project['project_id']}").click().run()

    assert app.session_state[DRAFT_STATE_KEY] == profile
    assert app.session_state["page"] == "wedding_dashboard"
    assert "login_password" not in app.session_state
    assert not app.exception
