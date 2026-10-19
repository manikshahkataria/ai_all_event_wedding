from datetime import date, datetime

import streamlit as st

from ai_event_planning_platform.navigation import previous_page, render_breadcrumbs, run_navigation
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
from ai_event_planning_platform.wedding_profile import WeddingProfile
from ai_event_planning_platform.smart_wedding_budget import SMART_BUDGET_PAGE, render_smart_wedding_budget
from ai_event_planning_platform.timeline_planning import (
    TIMELINE_PAGE,
    render_wedding_timeline,
)
from ai_event_planning_platform.dashboard_data import wedding_display_title
from ai_event_planning_platform.wedding_dashboard import (
    DASHBOARD_PAGE, BUDGET_DASHBOARD_PAGE, INVITATIONS_PAGE,
    render_wedding_dashboard, render_budget_dashboard,
)
from ai_event_planning_platform.invitation_studio import render_invitation_studio
from ai_event_planning_platform.invitation_state import refresh_invitation_sources_on_entry
from ai_event_planning_platform.ceremony_planning import (
    CEREMONY_PLANNING_PAGE,
    render_ceremony_planning,
)
from ai_event_planning_platform.wedding_project_state import (
    ACTIVE_PROJECT_ID_KEY,
    ACTIVE_PROJECT_NAME_KEY,
    LOADED_PROJECT_ID_KEY,
    PAGE_BY_PLANNING_STEP,
    PROJECT_DIRTY_KEY,
    PROJECT_ERROR_KEY,
    PROJECT_RETURN_PAGE_KEY,
    PROJECT_SUCCESS_KEY,
    PROFILE_SAVED_KEY,
    activate_created_project,
    clear_project_workspace,
    delete_project_from_state,
    navigate_project_to_page,
    persist_active_project,
    project_repository_from_state,
    restore_project_workspace,
)


EVENTS_PAGE = "events"
PROJECTS_PAGE = "wedding_projects"
WEDDING_START_PAGE = "wedding_start"
STEP_BY_STEP_PAGE = "step_by_step"
DESCRIBE_WEDDING_PAGE = "describe_wedding"
WEDDING_FOLLOWUPS_PAGE = "wedding_followups"
SIGNUP_PAGE = "signup"
LOGIN_PAGE = "login"
VALID_PAGES = {
    EVENTS_PAGE,
    PROJECTS_PAGE,
    WEDDING_START_PAGE,
    STEP_BY_STEP_PAGE,
    DESCRIBE_WEDDING_PAGE,
    WEDDING_FOLLOWUPS_PAGE,
    WEDDING_CEREMONIES_PAGE,
    CEREMONY_PLANNING_PAGE,
    SIGNUP_PAGE,
    SMART_BUDGET_PAGE,
    TIMELINE_PAGE,
    DASHBOARD_PAGE, BUDGET_DASHBOARD_PAGE, INVITATIONS_PAGE,
    LOGIN_PAGE,
}
PROTECTED_PAGES = {
    EVENTS_PAGE,
    PROJECTS_PAGE,
    WEDDING_START_PAGE,
    STEP_BY_STEP_PAGE,
    DESCRIBE_WEDDING_PAGE,
    WEDDING_FOLLOWUPS_PAGE,
    WEDDING_CEREMONIES_PAGE,
    CEREMONY_PLANNING_PAGE,
    SMART_BUDGET_PAGE,
    TIMELINE_PAGE,
    DASHBOARD_PAGE, BUDGET_DASHBOARD_PAGE, INVITATIONS_PAGE,
}
DELETE_TARGET_KEY = "project_delete_target"


def _cancel_delete() -> None:
    target = st.session_state.pop(DELETE_TARGET_KEY, None)
    if target:
        st.session_state.pop(f"project_delete_confirm_{target['project_id']}", None)


