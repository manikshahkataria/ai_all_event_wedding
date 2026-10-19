from unittest.mock import Mock, patch

import pytest
from httpx import ConnectError
from streamlit.testing.v1 import AppTest
from supabase_auth.errors import AuthApiError, AuthRetryableError

from ai_event_planning_platform import home
from ai_event_planning_platform.config import SupabaseConfigurationError
from ai_event_planning_platform.auth import (
    ACCESS_TOKEN_KEY,
    REFRESH_TOKEN_KEY,
    AuthSessionTokens,
    LoginError,
    SessionExpiredError,
    SessionVerificationError,
    clear_auth_session,
    login_user,
    logout_user,
    restore_authenticated_session,
    store_auth_session,
)


TEST_EMAIL = "login-test@example.invalid"
TEST_PASSWORD = "Test-only-password-123!"
TEST_TOKENS = AuthSessionTokens("test-access-token", "test-refresh-token")


def test_login_returns_tokens_from_mocked_auth_response() -> None:
    client = Mock()
    client.auth.sign_in_with_password.return_value = Mock(
        session=Mock(access_token="mock-access", refresh_token="mock-refresh"),
        user=Mock(id="mock-user"),
    )
    with patch(
        "ai_event_planning_platform.auth.create_supabase_client",
        return_value=client,
    ) as create_client:
        tokens = login_user(TEST_EMAIL, TEST_PASSWORD)

    assert tokens == AuthSessionTokens("mock-access", "mock-refresh")
    create_client.assert_called_once_with()
    client.auth.sign_in_with_password.assert_called_once_with(
        {"email": TEST_EMAIL, "password": TEST_PASSWORD}
    )


def test_invalid_credentials_have_safe_error() -> None:
    client = Mock()
    client.auth.sign_in_with_password.side_effect = AuthApiError(
        "private auth response", 400, "invalid_credentials"
    )
    with (
        patch(
            "ai_event_planning_platform.auth.create_supabase_client",
            return_value=client,
        ),
        pytest.raises(LoginError, match="Email or password is incorrect") as error,
    ):
        login_user(TEST_EMAIL, TEST_PASSWORD)

    assert "private auth response" not in str(error.value)


def test_login_connection_failure_has_safe_error() -> None:
    client = Mock()
    client.auth.sign_in_with_password.side_effect = AuthRetryableError(
        "private network response", 503
    )
    with (
        patch(
            "ai_event_planning_platform.auth.create_supabase_client",
            return_value=client,
        ),
        pytest.raises(LoginError, match="temporarily unavailable") as error,
    ):
        login_user(TEST_EMAIL, TEST_PASSWORD)

    assert "private network response" not in str(error.value)


@pytest.mark.parametrize(
    ("failure", "status"),
    [
        (ConnectError("private network response"), "none"),
        (AuthRetryableError("private network response", 503), "503"),
        (AuthRetryableError("private network response", 0), "none"),
        (AuthApiError("private network response", 400, "invalid_credentials"), "400"),
    ],
)
def test_login_diagnostics_are_sanitized(failure, status, caplog) -> None:
    client = Mock()
    client.auth.sign_in_with_password.side_effect = failure
    with (
        patch("ai_event_planning_platform.auth.create_supabase_client", return_value=client),
        pytest.raises(LoginError),
    ):
        login_user(TEST_EMAIL, TEST_PASSWORD)

    assert "stage=authentication_request" in caplog.text
    assert f"http_status={status}" in caplog.text
    assert f"category={type(failure).__name__}" in caplog.text
    for private_value in ("private network response", TEST_EMAIL, TEST_PASSWORD):
        assert private_value not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_client_configuration_failure_is_classified_before_login(caplog) -> None:
    with (
        patch(
            "ai_event_planning_platform.auth.create_supabase_client",
            side_effect=SupabaseConfigurationError("private configuration value"),
        ),
        pytest.raises(LoginError, match="local Supabase configuration") as error,
    ):
        login_user(TEST_EMAIL, TEST_PASSWORD)

    assert "stage=before_login_request http_status=none category=SupabaseConfigurationError" in caplog.text
    assert "private configuration value" not in caplog.text + str(error.value)


def test_restore_diagnostics_do_not_expose_session_tokens(caplog) -> None:
    client = Mock()
    client.auth.set_session.side_effect = AuthApiError(
        "private session response", 401, "invalid_jwt"
    )
    with (
        patch("ai_event_planning_platform.auth.create_supabase_client", return_value=client),
        pytest.raises(SessionExpiredError),
    ):
        restore_authenticated_session(TEST_TOKENS.access_token, TEST_TOKENS.refresh_token)

    assert "stage=session_restore http_status=401 category=AuthApiError" in caplog.text
    for private_value in ("private session response", TEST_TOKENS.access_token, TEST_TOKENS.refresh_token):
        assert private_value not in caplog.text


