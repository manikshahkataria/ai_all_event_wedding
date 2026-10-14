from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from ai_event_planning_platform.auth import AuthSessionTokens, store_auth_session
from ai_event_planning_platform.home import WEDDING_CEREMONIES_PAGE, WEDDING_FOLLOWUPS_PAGE
from ai_event_planning_platform.wedding_ceremonies import (
    CEREMONIES_CONFIRMED_KEY,
    CEREMONIES_STATE_KEY,
    CEREMONY_CHECKBOX_PREFIX,
    CUSTOM_CEREMONIES_STATE_KEY,
    Ceremony,
    recommend_ceremonies,
)
from ai_event_planning_platform.wedding_form import DRAFT_SAVED_STATE_KEY, DRAFT_STATE_KEY
from ai_event_planning_platform.wedding_profile import (
    CulturalBackground,
    TraditionPreference,
    WeddingProfile,
)


def _ceremony_ids(profile: WeddingProfile) -> set[str]:
    suggestions, _ = recommend_ceremonies(profile)
    return {item.ceremony.ceremony_id for item in suggestions}


def _build_app(page: str = WEDDING_CEREMONIES_PAGE) -> AppTest:
    app = AppTest.from_file("../app.py")
    app.session_state["page"] = page
    app.session_state[DRAFT_STATE_KEY] = WeddingProfile(
        partner_1_culture=CulturalBackground("Punjabi", "Punjab"),
    )
    app.session_state[DRAFT_SAVED_STATE_KEY] = True
    store_auth_session(
        app.session_state,
        AuthSessionTokens("test-access-token", "test-refresh-token"),
    )
    return app


@pytest.fixture
def ceremony_app() -> AppTest:
    app = _build_app()
    with patch(
        "ai_event_planning_platform.home.restore_authenticated_session",
        side_effect=lambda access, refresh: AuthSessionTokens(access, refresh),
    ):
        app.run()
        yield app


def test_recommendations_include_broad_events_and_explicit_context() -> None:
    profile = WeddingProfile(
        partner_1_culture=CulturalBackground("Punjabi Hindu", "Punjab"),
        traditions=(TraditionPreference.RELIGIOUS,),
    )

    ids = _ceremony_ids(profile)

    assert {"wedding_ceremony", "reception", "engagement"} <= ids
    assert {"haldi", "mehendi", "sangeet", "punjabi_hindu_jaimala"} <= ids


def test_mixed_cultural_profile_matches_each_explicit_tradition() -> None:
    profile = WeddingProfile(
        partner_1_culture=CulturalBackground("Punjabi Sikh", "Punjab"),
        partner_2_culture=CulturalBackground("Tamil Hindu", "Tamil Nadu"),
        traditions=(TraditionPreference.RELIGIOUS,),
    )

    ids = _ceremony_ids(profile)

    assert "punjabi_sikh_anand_karaj" in ids
    assert "tamil_hindu_kashi_yatra" in ids
    assert "punjabi_hindu_jaimala" not in ids


def test_ambiguous_religious_preference_requests_one_clarification() -> None:
    profile = WeddingProfile(
        partner_1_culture=CulturalBackground(None, "Punjab"),
        traditions=(TraditionPreference.RELIGIOUS,),
    )

    suggestions, clarification = recommend_ceremonies(profile)

    assert clarification is not None
    assert "Which tradition" in clarification
    assert not {
        "punjabi_hindu_jaimala",
        "punjabi_sikh_anand_karaj",
        "christian_wedding_service",
        "muslim_nikah",
    } & {item.ceremony.ceremony_id for item in suggestions}


def test_ambiguous_cultural_background_requests_targeted_clarification() -> None:
    profile = WeddingProfile(
        partner_1_culture=CulturalBackground("Punjabi", None),
    )

    _, clarification = recommend_ceremonies(profile)

    assert clarification is not None
    assert "specific religious or family tradition" in clarification


def test_region_does_not_assume_a_religion_or_preselect_ceremonies() -> None:
    profile = WeddingProfile(
        partner_1_culture=CulturalBackground(None, "Punjab"),
    )

    suggestions, _ = recommend_ceremonies(profile)

    assert not {
        "punjabi_hindu_jaimala",
        "punjabi_sikh_anand_karaj",
        "christian_wedding_service",
        "muslim_nikah",
    } & {item.ceremony.ceremony_id for item in suggestions}
    assert all(not item.ceremony.selected for item in suggestions)


def test_secular_preference_suppresses_religious_recommendations() -> None:
    profile = WeddingProfile(
        partner_1_culture=CulturalBackground("Punjabi Sikh", "Punjab"),
        traditions=(TraditionPreference.SECULAR,),
    )

    assert "punjabi_sikh_anand_karaj" not in _ceremony_ids(profile)


