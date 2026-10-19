"""Native Streamlit routes; URLs contain page slugs, never session/project data."""

from collections.abc import MutableMapping

import streamlit as st


ROUTES = {
    "login": ("Log in", "login"),
    "signup": ("Create your account", "person_add"),
    "wedding_projects": ("My Wedding Projects", "folder"),
    "events": ("Events", "event"),
    "wedding_start": ("Planning options", "celebration"),
    "step_by_step": ("Wedding Overview", "fact_check"),
    "describe_wedding": ("Describe Your Dream Wedding", "edit_note"),
    "wedding_followups": ("Complete Wedding Details", "question_answer"),
    "wedding_ceremonies": ("Wedding Ceremonies", "checklist"),
    "ceremony_planning": ("Ceremony Plans", "event_note"),
    "smart_wedding_budget": ("Smart Wedding Budget", "payments"),
    "wedding_timeline": ("Wedding Calendar & Timeline", "calendar_month"),
    "wedding_dashboard": ("Wedding Dashboard", "dashboard"),
    "budget_dashboard": ("Budget Dashboard", "monitoring"),
    "wedding_invitations": ("Wedding Invitation Studio", "mail"),
}
CURRENT_ROUTE_KEY = "app_navigation_current"
TRAIL_KEY = "app_navigation_trail"


def reconcile_route(state: MutableMapping, selected: str) -> str:
    """Distinguish an app callback request from a native URL/history navigation."""
    requested, previous = state.get("page"), state.get(CURRENT_ROUTE_KEY)
    if requested is not None and requested not in ROUTES:
        return "login"
    if requested in ROUTES and requested != previous:
        return requested
    return selected


def remember_route(state: MutableMapping, page: str) -> None:
    previous = state.get(CURRENT_ROUTE_KEY)
    if page == "wedding_projects" and previous in ROUTES and previous != page and state.get("active_wedding_project_id"):
        state["wedding_project_return_page"] = previous
    state[CURRENT_ROUTE_KEY] = page
    state["page"] = page
    trail = list(state.get(TRAIL_KEY, []))
    if not trail or trail[-1] != page:
        trail.append(page)
    state[TRAIL_KEY] = trail[-40:]


def previous_page(state: MutableMapping, current: str) -> str:
    trail = list(state.get(TRAIL_KEY, []))
    if trail and trail[-1] == current:
        trail.pop()
    while trail and trail[-1] in {current, "login", "signup"}:
        trail.pop()
    fallback = "wedding_projects" if current == "wedding_dashboard" else "wedding_dashboard"
    if not state.get("active_wedding_project_id"):
        fallback = "events" if current == "wedding_start" else "wedding_start"
    target = trail[-1] if trail else fallback
    state[TRAIL_KEY] = trail
    return target


def run_navigation(render_page) -> None:
    def page_body():
        render_page(pages)

    pages = {
        key: st.Page(page_body, title=title, icon=f":material/{icon}:",
                     url_path=key, default=key == "login")
        for key, (title, icon) in ROUTES.items()
    }
    selected = st.navigation(list(pages.values()), position="hidden")
    selected_key = next(key for key, page in pages.items() if page.url_path == selected.url_path)
    target = reconcile_route(st.session_state, selected_key)
    # A callback changes `page`; native Back/Forward changes the selected Page.
    # Switch before rendering, so content, URL and the auth guard always agree.
    if target != selected_key:
        st.session_state[CURRENT_ROUTE_KEY] = target
        st.switch_page(pages[target])
    remember_route(st.session_state, target)
    selected.run()


def render_breadcrumbs(page: str, navigate_back, pages) -> None:
    """Internal page links use the native router and retain the live app session."""
    if page not in ROUTES or page in {"login", "signup", "wedding_projects", "events", "wedding_dashboard"}:
        return
    with st.container(horizontal=True):
        st.button("Back", key="planning_back", on_click=navigate_back)
        st.caption("Wedding Dashboard" + (" / " + ROUTES[page][0] if page != "wedding_dashboard" else " / Overview"))
    with st.container(horizontal=True):
        for route, label, icon in (("wedding_dashboard", "Overview", "dashboard"),
                ("budget_dashboard", "Budget Dashboard", "monitoring"),
                ("wedding_timeline", "Calendar & Timeline", "calendar_month"),
                ("wedding_invitations", "Wedding Invitations", "mail")):
            st.page_link(pages[route], label=label, icon=f":material/{icon}:")
    st.caption("Save form entries before changing pages. Budget and timeline drafts require explicit saving.")