def test_dashboard_load_failure_does_not_become_a_login_failure() -> None:
    app = AppTest.from_file("../app.py").run()
    with (
        patch("ai_event_planning_platform.home.login_user", return_value=TEST_TOKENS),
        patch("ai_event_planning_platform.home.restore_authenticated_session", return_value=TEST_TOKENS),
        patch(
            "ai_event_planning_platform.home.project_repository_from_state",
            side_effect=RuntimeError("private dashboard response"),
        ),
    ):
        app.text_input(key="login_email").set_value(TEST_EMAIL)
        app.text_input(key="login_password").set_value(TEST_PASSWORD)
        app.button(key="login_submit").click().run()

    assert app.title[0].value == "My Wedding Projects"
    assert app.session_state[ACCESS_TOKEN_KEY] == TEST_TOKENS.access_token
    assert "projects could not be loaded" in app.error[0].value
    assert "temporarily unavailable" not in app.error[0].value
    assert "private dashboard response" not in app.error[0].value
    assert not app.exception


def test_unconfirmed_email_has_safe_error() -> None:
    client = Mock()
    client.auth.sign_in_with_password.side_effect = AuthApiError(
        "private auth response", 400, "email_not_confirmed"
    )
    with (
        patch(
            "ai_event_planning_platform.auth.create_supabase_client",
            return_value=client,
        ),
        pytest.raises(LoginError, match="Confirm your email") as error,
    ):
        login_user(TEST_EMAIL, TEST_PASSWORD)

    assert "private auth response" not in str(error.value)


def test_restore_session_returns_rotated_tokens() -> None:
    client = Mock()
    client.auth.set_session.return_value = Mock(
        session=Mock(access_token="rotated-access", refresh_token="rotated-refresh")
    )
    with patch(
        "ai_event_planning_platform.auth.create_supabase_client",
        return_value=client,
    ):
        tokens = restore_authenticated_session("old-access", "old-refresh")

    assert tokens == AuthSessionTokens("rotated-access", "rotated-refresh")
    client.auth.set_session.assert_called_once_with("old-access", "old-refresh")


def test_expired_session_is_classified_without_details() -> None:
    client = Mock()
    client.auth.set_session.side_effect = AuthApiError(
        "private expired-session detail", 401, "invalid_jwt"
    )
    with (
        patch(
            "ai_event_planning_platform.auth.create_supabase_client",
            return_value=client,
        ),
        pytest.raises(SessionExpiredError, match="session has expired") as error,
    ):
        restore_authenticated_session("expired-access", "expired-refresh")

    assert "private expired-session detail" not in str(error.value)


def test_missing_session_is_rejected_without_client_creation() -> None:
    with (
        patch("ai_event_planning_platform.auth.create_supabase_client") as factory,
        pytest.raises(SessionExpiredError),
    ):
        restore_authenticated_session("", "")

    factory.assert_not_called()


def test_session_verification_connection_failure_is_safe() -> None:
    client = Mock()
    client.auth.set_session.side_effect = AuthRetryableError(
        "private network response", 503
    )
    with (
        patch(
            "ai_event_planning_platform.auth.create_supabase_client",
            return_value=client,
        ),
        pytest.raises(SessionVerificationError, match="could not be verified") as error,
    ):
        restore_authenticated_session("access-token", "refresh-token")

    assert "private network response" not in str(error.value)


def test_logout_uses_local_scope_and_returns_success() -> None:
    client = Mock()
    client.auth.set_session.return_value = Mock(session=Mock())
    with patch(
        "ai_event_planning_platform.auth.create_supabase_client",
        return_value=client,
    ):
        result = logout_user("access-token", "refresh-token")

    assert result is True
    client.auth.set_session.assert_called_once_with("access-token", "refresh-token")
    client.auth.sign_out.assert_called_once_with({"scope": "local"})


def test_logout_failure_does_not_expose_server_details() -> None:
    with patch(
        "ai_event_planning_platform.auth.create_supabase_client",
        side_effect=RuntimeError("private logout detail"),
    ):
        result = logout_user("access-token", "refresh-token")

    assert result is False


def test_auth_tokens_are_isolated_and_clearable_between_sessions() -> None:
    first_state: dict[str, object] = {}
    second_state: dict[str, object] = {}
    store_auth_session(first_state, AuthSessionTokens("first-access", "first-refresh"))
    store_auth_session(second_state, AuthSessionTokens("second-access", "second-refresh"))

    assert first_state[ACCESS_TOKEN_KEY] == "first-access"
    assert second_state[ACCESS_TOKEN_KEY] == "second-access"
    assert first_state[REFRESH_TOKEN_KEY] == "first-refresh"
    assert second_state[REFRESH_TOKEN_KEY] == "second-refresh"

    clear_auth_session(first_state)
    assert ACCESS_TOKEN_KEY not in first_state
    assert REFRESH_TOKEN_KEY not in first_state
    assert ACCESS_TOKEN_KEY in second_state


