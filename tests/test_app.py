from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from ai_event_planning_platform.auth import AuthSessionTokens, store_auth_session


@pytest.fixture
def authenticated_app() -> AppTest:
    app = AppTest.from_file("../app.py")
    tokens = AuthSessionTokens("test-access-token", "test-refresh-token")
    app.session_state["page"] = "events"
    store_auth_session(app.session_state, tokens)
    with patch(
        "ai_event_planning_platform.home.restore_authenticated_session",
        side_effect=lambda access, refresh: AuthSessionTokens(access, refresh),
    ):
        app.run()
        yield app


def test_event_selection_shows_all_categories(authenticated_app: AppTest) -> None:
    app = authenticated_app

    assert not app.exception
    assert app.title[0].value == "What Would You Like to Plan?"
    categories = {element.value for element in app.subheader}
    assert categories == {"Wedding", "Birthday", "Anniversary", "Corporate Event"}


def test_wedding_is_available(authenticated_app: AppTest) -> None:
    app = authenticated_app

    wedding_button = app.button(key="select_wedding")
    assert wedding_button.label == "Plan a wedding"
    assert not wedding_button.disabled


def test_other_event_categories_are_disabled(authenticated_app: AppTest) -> None:
    app = authenticated_app

    for key in ("birthday_disabled", "anniversary_disabled", "corporate_disabled"):
        assert app.button(key=key).disabled


@pytest.mark.parametrize(
    ("option_key", "expected_page", "back_key"),
    [
        ("choose_step_by_step", "Plan Step by Step", "back_from_step_by_step"),
        (
            "choose_describe_wedding",
            "Describe Your Dream Wedding",
            "back_from_describe_wedding",
        ),
    ],
)
def test_wedding_onboarding_options_open_placeholders(
    authenticated_app: AppTest, option_key: str, expected_page: str, back_key: str
) -> None:
    app = authenticated_app
    app.button(key="select_wedding").click().run()

    assert not app.exception
    assert app.title[0].value == "How Would You Like to Start Planning?"
    assert app.button(key="choose_step_by_step").label == "Plan Step by Step"
    assert (
        app.button(key="choose_describe_wedding").label
        == "Describe Your Dream Wedding"
    )

    app.button(key=option_key).click().run()

    assert not app.exception
    assert app.title[0].value == expected_page
    assert app.button(key=back_key).label == "Back to planning options"
    app.button(key=back_key).click().run()

    assert app.title[0].value == "How Would You Like to Start Planning?"
    assert not app.exception


def test_back_navigation_returns_to_previous_screens(
    authenticated_app: AppTest,
) -> None:
    app = authenticated_app
    app.button(key="select_wedding").click().run()
    app.button(key="choose_step_by_step").click().run()
    app.button(key="back_from_step_by_step").click().run()

    assert app.title[0].value == "How Would You Like to Start Planning?"
    app.button(key="back_to_events").click().run()

    assert app.title[0].value == "What Would You Like to Plan?"
    assert not app.exception


def test_invalid_navigation_state_returns_to_event_selection() -> None:
    app = AppTest.from_file("../app.py")
    app.session_state["page"] = "unknown_page"
    app.run()

    assert app.session_state["page"] == "login"
    assert app.title[0].value == "Log in"
    assert not app.exception


@pytest.mark.parametrize(
    "protected_page",
    [
        "events",
        "wedding_start",
        "step_by_step",
        "describe_wedding",
        "wedding_followups",
        "wedding_ceremonies",
    ],
)
def test_protected_pages_redirect_unauthenticated_user(
    protected_page: str,
) -> None:
    app = AppTest.from_file("../app.py")
    app.session_state["page"] = protected_page
    app.run()

    assert app.session_state["page"] == "login"
    assert app.title[0].value == "Log in"
    assert not app.subheader
    assert not app.exception


def test_new_streamlit_session_does_not_restore_login_tokens() -> None:
    app = AppTest.from_file("../app.py").run()

    assert app.session_state["page"] == "login"
    assert "auth_access_token" not in app.session_state
    assert "auth_refresh_token" not in app.session_state
    assert app.title[0].value == "Log in"
    assert not app.subheader
    assert not app.exception