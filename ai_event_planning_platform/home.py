import streamlit as st

from ai_event_planning_platform.auth import (
    ACCESS_TOKEN_KEY,
    REFRESH_TOKEN_KEY,
    AuthSessionTokens,
    LoginError,
    SessionExpiredError,
    SessionVerificationError,
    SignupError,
    clear_auth_session,
    login_user,
    logout_user,
    register_account,
    restore_authenticated_session,
    store_auth_session,
)
from ai_event_planning_platform.wedding_form import render_wedding_requirements
from ai_event_planning_platform.wedding_description import render_wedding_description
from ai_event_planning_platform.wedding_followups import render_wedding_followups
from ai_event_planning_platform.wedding_ceremonies import (
    WEDDING_CEREMONIES_PAGE,
    render_wedding_ceremonies,
)


EVENTS_PAGE = "events"
WEDDING_START_PAGE = "wedding_start"
STEP_BY_STEP_PAGE = "step_by_step"
DESCRIBE_WEDDING_PAGE = "describe_wedding"
WEDDING_FOLLOWUPS_PAGE = "wedding_followups"
WEDDING_FOLLOWUPS_PAGE = "wedding_followups"
SIGNUP_PAGE = "signup"
LOGIN_PAGE = "login"
VALID_PAGES = {
    EVENTS_PAGE,
    WEDDING_START_PAGE,
    STEP_BY_STEP_PAGE,
    DESCRIBE_WEDDING_PAGE,
    WEDDING_FOLLOWUPS_PAGE,
    WEDDING_CEREMONIES_PAGE,
    SIGNUP_PAGE,
    LOGIN_PAGE,
}
PROTECTED_PAGES = {
    EVENTS_PAGE,
    WEDDING_START_PAGE,
    STEP_BY_STEP_PAGE,
    DESCRIBE_WEDDING_PAGE,
    WEDDING_FOLLOWUPS_PAGE,
    WEDDING_CEREMONIES_PAGE,
}


def _navigate_to(page: str) -> None:
    if page == SIGNUP_PAGE:
        st.session_state["signup_complete"] = False
    st.session_state.page = page


def _require_authenticated_session() -> bool:
    access_token = st.session_state.get(ACCESS_TOKEN_KEY)
    refresh_token = st.session_state.get(REFRESH_TOKEN_KEY)
    if not isinstance(access_token, str) or not isinstance(refresh_token, str):
        clear_auth_session(st.session_state)
        st.session_state.page = LOGIN_PAGE
        return False

    try:
        tokens = restore_authenticated_session(access_token, refresh_token)
    except SessionExpiredError:
        clear_auth_session(st.session_state)
        st.session_state.auth_notice = "Your session expired. Please log in again."
        st.session_state.page = LOGIN_PAGE
        return False
    except SessionVerificationError:
        clear_auth_session(st.session_state)
        st.session_state.auth_notice = (
            "Your session could not be verified. Please log in again."
        )
        st.session_state.page = LOGIN_PAGE
        return False

    store_auth_session(st.session_state, tokens)
    st.session_state.pop("auth_notice", None)
    return True


def _logout() -> None:
    access_token = st.session_state.get(ACCESS_TOKEN_KEY, "")
    refresh_token = st.session_state.get(REFRESH_TOKEN_KEY, "")
    remote_logout_completed = False
    try:
        remote_logout_completed = logout_user(access_token, refresh_token)
    except Exception:
        pass
    finally:
        clear_auth_session(st.session_state)
        st.session_state.page = LOGIN_PAGE
        if remote_logout_completed:
            st.session_state.auth_notice = "Your app session has been cleared."
        else:
            st.session_state.auth_notice = (
                "Your app session has been cleared, but remote sign-out could not "
                "be confirmed."
            )


def _render_login() -> None:
    st.title("Log in")
    st.write("Log in to continue to your event plans.")
    notice = st.session_state.get("auth_notice")
    if notice:
        st.info(notice)

    st.session_state.setdefault("login_in_progress", False)
    with st.form("login_form", clear_on_submit=True):
        email = st.text_input("Email", key="login_email")
        password = st.text_input("Password", type="password", key="login_password")
        submitted = st.form_submit_button(
            "Login",
            key="login_submit",
            type="primary",
            disabled=st.session_state.login_in_progress,
        )

    if submitted:
        if st.session_state.login_in_progress:
            st.info("Login is already being processed.")
        else:
            st.session_state.login_in_progress = True
            try:
                tokens = login_user(email, password)
            except LoginError as error:
                st.error(str(error))
            else:
                store_auth_session(st.session_state, tokens)
                st.session_state.page = EVENTS_PAGE
                st.session_state.pop("auth_notice", None)
                st.rerun()
            finally:
                st.session_state.login_in_progress = False

    st.button(
        "Sign up",
        key="login_signup",
        on_click=_navigate_to,
        args=(SIGNUP_PAGE,),
    )