def test_selection_requires_explicit_confirmation_and_can_be_deselected(
    ceremony_app: AppTest,
) -> None:
    app = ceremony_app
    wedding_key = f"{CEREMONY_CHECKBOX_PREFIX}wedding_ceremony"
    wedding_checkbox = app.checkbox(key=wedding_key)
    assert not wedding_checkbox.value
    assert not app.checkbox(
        key=f"{CEREMONY_CHECKBOX_PREFIX}christian_wedding_service"
    ).value
    assert not app.checkbox(key=f"{CEREMONY_CHECKBOX_PREFIX}muslim_nikah").value

    wedding_checkbox.check().run()
    assert not app.session_state[CEREMONIES_CONFIRMED_KEY]
    assert app.session_state[CEREMONIES_STATE_KEY] == []

    app.button(key="confirm_wedding_ceremonies").click().run()
    assert app.session_state[CEREMONIES_CONFIRMED_KEY]
    assert [item.ceremony_id for item in app.session_state[CEREMONIES_STATE_KEY]] == [
        "wedding_ceremony"
    ]
    assert any("Next: Plan each ceremony's guests" in item.value for item in app.info)

    app.checkbox(key=wedding_key).uncheck().run()
    assert [item.ceremony_id for item in app.session_state[CEREMONIES_STATE_KEY]] == [
        "wedding_ceremony"
    ]
    app.button(key="confirm_wedding_ceremonies").click().run()
    assert app.session_state[CEREMONIES_STATE_KEY] == []


def test_custom_ceremony_can_be_created_renamed_and_deleted(
    ceremony_app: AppTest,
) -> None:
    app = ceremony_app
    app.text_input(key="custom_ceremony_new_name").set_value("Family blessing")
    app.button(key="add_custom_ceremony_submit").click().run()

    ceremony = app.session_state[CUSTOM_CEREMONIES_STATE_KEY][0]
    assert isinstance(ceremony, Ceremony)
    assert ceremony.is_custom
    name_key = f"custom_ceremony_name_{ceremony.ceremony_id}"
    app.text_input(key=name_key).set_value("Family welcome")
    app.button(key=f"save_custom_ceremony_{ceremony.ceremony_id}").click().run()
    assert app.session_state[CUSTOM_CEREMONIES_STATE_KEY][0].name == "Family welcome"

    app.button(key=f"delete_custom_ceremony_{ceremony.ceremony_id}").click().run()
    assert app.session_state[CUSTOM_CEREMONIES_STATE_KEY] == []
    assert not app.exception


def test_custom_ceremony_can_be_selected_and_confirmed(ceremony_app: AppTest) -> None:
    app = ceremony_app
    app.text_input(key="custom_ceremony_new_name").set_value("Family blessing")
    app.button(key="add_custom_ceremony_submit").click().run()
    ceremony = app.session_state[CUSTOM_CEREMONIES_STATE_KEY][0]

    app.checkbox(key=f"{CEREMONY_CHECKBOX_PREFIX}{ceremony.ceremony_id}").check().run()
    app.button(key="confirm_wedding_ceremonies").click().run()

    assert ceremony.ceremony_id in {
        item.ceremony_id for item in app.session_state[CEREMONIES_STATE_KEY]
    }


def test_confirmed_selection_is_preserved_after_leaving_and_revisiting(
    ceremony_app: AppTest,
) -> None:
    app = ceremony_app
    app.checkbox(key=f"{CEREMONY_CHECKBOX_PREFIX}reception").check().run()
    app.button(key="confirm_wedding_ceremonies").click().run()

    app.session_state["page"] = "step_by_step"
    app.run()
    app.session_state["page"] = WEDDING_CEREMONIES_PAGE
    app.run()

    assert app.checkbox(key=f"{CEREMONY_CHECKBOX_PREFIX}reception").value
    assert [item.ceremony_id for item in app.session_state[CEREMONIES_STATE_KEY]] == [
        "reception"
    ]
    assert not app.exception


def test_followup_page_links_to_ceremony_selection() -> None:
    app = _build_app(WEDDING_FOLLOWUPS_PAGE)
    with patch(
        "ai_event_planning_platform.home.restore_authenticated_session",
        side_effect=lambda access, refresh: AuthSessionTokens(access, refresh),
    ):
        app.run()
        app.button(key="choose_ceremonies_from_followups").click().run()

    assert app.session_state["page"] == WEDDING_CEREMONIES_PAGE
    assert app.title[0].value == "Choose Your Wedding Ceremonies"
    assert not app.exception


def test_accepted_extracted_profile_links_to_ceremony_selection() -> None:
    from ai_event_planning_platform.wedding_description import (
        EXTRACTION_ACCEPTED_STATE_KEY,
        EXTRACTION_PROFILE_STATE_KEY,
    )

    app = _build_app("describe_wedding")
    app.session_state[EXTRACTION_PROFILE_STATE_KEY] = WeddingProfile(
        partner_1_culture=CulturalBackground("Tamil Hindu", "Tamil Nadu")
    )
    app.session_state[EXTRACTION_ACCEPTED_STATE_KEY] = True
    with patch(
        "ai_event_planning_platform.home.restore_authenticated_session",
        side_effect=lambda access, refresh: AuthSessionTokens(access, refresh),
    ):
        app.run()
        app.button(key="choose_ceremonies_after_accepted_extraction").click().run()

    assert app.session_state["page"] == WEDDING_CEREMONIES_PAGE
    assert not app.exception


def test_ceremony_route_is_protected() -> None:
    app = AppTest.from_file("../app.py")
    app.session_state["page"] = WEDDING_CEREMONIES_PAGE
    app.run()

    assert app.session_state["page"] == "login"
    assert app.title[0].value == "Log in"
    assert not app.exception