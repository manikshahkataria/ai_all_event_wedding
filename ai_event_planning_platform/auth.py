import logging
import re
from collections.abc import MutableMapping
from dataclasses import dataclass

from supabase_auth.errors import AuthApiError, AuthError, AuthRetryableError

from ai_event_planning_platform.config import SupabaseConfigurationError
from ai_event_planning_platform.supabase_client import create_supabase_client


EMAIL_PATTERN = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
DUPLICATE_ACCOUNT_CODES = {"email_exists", "user_already_exists"}
ACCESS_TOKEN_KEY = "auth_access_token"
REFRESH_TOKEN_KEY = "auth_refresh_token"
logger = logging.getLogger(__name__)


def _log_auth_failure(stage: str, error: Exception) -> None:
    """Log diagnostic metadata without response bodies, URLs, or credentials."""
    status = getattr(error, "status", None)
    if type(status) is not int or not 100 <= status <= 599:
        status = "none"
    logger.warning(
        "Supabase auth failure stage=%s http_status=%s category=%s",
        stage, status, type(error).__name__,
    )


@dataclass(frozen=True)
class AuthSessionTokens:
    access_token: str
    refresh_token: str


class SignupError(ValueError):
    """An account signup failure with a safe, user-facing message."""


class LoginError(ValueError):
    """A login failure with a safe, user-facing message."""


class SessionExpiredError(RuntimeError):
    """Raised when a stored Supabase session is no longer valid."""


class SessionVerificationError(RuntimeError):
    """Raised when a stored session cannot be verified due to a service error."""


def _validate_signup(email: str, password: str, confirm_password: str) -> None:
    if not EMAIL_PATTERN.fullmatch(email.strip()):
        raise SignupError("Enter a valid email address.")
    if not password:
        raise SignupError("Enter a password.")
    if password != confirm_password:
        raise SignupError("The passwords do not match.")


def _safe_auth_error(error: AuthApiError) -> str:
    code = error.code
    if code in DUPLICATE_ACCOUNT_CODES:
        return "An account may already exist for this email. Check the address."
    if code == "weak_password":
        return "That password is too weak. Choose a stronger password and try again."
    if code == "email_address_invalid":
        return "Enter a valid email address."
    return "We could not create your account. Please try again."


def register_account(email: str, password: str, confirm_password: str) -> None:
    _validate_signup(email, password, confirm_password)

    try:
        client = create_supabase_client()
        client.auth.sign_up({"email": email.strip(), "password": password})
    except SupabaseConfigurationError:
        raise SignupError(
            "Signup is unavailable. Check the local Supabase configuration."
        ) from None
    except AuthApiError as error:
        raise SignupError(_safe_auth_error(error)) from None
    except Exception:
        raise SignupError(
            "We could not create your account. Please try again."
        ) from None


def login_user(email: str, password: str) -> AuthSessionTokens:
    if not EMAIL_PATTERN.fullmatch(email.strip()):
        raise LoginError("Enter a valid email address.")
    if not password:
        raise LoginError("Enter your password.")

    stage = "before_login_request"
    try:
        client = create_supabase_client()
        stage = "authentication_request"
        response = client.auth.sign_in_with_password(
            {"email": email.strip(), "password": password}
        )
    except SupabaseConfigurationError as error:
        _log_auth_failure(stage, error)
        raise LoginError(
            "Login is unavailable. Check the local Supabase configuration."
        ) from None
    except AuthApiError as error:
        _log_auth_failure(stage, error)
        if error.code == "email_not_confirmed":
            raise LoginError("Confirm your email before logging in.") from None
        if error.code == "over_request_rate_limit":
            raise LoginError("Too many login attempts. Please wait and try again.") from None
        if error.code in {"invalid_credentials", "user_not_found"}:
            raise LoginError("Email or password is incorrect.") from None
        raise LoginError("Unable to log in right now. Please try again.") from None
    except AuthRetryableError as error:
        _log_auth_failure(stage, error)
        raise LoginError(
            "The authentication service is temporarily unavailable. Please try again."
        ) from None
    except Exception as error:
        _log_auth_failure(stage, error)
        raise LoginError(
            "The authentication service is temporarily unavailable. Please try again."
        ) from None

    session = response.session
    if session is None or response.user is None:
        raise LoginError(
            "Login did not establish a session. Confirm your email or try again."
        )
    return AuthSessionTokens(
        access_token=session.access_token,
        refresh_token=session.refresh_token,
    )


def restore_authenticated_session(
    access_token: str, refresh_token: str
) -> AuthSessionTokens:
    if not access_token or not refresh_token:
        raise SessionExpiredError("Your session has expired. Please log in again.")

    try:
        client = create_supabase_client()
        response = client.auth.set_session(access_token, refresh_token)
    except AuthRetryableError as error:
        _log_auth_failure("session_restore", error)
        raise SessionVerificationError(
            "Your session could not be verified. Please log in again."
        ) from None
    except AuthApiError as error:
        _log_auth_failure("session_restore", error)
        if error.status in {400, 401}:
            raise SessionExpiredError(
                "Your session has expired. Please log in again."
            ) from None
        raise SessionVerificationError(
            "Your session could not be verified. Please log in again."
        ) from None
    except AuthError as error:
        _log_auth_failure("session_restore", error)
        raise SessionExpiredError(
            "Your session has expired. Please log in again."
        ) from None
    except Exception as error:
        _log_auth_failure("session_restore", error)
        raise SessionVerificationError(
            "Your session could not be verified. Please log in again."
        ) from None

    session = response.session
    if session is None:
        raise SessionExpiredError("Your session has expired. Please log in again.")
    return AuthSessionTokens(
        access_token=session.access_token,
        refresh_token=session.refresh_token,
    )


def logout_user(access_token: str, refresh_token: str) -> bool:
    if not access_token or not refresh_token:
        return False

    try:
        client = create_supabase_client()
        response = client.auth.set_session(access_token, refresh_token)
        if response.session is None:
            return False
        client.auth.sign_out({"scope": "local"})
        return True
    except Exception:
        return False


def store_auth_session(
    state: MutableMapping[str, object], tokens: AuthSessionTokens
) -> None:
    state[ACCESS_TOKEN_KEY] = tokens.access_token
    state[REFRESH_TOKEN_KEY] = tokens.refresh_token


def clear_auth_session(state: MutableMapping[str, object]) -> None:
    state.pop(ACCESS_TOKEN_KEY, None)
    state.pop(REFRESH_TOKEN_KEY, None)