def _render_event_selection() -> None:
    st.title("What Would You Like to Plan?")
    st.write("Choose an event to get started.")
    st.button(
        "Create account",
        key="open_signup",
        on_click=_navigate_to,
        args=(SIGNUP_PAGE,),
    )

    wedding_column, birthday_column = st.columns(2)
    anniversary_column, corporate_column = st.columns(2)

    with wedding_column.container(border=True):
        st.subheader("Wedding")
        st.caption("Available now")
        st.button(
            "Plan a wedding",
            key="select_wedding",
            type="primary",
            on_click=_navigate_to,
            args=(WEDDING_START_PAGE,),
            width="stretch",
        )

    with birthday_column.container(border=True):
        st.subheader("Birthday")
        st.caption("Coming soon")
        st.button("Birthday", key="birthday_disabled", disabled=True, width="stretch")

    with anniversary_column.container(border=True):
        st.subheader("Anniversary")
        st.caption("Coming soon")
        st.button(
            "Anniversary",
            key="anniversary_disabled",
            disabled=True,
            width="stretch",
        )

    with corporate_column.container(border=True):
        st.subheader("Corporate Event")
        st.caption("Coming soon")
        st.button(
            "Corporate Event",
            key="corporate_disabled",
            disabled=True,
            width="stretch",
        )


def _render_wedding_start() -> None:
    st.title("How Would You Like to Start Planning?")
    st.write("Choose a starting point for your wedding plans.")

    step_column, describe_column = st.columns(2)
    with step_column.container(border=True):
        st.subheader("Plan Step by Step")
        st.write("Build your wedding plan through a guided sequence of steps.")
        st.button(
            "Plan Step by Step",
            key="choose_step_by_step",
            type="primary",
            on_click=_navigate_to,
            args=(STEP_BY_STEP_PAGE,),
            width="stretch",
        )

    with describe_column.container(border=True):
        st.subheader("Describe Your Dream Wedding")
        st.write("Share your vision in your own words to begin planning.")
        st.button(
            "Describe Your Dream Wedding",
            key="choose_describe_wedding",
            on_click=_navigate_to,
            args=(DESCRIBE_WEDDING_PAGE,),
            width="stretch",
        )

    st.button(
        "Back to events",
        key="back_to_events",
        icon=":material/arrow_back:",
        on_click=_navigate_to,
        args=(EVENTS_PAGE,),
    )


def _render_step_by_step_placeholder() -> None:
    render_wedding_requirements()


def _render_describe_wedding_placeholder() -> None:
    render_wedding_description()


def _render_signup() -> None:
    st.title("Create your account")
    st.write("Create an account to get started with your event plans.")
    st.session_state.setdefault("signup_in_progress", False)
    st.session_state.setdefault("signup_complete", False)

    if st.session_state.signup_complete:
        st.success("Your account was created successfully.")
        st.button(
            "Go to login",
            key="signup_success_back",
            icon=":material/arrow_back:",
            on_click=_navigate_to,
            args=(LOGIN_PAGE,),
        )
        return

    with st.form("signup_form", clear_on_submit=True):
        email = st.text_input("Email", key="signup_email")
        password = st.text_input(
            "Password", type="password", key="signup_password"
        )
        confirm_password = st.text_input(
            "Confirm password", type="password", key="signup_confirm_password"
        )
        submitted = st.form_submit_button(
            "Create Account",
            key="signup_submit",
            type="primary",
            disabled=st.session_state.signup_in_progress,
        )

    if submitted:
        if st.session_state.signup_in_progress:
            st.info("Account creation is already being processed.")
        else:
            st.session_state.signup_in_progress = True
            try:
                with st.spinner("Creating your account..."):
                    register_account(email, password, confirm_password)
            except SignupError as error:
                st.error(str(error))
            else:
                st.session_state.signup_complete = True
                st.success("Your account was created successfully.")
            finally:
                st.session_state.signup_in_progress = False

    st.button(
        "Back to events",
        key="signup_back",
        icon=":material/arrow_back:",
        on_click=_navigate_to,
        args=(EVENTS_PAGE,),
    )


def render_home() -> None:
    st.session_state.setdefault("page", LOGIN_PAGE)
    if st.session_state.page not in VALID_PAGES:
        st.session_state.page = LOGIN_PAGE

    page = st.session_state.page
    if page in PROTECTED_PAGES:
        if _require_authenticated_session():
            st.button("Logout", key="logout", on_click=_logout)
        else:
            page = LOGIN_PAGE

    if page == EVENTS_PAGE:
        _render_event_selection()
    elif page == WEDDING_START_PAGE:
        _render_wedding_start()
    elif page == STEP_BY_STEP_PAGE:
        _render_step_by_step_placeholder()
    elif page == DESCRIBE_WEDDING_PAGE:
        _render_describe_wedding_placeholder()
    elif page == WEDDING_FOLLOWUPS_PAGE:
        render_wedding_followups()
    elif page == WEDDING_CEREMONIES_PAGE:
        render_wedding_ceremonies()
    elif page == SIGNUP_PAGE:
        _render_signup()
    else:
        _render_login()