@st.dialog("Delete wedding project", on_dismiss=_cancel_delete)
def _confirm_project_deletion(project: dict) -> None:
    project_id = str(project["project_id"])
    # A fragment rerun must still match the project the user chose on the card.
    target = st.session_state.get(DELETE_TARGET_KEY)
    if not target or target.get("project_id") != project_id:
        st.error("Reopen Delete from the selected project's card.")
        return
    profile = project.get("wedding_profile", {})
    st.text("Project: " + str(project.get("project_name", "Not provided")))
    st.text("Location: " + (", ".join(str(profile[k]) for k in ("city", "state") if profile.get(k)) or "Not provided"))
    st.text("Wedding date: " + _format_project_date(profile.get("wedding_date"), profile.get("wedding_date_status")))
    st.caption("Project ID: " + project_id)
    st.warning("Deletion is permanent. This removes this project's wedding details, ceremonies, plans and saved budget, and discards its unsaved drafts. Other projects are unaffected.")
    confirmed = st.checkbox("I understand this permanently deletes this wedding project.", key=f"project_delete_confirm_{project_id}")
    with st.container(horizontal=True):
        if st.button("Cancel", key="project_delete_cancel"):
            _cancel_delete()
            st.rerun()
        if st.button("Delete permanently", key=f"project_delete_execute_{project_id}", disabled=not confirmed, type="primary"):
            try:
                delete_project_from_state(st.session_state, project_id, confirmed=confirmed,
                                          expected_updated_at=project.get("updated_at"))
            except Exception:
                st.error("Deletion could not be completed or verified. Check your connection and retry. If the project changed or is already absent, Cancel and refresh the project list before trying again.")
            else:
                _cancel_delete()
                st.session_state[PROJECT_SUCCESS_KEY] = "The selected wedding project was permanently deleted and its removal verified."
                st.rerun()


def _back_to_previous_page() -> None:
    target = previous_page(st.session_state, st.session_state["page"])
    if target == PROJECTS_PAGE:
        _navigate_to(target)
    else:
        navigate_project_to_page(st.session_state, target)


def _navigate_to(page: str) -> None:
    if page == SIGNUP_PAGE:
        st.session_state["signup_complete"] = False
    if page == PROJECTS_PAGE and st.session_state.get(ACTIVE_PROJECT_ID_KEY):
        st.session_state[PROJECT_RETURN_PAGE_KEY] = st.session_state.get("page")
    if page in PAGE_BY_PLANNING_STEP.values() and st.session_state.get(
        ACTIVE_PROJECT_ID_KEY
    ):
        try:
            planning_step = next(
                step
                for step, mapped_page in PAGE_BY_PLANNING_STEP.items()
                if mapped_page == page
            )
            persist_active_project(st.session_state, planning_step=planning_step)
        except Exception:
            st.session_state[PROJECT_DIRTY_KEY] = True
            st.session_state[PROJECT_ERROR_KEY] = (
                "Your planning progress could not be saved. Your current edits are "
                "still available in this session."
            )
    st.session_state.page = page


