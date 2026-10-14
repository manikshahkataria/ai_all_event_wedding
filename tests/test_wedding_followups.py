from datetime import date
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from ai_event_planning_platform.auth import AuthSessionTokens, store_auth_session
from ai_event_planning_platform.home import WEDDING_FOLLOWUPS_PAGE
from ai_event_planning_platform.wedding_followups import (
    FOLLOWUP_ANSWERED_KEY,
    FOLLOWUP_SKIPPED_KEY,
    missing_followup_ids,
    provided_detail_count,
)
from ai_event_planning_platform.wedding_form import DRAFT_SAVED_STATE_KEY, DRAFT_STATE_KEY
from ai_event_planning_platform.wedding_profile import (
    CulturalBackground,
    TraditionPreference,
    WeddingDateStatus,
    WeddingLocationPreference,
    WeddingProfile,
    WeddingStyle,
)


TODAY = date(2026, 10, 15)


@pytest.fixture
def followup_app() -> AppTest:
    app = AppTest.from_file("../app.py")
    app.session_state["page"] = WEDDING_FOLLOWUPS_PAGE
    app.session_state[DRAFT_STATE_KEY] = WeddingProfile(
        city="Bareilly",
        state="Uttar Pradesh",
    )
    app.session_state[DRAFT_SAVED_STATE_KEY] = True
    store_auth_session(
        app.session_state,
        AuthSessionTokens("test-access-token", "test-refresh-token"),
    )
    with patch(
        "ai_event_planning_platform.home.restore_authenticated_session",
        side_effect=lambda access, refresh: AuthSessionTokens(access, refresh),
    ):
        app.run()
        yield app


def _complete_profile(**overrides: object) -> WeddingProfile:
    fields: dict[str, object] = {
        "wedding_date_status": WeddingDateStatus.FIXED_DATE,
        "wedding_date": date(2027, 11, 20),
        "city": "Bareilly",
        "state": "Uttar Pradesh",
        "location_preference": WeddingLocationPreference.LOCAL,
        "guest_count": 250,
        "budget_inr": 1300000,
        "style": WeddingStyle.TRADITIONAL,
        "partner_1_culture": CulturalBackground("Punjabi", "Punjab"),
        "partner_2_culture": CulturalBackground("Tamil", "Tamil Nadu"),
        "traditions": (TraditionPreference.CUSTOM,),
    }
    fields.update(overrides)
    return WeddingProfile(**fields)  # type: ignore[arg-type]


def test_missing_fields_and_progress_use_existing_values() -> None:
    profile = WeddingProfile(city="Bareilly", state="Uttar Pradesh")

    missing = missing_followup_ids(profile)

    assert "city" not in missing
    assert "state" not in missing
    assert missing[0] == "wedding_date"
    assert provided_detail_count(profile) == 2


def test_only_one_relevant_question_is_shown_and_existing_fields_are_not_reasked(
    followup_app: AppTest,
) -> None:
    app = followup_app

    assert app.title[0].value == "Complete Wedding Details"
    assert app.subheader[-1].value == "Wedding date"
    assert "2 of 12 planning details provided." in [item.value for item in app.markdown]
    assert not any(item.value == "City" for item in app.subheader)
    assert not app.exception


def test_valid_date_answer_updates_active_profile_and_advances(
    followup_app: AppTest,
) -> None:
    app = followup_app
    app.selectbox(key="wedding_followup_wedding_date_status").select("Fixed Date").run()
    app.date_input(key="wedding_followup_wedding_date_value").set_value(date(2027, 11, 20)).run()
    app.button(key="wedding_followup_save").click().run()

    profile = app.session_state[DRAFT_STATE_KEY]
    assert profile.wedding_date_status is WeddingDateStatus.FIXED_DATE
    assert profile.wedding_date == date(2027, 11, 20)
    assert "wedding_date" in app.session_state[FOLLOWUP_ANSWERED_KEY]
    assert app.subheader[-1].value == "Local or destination preference"
    assert not app.exception


