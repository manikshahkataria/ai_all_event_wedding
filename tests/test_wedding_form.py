from datetime import date
from enum import Enum
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from ai_event_planning_platform.auth import AuthSessionTokens, store_auth_session
from ai_event_planning_platform.wedding_form import (
    DRAFT_SAVED_STATE_KEY,
    DRAFT_STATE_KEY,
)
from ai_event_planning_platform.wedding_profile import (
    CulturalBackground,
    TraditionPreference,
    WeddingDateStatus,
    WeddingLocationPreference,
    WeddingProfile,
    WeddingStyle,
)


@pytest.fixture
def wedding_form_app() -> AppTest:
    app = AppTest.from_file("../app.py")
    app.session_state["page"] = "step_by_step"
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


def test_requirements_form_allows_incomplete_optional_draft(
    wedding_form_app: AppTest,
) -> None:
    app = wedding_form_app
    assert app.title[0].value == "Plan Step by Step"
    app.button(key="save_wedding_profile").click().run()

    assert not app.exception
    assert app.session_state[DRAFT_STATE_KEY] == WeddingProfile()
    assert app.session_state[DRAFT_SAVED_STATE_KEY] is True
    assert any("Wedding date: Not Decided Yet" in item.value for item in app.text)


def test_form_saves_plain_fields_and_formats_indian_budget(
    wedding_form_app: AppTest,
) -> None:
    app = wedding_form_app
    app.selectbox(key="wedding_date_status").select("Approximate Date").run()
    app.date_input(key="wedding_date_value").set_value(date(2027, 5, 1))
    app.text_input(key="wedding_city_value").set_value("Bareilly")
    app.text_input(key="wedding_state_value").set_value("Uttar Pradesh")
    app.selectbox(key="wedding_location_preference").select("Local wedding")
    app.number_input(key="wedding_guest_count_value").set_value(300)
    app.number_input(key="wedding_budget_inr_value").set_value(1300000)
    app.selectbox(key="wedding_style_value").select("Traditional")
    app.text_input(key="wedding_partner_1_background_value").set_value("Punjabi")
    app.text_input(key="wedding_partner_1_region_value").set_value("Punjab")
    app.multiselect(key="wedding_traditions_value").set_value(
        ["Religious", "Custom"]
    )
    app.text_area(key="wedding_additional_preferences_value").set_value("Outdoor venue")
    app.button(key="save_wedding_profile").click().run()

    profile = app.session_state[DRAFT_STATE_KEY]
    assert profile.wedding_date_status is WeddingDateStatus.APPROXIMATE_DATE
    assert profile.wedding_date == date(2027, 5, 1)
    assert profile.city == "Bareilly"
    assert profile.state == "Uttar Pradesh"
    assert profile.location_preference is WeddingLocationPreference.LOCAL
    assert profile.guest_count == 300
    assert profile.budget_inr == 1300000
    assert profile.style is WeddingStyle.TRADITIONAL
    assert profile.partner_1_culture == CulturalBackground("Punjabi", "Punjab")
    assert profile.traditions == (
        TraditionPreference.RELIGIOUS,
        TraditionPreference.CUSTOM,
    )
    assert profile.additional_preferences == "Outdoor venue"
    summary = [item.value for item in app.text]
    assert "City: Bareilly" in summary
    assert "Guest count: 300" in summary
    assert "Budget: ₹13,00,000" in summary
    assert not any("(Confirmed)" in item or "(Approximate)" in item for item in summary)
    assert not any("certainty" in item.label.lower() for item in app.selectbox)
    assert not app.exception


def test_invalid_guest_count_keeps_existing_draft_unchanged(
    wedding_form_app: AppTest,
) -> None:
    app = wedding_form_app
    app.button(key="save_wedding_profile").click().run()
    saved_profile = app.session_state[DRAFT_STATE_KEY]

    app.number_input(key="wedding_guest_count_value").set_value(-5)
    app.button(key="save_wedding_profile").click().run()

    assert app.session_state[DRAFT_STATE_KEY] == saved_profile
    assert any(
        "Guest count must be a positive whole number." in item.value
        for item in app.error
    )
    assert not app.exception


def test_draft_survives_navigation_away_and_back(wedding_form_app: AppTest) -> None:
    app = wedding_form_app
    app.text_input(key="wedding_city_value").set_value("Bareilly")
    app.button(key="save_wedding_profile").click().run()
    saved_profile = app.session_state[DRAFT_STATE_KEY]
    app.button(key="back_from_step_by_step").click().run()

    assert app.title[0].value == "How Would You Like to Start Planning?"
    app.button(key="choose_step_by_step").click().run()

    assert app.session_state[DRAFT_STATE_KEY] == saved_profile
    assert app.title[0].value == "Plan Step by Step"
    assert any("Requirements summary" in item.value for item in app.subheader)
    assert not app.exception


def test_existing_legacy_draft_is_migrated_and_fields_remain_editable(
    wedding_form_app: AppTest,
) -> None:
    app = wedding_form_app

    class OldCertainty(Enum):
        CONFIRMED = "confirmed"
        APPROXIMATE = "approximate"
        UNDECIDED = "undecided"

    def answer(value: object, certainty: OldCertainty) -> SimpleNamespace:
        return SimpleNamespace(value=value, certainty=certainty)

    app.session_state[DRAFT_STATE_KEY] = SimpleNamespace(
        wedding_date=answer("2027-05", OldCertainty.APPROXIMATE),
        city=answer("Bareilly", OldCertainty.CONFIRMED),
        state=answer("Uttar Pradesh", OldCertainty.CONFIRMED),
        location_preference=answer(
            SimpleNamespace(value="destination"), OldCertainty.CONFIRMED
        ),
        guest_count=answer(300, OldCertainty.APPROXIMATE),
        budget_inr=answer(1300000, OldCertainty.CONFIRMED),
        style=answer(SimpleNamespace(value="traditional"), OldCertainty.CONFIRMED),
        partner_1_culture=answer(
            SimpleNamespace(background="Punjabi", region="Punjab"),
            OldCertainty.CONFIRMED,
        ),
        partner_2_culture=answer(None, OldCertainty.UNDECIDED),
        traditions=answer(
            (SimpleNamespace(value="religious"),), OldCertainty.CONFIRMED
        ),
        additional_preferences=answer("Outdoor", OldCertainty.APPROXIMATE),
    )
    app.session_state[DRAFT_SAVED_STATE_KEY] = True
    app.session_state["wedding_date_certainty"] = "Approximate month"
    app.session_state["wedding_city_certainty"] = "Confirmed"
    app.run()

    migrated = app.session_state[DRAFT_STATE_KEY]
    assert migrated.wedding_date_status is WeddingDateStatus.APPROXIMATE_DATE
    assert migrated.city == "Bareilly"
    assert migrated.guest_count == 300
    assert migrated.budget_inr == 1300000
    assert app.session_state["wedding_date_status"] == "Approximate Date"
    assert "wedding_city_certainty" not in app.session_state
    assert not any("certainty" in item.label.lower() for item in app.selectbox)
    assert not app.exception