def test_login_screen_authenticates_and_opens_projects_dashboard() -> None:
    app = AppTest.from_file("../app.py").run()
    assert app.title[0].value == "Log in"

    with (
        patch("ai_event_planning_platform.home.login_user", return_value=TEST_TOKENS) as login,
        patch(
            "ai_event_planning_platform.home.restore_authenticated_session",
            return_value=TEST_TOKENS,
        ),
        patch(
            "ai_event_planning_platform.home.project_repository_from_state",
            return_value=Mock(list_user_projects=Mock(return_value=[])),
        ),
    ):
        app.text_input(key="login_email").set_value(TEST_EMAIL)
        app.text_input(key="login_password").set_value(TEST_PASSWORD)
        app.button(key="login_submit").click().run()

    login.assert_called_once_with(TEST_EMAIL, TEST_PASSWORD)
    assert app.session_state[ACCESS_TOKEN_KEY] == TEST_TOKENS.access_token
    assert app.session_state[REFRESH_TOKEN_KEY] == TEST_TOKENS.refresh_token
    assert "login_password" not in app.session_state
    assert app.title[0].value == "My Wedding Projects"
    assert not app.exception


def test_login_screen_displays_safe_invalid_credentials_error() -> None:
    app = AppTest.from_file("../app.py").run()
    with patch(
        "ai_event_planning_platform.home.login_user",
        side_effect=LoginError("Email or password is incorrect."),
    ) as login:
        app.text_input(key="login_email").set_value(TEST_EMAIL)
        app.text_input(key="login_password").set_value(TEST_PASSWORD)
        app.button(key="login_submit").click().run()

    login.assert_called_once_with(TEST_EMAIL, TEST_PASSWORD)
    assert app.error[0].value == "Email or password is incorrect."
    assert ACCESS_TOKEN_KEY not in app.session_state
    assert not app.exception


def test_expired_protected_session_clears_tokens_and_redirects_to_login() -> None:
    app = AppTest.from_file("../app.py")
    app.session_state["page"] = "events"
    store_auth_session(app.session_state, TEST_TOKENS)
    with patch(
        "ai_event_planning_platform.home.restore_authenticated_session",
        side_effect=SessionExpiredError("safe expired message"),
    ):
        app.run()

    assert app.session_state["page"] == "login"
    assert ACCESS_TOKEN_KEY not in app.session_state
    assert REFRESH_TOKEN_KEY not in app.session_state
    assert app.title[0].value == "Log in"
    assert app.info[0].value == "Your session expired. Please log in again."
    assert not app.exception


def test_logout_clears_local_session_and_calls_supabase_signout() -> None:
    app = AppTest.from_file("../app.py")
    app.session_state["page"] = "events"
    store_auth_session(app.session_state, TEST_TOKENS)
    app.session_state["active_wedding_project_id"] = "local-project-id"
    app.session_state["wedding_profile_draft"] = "temporary draft"
    with (
        patch(
            "ai_event_planning_platform.home.restore_authenticated_session",
            return_value=TEST_TOKENS,
        ),
        patch(
            "ai_event_planning_platform.home.logout_user", return_value=True
        ) as logout,
    ):
        app.run()
        app.button(key="logout").click().run()

    logout.assert_called_once_with(TEST_TOKENS.access_token, TEST_TOKENS.refresh_token)
    assert ACCESS_TOKEN_KEY not in app.session_state
    assert REFRESH_TOKEN_KEY not in app.session_state
    assert "active_wedding_project_id" not in app.session_state
    assert "wedding_profile_draft" not in app.session_state
    assert app.session_state["page"] == "login"
    assert app.info[0].value == "Your app session has been cleared."
    assert not app.exception


def test_logout_clears_local_session_when_remote_signout_fails() -> None:
    app = AppTest.from_file("../app.py")
    app.session_state["page"] = "events"
    store_auth_session(app.session_state, TEST_TOKENS)
    with (
        patch(
            "ai_event_planning_platform.home.restore_authenticated_session",
            return_value=TEST_TOKENS,
        ),
        patch("ai_event_planning_platform.home.logout_user", return_value=False),
    ):
        app.run()
        app.button(key="logout").click().run()

    assert ACCESS_TOKEN_KEY not in app.session_state
    assert REFRESH_TOKEN_KEY not in app.session_state
    assert app.session_state["page"] == "login"
    assert "remote sign-out could not be confirmed" in app.info[0].value
    assert not app.exception


def test_separate_streamlit_sessions_keep_distinct_tokens() -> None:
    first_app = AppTest.from_file("../app.py")
    second_app = AppTest.from_file("../app.py")
    first_tokens = AuthSessionTokens("first-access", "first-refresh")
    second_tokens = AuthSessionTokens("second-access", "second-refresh")
    for app, tokens in ((first_app, first_tokens), (second_app, second_tokens)):
        app.session_state["page"] = "events"
        store_auth_session(app.session_state, tokens)

    with patch(
        "ai_event_planning_platform.home.restore_authenticated_session",
        side_effect=lambda access, refresh: AuthSessionTokens(access, refresh),
    ):
        first_app.run()
        second_app.run()

    assert first_app.session_state[ACCESS_TOKEN_KEY] == "first-access"
    assert second_app.session_state[ACCESS_TOKEN_KEY] == "second-access"
    assert first_app.session_state[REFRESH_TOKEN_KEY] != second_app.session_state[
        REFRESH_TOKEN_KEY
    ]
