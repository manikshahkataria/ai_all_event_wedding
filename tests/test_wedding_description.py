from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from ai_event_planning_platform.auth import AuthSessionTokens, store_auth_session
from ai_event_planning_platform.wedding_extraction import WeddingExtractionError
from ai_event_planning_platform.wedding_description import (
    DRAFT_STATE_KEY,
    EXTRACTION_PROFILE_STATE_KEY,
    missing_wedding_details,
)
from ai_event_planning_platform.wedding_form import DRAFT_SAVED_STATE_KEY
from ai_event_planning_platform.wedding_profile import WeddingProfile, WeddingStyle


SAMPLE_CANDIDATE = WeddingProfile(city="Jaipur", style=WeddingStyle.MODERN)


@pytest.fixture
def description_app() -> AppTest:
    app = AppTest.from_file("../app.py")
    app.session_state["page"] = "describe_wedding"
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


def _submit_extraction(app: AppTest) -> None:
    app.text_area(key="wedding_ai_description").set_value(
        "We plan a modern wedding in Jaipur."
    )
    app.button(key="extract_wedding_details").click().run()


def test_description_page_starts_with_editable_sample(description_app: AppTest) -> None:
    app = description_app

    assert app.title[0].value == "Describe Your Dream Wedding"
    assert "destination wedding in Jaipur" in app.text_area(
        key="wedding_ai_description"
    ).value
    voice_button = app.button(key="wedding_voice_input")
    assert voice_button.disabled
    assert voice_button.help == (
        "Soon you'll be able to describe your wedding using your voice."
    )
    assert not app.exception


def test_description_page_uses_mocked_extraction_and_separate_candidate(
    description_app: AppTest,
) -> None:
    app = description_app
    existing_draft = WeddingProfile(city="Bareilly")
    app.session_state[DRAFT_STATE_KEY] = existing_draft
    app.session_state[DRAFT_SAVED_STATE_KEY] = True
    with patch(
        "ai_event_planning_platform.wedding_description.extract_wedding_profile",
        return_value=SAMPLE_CANDIDATE,
    ) as extract:
        _submit_extraction(app)

    extract.assert_called_once_with("We plan a modern wedding in Jaipur.")
    assert app.title[0].value == "Describe Your Dream Wedding"
    assert app.session_state[EXTRACTION_PROFILE_STATE_KEY] == SAMPLE_CANDIDATE
    assert app.session_state[DRAFT_STATE_KEY] == existing_draft
    assert app.button(key="accept_wedding_extraction").disabled
    assert any("State" in item.value for item in app.markdown)
    assert not app.exception


def test_review_edits_are_saved_only_after_explicit_acceptance(
    description_app: AppTest,
) -> None:
    app = description_app
    with patch(
        "ai_event_planning_platform.wedding_description.extract_wedding_profile",
        return_value=SAMPLE_CANDIDATE,
    ):
        _submit_extraction(app)

    assert DRAFT_STATE_KEY not in app.session_state
    app.text_input(key="ai_review_wedding_city_value").set_value("Udaipur")
    app.button(key="ai_review_wedding_save_profile").click().run()

    assert app.session_state[EXTRACTION_PROFILE_STATE_KEY].city == "Udaipur"
    assert DRAFT_STATE_KEY not in app.session_state
    app.button(key="accept_wedding_extraction").click().run()

    assert app.session_state[DRAFT_STATE_KEY].city == "Udaipur"
    assert app.session_state[DRAFT_SAVED_STATE_KEY] is True
    assert not app.exception


def test_existing_draft_requires_explicit_replace_confirmation(
    description_app: AppTest,
) -> None:
    app = description_app
    existing_draft = WeddingProfile(city="Bareilly")
    app.session_state[DRAFT_STATE_KEY] = existing_draft
    app.session_state[DRAFT_SAVED_STATE_KEY] = True
    with patch(
        "ai_event_planning_platform.wedding_description.extract_wedding_profile",
        return_value=SAMPLE_CANDIDATE,
    ):
        _submit_extraction(app)

    assert app.button(key="accept_wedding_extraction").disabled
    assert app.session_state[DRAFT_STATE_KEY] == existing_draft
    app.checkbox(key="wedding_ai_replace_existing_draft").check().run()
    app.button(key="accept_wedding_extraction").click().run()

    assert app.session_state[DRAFT_STATE_KEY] == SAMPLE_CANDIDATE
    assert app.session_state[DRAFT_SAVED_STATE_KEY] is True
    assert not app.exception


def test_failed_extraction_preserves_description_and_active_draft(
    description_app: AppTest,
) -> None:
    app = description_app
    existing_draft = WeddingProfile(city="Bareilly")
    app.session_state[DRAFT_STATE_KEY] = existing_draft
    with patch(
        "ai_event_planning_platform.wedding_description.extract_wedding_profile",
        side_effect=WeddingExtractionError("Could not connect to Groq."),
    ) as extract:
        _submit_extraction(app)

    extract.assert_called_once()
    assert app.text_area(key="wedding_ai_description").value == (
        "We plan a modern wedding in Jaipur."
    )
    assert app.session_state[DRAFT_STATE_KEY] == existing_draft
    assert "provider detail" not in app.error[0].value
    assert not app.exception


@pytest.mark.parametrize(
    ("profile", "expected_missing"),
    [
        (WeddingProfile(), {"Wedding date", "City", "Guest count", "Total budget"}),
        (SAMPLE_CANDIDATE, {"Wedding date", "Guest count", "Total budget"}),
    ],
)
def test_missing_details_are_reported(
    profile: WeddingProfile, expected_missing: set[str]
) -> None:
    assert expected_missing.issubset(set(missing_wedding_details(profile)))