def _require_authenticated_session() -> bool:
    access_token = st.session_state.get(ACCESS_TOKEN_KEY)
    refresh_token = st.session_state.get(REFRESH_TOKEN_KEY)
    if not isinstance(access_token, str) or not isinstance(refresh_token, str):
        clear_auth_session(st.session_state)
        clear_project_workspace(st.session_state, clear_active_project=True)
        _cancel_delete()
        st.session_state.page = LOGIN_PAGE
        return False

    try:
        tokens = restore_authenticated_session(access_token, refresh_token)
    except SessionExpiredError:
        clear_auth_session(st.session_state)
        clear_project_workspace(st.session_state, clear_active_project=True)
        _cancel_delete()
        st.session_state.auth_notice = "Your session expired. Please log in again."
        st.session_state.page = LOGIN_PAGE
        return False
    except SessionVerificationError:
        clear_auth_session(st.session_state)
        clear_project_workspace(st.session_state, clear_active_project=True)
        _cancel_delete()
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
        clear_project_workspace(st.session_state, clear_active_project=True)
        _cancel_delete()
        st.session_state.pop("new_wedding_project_name", None)
        st.session_state.pop("new_wedding_bride_name", None)
        st.session_state.pop("new_wedding_groom_name", None)
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
                st.session_state.page = PROJECTS_PAGE
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
    st.button(
        "My Wedding Projects",
        key="events_open_projects",
        on_click=_navigate_to,
        args=(PROJECTS_PAGE,),
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

def _render_step_by_step_placeholder() -> None:
    render_wedding_requirements(show_back_button=False)


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


def _format_project_date(value: object, status: object) -> str:
    if not isinstance(value, str):
        return "Not set"
    try:
        parsed = date.fromisoformat(f"{value}-01" if len(value) == 7 else value)
    except ValueError:
        return "Not set"
    if status == "approximate_date":
        return parsed.strftime("%B %Y")
    return parsed.strftime("%d %B %Y")


def _format_updated_at(value: object) -> str:
    if not isinstance(value, str):
        return "Unknown"
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return "Unknown"
    return timestamp.strftime("%d %b %Y")


def _render_projects_dashboard() -> None:
    st.title("My Wedding Projects")
    active_project_id = st.session_state.get(ACTIVE_PROJECT_ID_KEY)
    has_unsaved_changes = bool(st.session_state.get(PROJECT_DIRTY_KEY, False))
    if has_unsaved_changes:
        st.warning(
            "A previous save failed. Return to that project and save your edits "
            "before starting or switching projects."
        )

    st.session_state.setdefault("new_wedding_project_name", "My wedding")
    with st.form("new_wedding_project_form", clear_on_submit=False):
        st.text_input(
            "Wedding project name",
            key="new_wedding_project_name",
            max_chars=120,
        )
        bride_name = st.text_input("Bride's name (optional)", key="new_wedding_bride_name", max_chars=120)
        groom_name = st.text_input("Groom's name (optional)", key="new_wedding_groom_name", max_chars=120)
        create_submitted = st.form_submit_button(
            "Start New Wedding",
            key="start_new_wedding",
            type="primary",
            disabled=has_unsaved_changes,
        )
    if create_submitted:
        name = str(st.session_state.get("new_wedding_project_name", "")).strip()
        if not name:
            st.error("Enter a name for this wedding project.")
        else:
            try:
                project = project_repository_from_state(
                    st.session_state
                ).create_wedding_project(
                    name,
                    WeddingProfile(bride_name=bride_name.strip() or None, groom_name=groom_name.strip() or None),
                    [],
                    planning_step="wedding_start",
                )
            except Exception:
                st.error(
                    "Your wedding project could not be created or verified. Check "
                    "the project list before retrying to avoid creating it twice."
                )
            else:
                activate_created_project(st.session_state, project)
                st.session_state.pop("new_wedding_bride_name", None)
                st.session_state.pop("new_wedding_groom_name", None)
                st.rerun()

    try:
        projects = project_repository_from_state(st.session_state).list_user_projects()
    except Exception:
        st.error(
            "Your wedding projects could not be loaded. Check the database "
            "connection and try again."
        )
        return

    if not projects:
        st.info(
            "Your wedding plans will appear here. Start a new wedding to create "
            "your first project."
        )
        return

    for project in projects:
        project_id = str(project.get("project_id", ""))
        profile = project.get("wedding_profile")
        if not isinstance(profile, dict):
            profile = {}
        with st.container(border=True):
            st.subheader(wedding_display_title(profile, str(project.get("project_name", "Wedding"))))
            city = profile.get("city")
            city_label = city if isinstance(city, str) and city else "Not set"
            st.write(f"**Wedding city:** {city_label}")
            st.write(
                "**Wedding date:** "
                + _format_project_date(
                    profile.get("wedding_date"),
                    profile.get("wedding_date_status"),
                )
            )
            status = str(project.get("status", "planning")).replace("_", " ").title()
            st.write(f"**Planning status:** {status}")
            st.caption(f"Last updated {_format_updated_at(project.get('updated_at'))}")
            same_project = project_id == active_project_id
            return_label = (
                "Return to active project" if same_project else "Continue Planning"
            )
            if st.button(
                return_label,
                key=f"continue_wedding_project_{project_id}",
                disabled=has_unsaved_changes and not same_project,
                type="primary" if same_project else "secondary",
            ):
                if same_project and not has_unsaved_changes and st.session_state.get(LOADED_PROJECT_ID_KEY) == project_id:
                    st.session_state["page"] = DASHBOARD_PAGE
                    st.rerun()
                if same_project and has_unsaved_changes and st.session_state.get(PROJECT_RETURN_PAGE_KEY):
                    return_page = st.session_state.pop(PROJECT_RETURN_PAGE_KEY)
                    st.session_state["page"] = (
                        return_page
                        if return_page in PAGE_BY_PLANNING_STEP.values()
                        else PAGE_BY_PLANNING_STEP.get(
                            str(project.get("planning_step", "requirements")),
                            WEDDING_START_PAGE,
                        )
                    )
                    st.rerun()
                try:
                    selected = project_repository_from_state(
                        st.session_state
                    ).load_selected_project(project_id)
                    if selected is not None:
                        restore_project_workspace(st.session_state, selected)
                        st.session_state["page"] = DASHBOARD_PAGE
                except Exception:
                    st.error("This wedding project could not be opened. Try again.")
                else:
                    if selected is None:
                        st.error(
                            "This wedding project is unavailable for your account."
                        )
                    else:
                        st.rerun()
            if st.button("Delete", key=f"delete_wedding_project_{project_id}", icon=":material/delete:"):
                _cancel_delete()
                st.session_state[DELETE_TARGET_KEY] = dict(project)
    target = st.session_state.get(DELETE_TARGET_KEY)
    if target is not None:
        _confirm_project_deletion(target)


def _ensure_active_project_loaded() -> bool:
    project_id = st.session_state.get(ACTIVE_PROJECT_ID_KEY)
    if not isinstance(project_id, str) or st.session_state.get(
        LOADED_PROJECT_ID_KEY
    ) == project_id:
        return True
    try:
        requested_page = st.session_state["page"]
        project = project_repository_from_state(st.session_state).load_selected_project(
            project_id
        )
        if project is not None:
            restore_project_workspace(st.session_state, project)
            st.session_state["page"] = requested_page
    except Exception:
        project = None
        message = "The selected wedding project could not be verified."
    else:
        message = "This wedding project is unavailable for your account."
    if project is None:
        clear_project_workspace(st.session_state, clear_active_project=True)
        st.session_state[PROJECT_ERROR_KEY] = message
        st.session_state.page = PROJECTS_PAGE
        return False
    return True


def _render_current_page(pages) -> None:
    st.session_state.setdefault("page", LOGIN_PAGE)
    if st.session_state.page not in VALID_PAGES:
        st.session_state.page = LOGIN_PAGE

    page = st.session_state.page
    if page in PROTECTED_PAGES:
        if _require_authenticated_session():
            if page not in {PROJECTS_PAGE, EVENTS_PAGE} and not _ensure_active_project_loaded():
                page = PROJECTS_PAGE
        else:
            page = LOGIN_PAGE
    if page != st.session_state.get("app_navigation_current"):
        # Authentication/project guards redirect through native routing too.
        st.session_state["page"] = page
        st.rerun()
    if page in PROTECTED_PAGES and st.session_state.get(ACTIVE_PROJECT_ID_KEY):
        try:
            refreshed = refresh_invitation_sources_on_entry(st.session_state, page)
        except Exception:
            st.session_state["wedding_invitation_source_error"] = "Latest saved wedding details could not be refreshed. Your invitation edits are retained; retry Use Latest Wedding Details or reopen the Studio."
        else:
            if refreshed:
                st.session_state.pop("wedding_invitation_source_error", None)
    if page in PROTECTED_PAGES:
        with st.container(border=True):
            if page not in {PROJECTS_PAGE, EVENTS_PAGE, DASHBOARD_PAGE} and st.session_state.get(ACTIVE_PROJECT_ID_KEY):
                st.subheader(wedding_display_title(st.session_state.get(PROFILE_SAVED_KEY), st.session_state.get(ACTIVE_PROJECT_NAME_KEY)))
            with st.container(horizontal=True):
                if page != PROJECTS_PAGE:
                    st.button("My Wedding Projects", key="open_wedding_projects", on_click=_navigate_to, args=(PROJECTS_PAGE,))
                st.button("Logout", key="logout", on_click=_logout)
            render_breadcrumbs(page, _back_to_previous_page, pages)
    project_error = st.session_state.pop(PROJECT_ERROR_KEY, None)
    if project_error and page != LOGIN_PAGE:
        st.error(project_error)
    project_success = st.session_state.pop(PROJECT_SUCCESS_KEY, None)
    if project_success and page != LOGIN_PAGE:
        st.success(project_success)

    if page == PROJECTS_PAGE:
        _render_projects_dashboard()
    elif page == EVENTS_PAGE:
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
    elif page == CEREMONY_PLANNING_PAGE:
        render_ceremony_planning()
    elif page == SMART_BUDGET_PAGE:
        render_smart_wedding_budget()
    elif page == TIMELINE_PAGE:
        render_wedding_timeline()
    elif page == DASHBOARD_PAGE:
        render_wedding_dashboard()
    elif page == BUDGET_DASHBOARD_PAGE:
        render_budget_dashboard()
    elif page == INVITATIONS_PAGE:
        render_invitation_studio()
    elif page == SIGNUP_PAGE:
        _render_signup()
    else:
        _render_login()


def render_home() -> None:
    run_navigation(_render_current_page)
