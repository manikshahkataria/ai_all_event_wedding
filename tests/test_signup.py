from unittest.mock import Mock, patch

import pytest
from streamlit.testing.v1 import AppTest
from supabase_auth.errors import AuthApiError

from ai_event_planning_platform.auth import SignupError, register_account


TEST_EMAIL = "signup-test@example.invalid"
TEST_PASSWORD = "Test-only-password-123!"


def test_signup_calls_supabase_auth_and_discards_response() -> None:
    client = Mock()
    client.auth.sign_up.return_value = Mock(
        user=Mock(id="test-user"), session=Mock(access_token="test-token")
    )
    with patch(
        "ai_event_planning_platform.auth.create_supabase_client",
        return_value=client,
    ) as create_client:
        result = register_account(TEST_EMAIL, TEST_PASSWORD, TEST_PASSWORD)

    assert result is None
    create_client.assert_called_once_with()
    client.auth.sign_up.assert_called_once_with(
        {"email": TEST_EMAIL, "password": TEST_PASSWORD}
    )


@pytest.mark.parametrize(
    ("email", "password", "confirmation", "message"),
    [
        ("not-an-email", TEST_PASSWORD, TEST_PASSWORD, "valid email"),
        (TEST_EMAIL, TEST_PASSWORD, "different-password", "do not match"),
        (TEST_EMAIL, "", "", "Enter a password"),
    ],
)
def test_invalid_signup_input_does_not_call_supabase(
    email: str, password: str, confirmation: str, message: str
) -> None:
    with (
        patch("ai_event_planning_platform.auth.create_supabase_client") as factory,
        pytest.raises(SignupError, match=message),
    ):
        register_account(email, password, confirmation)

    factory.assert_not_called()


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("weak_password", "password is too weak"),
        ("email_exists", "account may already exist"),
        ("user_already_exists", "account may already exist"),
        ("email_address_invalid", "valid email"),
    ],
)
def test_auth_failures_are_mapped_to_safe_messages(code: str, message: str) -> None:
    client = Mock()
    client.auth.sign_up.side_effect = AuthApiError(
        "internal detail that must not be shown", 400, code
    )
    with (
        patch(
            "ai_event_planning_platform.auth.create_supabase_client",
            return_value=client,
        ),
        pytest.raises(SignupError, match=message) as error,
    ):
        register_account(TEST_EMAIL, TEST_PASSWORD, TEST_PASSWORD)

    assert "internal detail" not in str(error.value)


def test_unexpected_auth_error_does_not_expose_details() -> None:
    client = Mock()
    client.auth.sign_up.side_effect = RuntimeError("sensitive backend detail")
    with (
        patch(
            "ai_event_planning_platform.auth.create_supabase_client",
            return_value=client,
        ),
        pytest.raises(SignupError, match="could not create your account") as error,
    ):
        register_account(TEST_EMAIL, TEST_PASSWORD, TEST_PASSWORD)

    assert "sensitive backend detail" not in str(error.value)


def test_signup_screen_submits_with_mocked_auth() -> None:
    app = AppTest.from_file("../app.py").run()
    app.button(key="login_signup").click().run()

    assert not app.exception
    assert app.title[0].value == "Create your account"
    with patch("ai_event_planning_platform.home.register_account") as signup:
        app.text_input(key="signup_email").set_value(TEST_EMAIL)
        app.text_input(key="signup_password").set_value(TEST_PASSWORD)
        app.text_input(key="signup_confirm_password").set_value(TEST_PASSWORD)
        app.button(key="signup_submit").click().run()

    signup.assert_called_once_with(TEST_EMAIL, TEST_PASSWORD, TEST_PASSWORD)
    assert not app.exception
    assert app.success[0].value == "Your account was created successfully."
    assert app.session_state["signup_complete"] is True
    assert app.session_state["signup_in_progress"] is False
    assert "supabase_client" not in app.session_state
    assert "auth_session" not in app.session_state


def test_signup_screen_shows_safe_duplicate_error() -> None:
    app = AppTest.from_file("../app.py").run()
    app.button(key="login_signup").click().run()
    with patch(
        "ai_event_planning_platform.home.register_account",
        side_effect=SignupError("An account may already exist for this email."),
    ) as signup:
        app.text_input(key="signup_email").set_value(TEST_EMAIL)
        app.text_input(key="signup_password").set_value(TEST_PASSWORD)
        app.text_input(key="signup_confirm_password").set_value(TEST_PASSWORD)
        app.button(key="signup_submit").click().run()

    signup.assert_called_once_with(TEST_EMAIL, TEST_PASSWORD, TEST_PASSWORD)
    assert not app.exception
    assert app.error[0].value == "An account may already exist for this email."


def test_signup_submit_is_disabled_while_processing() -> None:
    app = AppTest.from_file("../app.py").run()
    app.button(key="login_signup").click().run()
    app.session_state["signup_in_progress"] = True
    app.run()

    assert app.button(key="signup_submit").disabled
    assert not app.exception