def test_invalid_guest_answer_is_rejected_without_changing_profile() -> None:
    app = AppTest.from_file("../app.py")
    app.session_state["page"] = WEDDING_FOLLOWUPS_PAGE
    profile = _complete_profile(guest_count=None)
    app.session_state[DRAFT_STATE_KEY] = profile
    app.session_state[DRAFT_SAVED_STATE_KEY] = True
    store_auth_session(
        app.session_state,
        AuthSessionTokens("test-access-token", "test-refresh-token"),
    )
    with patch(
        "ai_event_planning_platform.home.restore_authenticated_session",
        side_effect=lambda access, refresh: AuthSessionTokens(access, refresh),
    ):
        app.run()
        app.number_input(key="wedding_followup_guest_count_value").set_value(-3)
        app.button(key="wedding_followup_save").click().run()

    assert app.session_state[DRAFT_STATE_KEY] == profile
    assert "Guest count must be a positive whole number." in [item.value for item in app.error]
    assert not app.exception


def test_skip_advances_and_keeps_question_in_unanswered_list(
    followup_app: AppTest,
) -> None:
    app = followup_app
    app.button(key="wedding_followup_skip").click().run()

    assert "wedding_date" in app.session_state[FOLLOWUP_SKIPPED_KEY]
    assert app.subheader[-1].value == "Local or destination preference"
    assert any("Wedding date" in item.value for item in app.caption)
    assert not app.exception


def test_previous_answer_can_be_reopened_and_updated(
    followup_app: AppTest,
) -> None:
    app = followup_app
    app.selectbox(key="wedding_followup_wedding_date_status").select("Fixed Date").run()
    app.date_input(key="wedding_followup_wedding_date_value").set_value(date(2027, 11, 20)).run()
    app.button(key="wedding_followup_save").click().run()

    app.selectbox(key="wedding_followup_edit_select").select("Wedding date").run()
    app.button(key="wedding_followup_edit_button").click().run()
    assert app.subheader[-1].value == "Wedding date"
    app.date_input(key="wedding_followup_wedding_date_value").set_value(date(2028, 2, 10)).run()
    app.button(key="wedding_followup_save").click().run()

    assert app.session_state[DRAFT_STATE_KEY].wedding_date == date(2028, 2, 10)
    assert "wedding_date" in app.session_state[FOLLOWUP_ANSWERED_KEY]
    assert not app.exception


def test_cultural_background_does_not_imply_religious_traditions() -> None:
    app = AppTest.from_file("../app.py")
    app.session_state["page"] = WEDDING_FOLLOWUPS_PAGE
    app.session_state[DRAFT_STATE_KEY] = _complete_profile(traditions=())
    app.session_state[DRAFT_SAVED_STATE_KEY] = True
    store_auth_session(
        app.session_state,
        AuthSessionTokens("test-access-token", "test-refresh-token"),
    )
    with patch(
        "ai_event_planning_platform.home.restore_authenticated_session",
        side_effect=lambda access, refresh: AuthSessionTokens(access, refresh),
    ):
        app.run()

    assert app.subheader[-1].value == "Tradition preferences"
    assert any(
        "does not imply a particular ceremony" in item.value for item in app.caption
    )
    assert app.multiselect(key="wedding_followup_traditions_values").value == []
    assert app.session_state[DRAFT_STATE_KEY].traditions == ()
    assert not app.exception


def test_continue_with_available_details_returns_to_step_by_step(
    followup_app: AppTest,
) -> None:
    app = followup_app
    app.session_state[FOLLOWUP_SKIPPED_KEY] = list(missing_followup_ids(app.session_state[DRAFT_STATE_KEY]))
    app.run()

    assert any("unanswered" in item.value.lower() for item in app.markdown)
    app.button(key="wedding_followup_continue").click().run()

    assert app.session_state["page"] == "step_by_step"
    assert app.title[0].value == "Plan Step by Step"
    assert app.session_state[DRAFT_STATE_KEY].city == "Bareilly"
    assert not app.exception


def test_followup_route_remains_protected() -> None:
    app = AppTest.from_file("../app.py")
    app.session_state["page"] = WEDDING_FOLLOWUPS_PAGE
    app.run()

    assert app.session_state["page"] == "login"
    assert app.title[0].value == "Log in"
    assert not app.exception