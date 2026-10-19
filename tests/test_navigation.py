from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from ai_event_planning_platform.auth import ACCESS_TOKEN_KEY, REFRESH_TOKEN_KEY
from ai_event_planning_platform.navigation import CURRENT_ROUTE_KEY, ROUTES, reconcile_route
from ai_event_planning_platform.smart_wedding_budget import WORKING_KEY
from test_smart_wedding_budget import environment


def test_project_open_keeps_authenticated_route(environment):
    _repo, create, _open, _ai = environment
    _row, app = create()
    assert app.session_state["page"] == "smart_wedding_budget"
    assert ACCESS_TOKEN_KEY in app.session_state
    assert app.title[0].value == "Smart Wedding Budget"
    assert not app.exception


@pytest.mark.parametrize("state,selected,expected", [
    ({}, "smart_wedding_budget", "smart_wedding_budget"),
    ({"page": "step_by_step", CURRENT_ROUTE_KEY: "smart_wedding_budget"}, "smart_wedding_budget", "step_by_step"),
    ({"page": "smart_wedding_budget", CURRENT_ROUTE_KEY: "smart_wedding_budget"}, "step_by_step", "step_by_step"),
    ({"page": "step_by_step", CURRENT_ROUTE_KEY: "step_by_step"}, "smart_wedding_budget", "smart_wedding_budget"),
    ({"page": "unrecognized"}, "events", "login"),
])
def test_router_distinguishes_callback_requests_from_native_history(state, selected, expected):
    assert reconcile_route(state, selected) == expected


def client_visit(app, route):
    """Simulate a native client page-selection event, not a real browser history."""
    path = "" if route == "login" else route
    app._page_hash = next(key for key, info in app._registered_pages.items() if info.get("url_pathname") == path)
    return app.run()


def test_native_page_selection_back_forward_preserves_auth_project_and_budget_draft(environment):
    from test_smart_wedding_budget import generate_and_review

    _repo, create, _open, _ai = environment
    row, app = create()
    generate_and_review(app)
    app.text_input(key="wedding_budget_editor_name_haldi:catering").set_value("Draft breakfast").run()
    working = app.session_state[WORKING_KEY]
    access, refresh = app.session_state[ACCESS_TOKEN_KEY], app.session_state[REFRESH_TOKEN_KEY]
    with patch("ai_event_planning_platform.home.logout_user") as logout:
        client_visit(app, "step_by_step")
        assert app.session_state["page"] == "step_by_step"
        client_visit(app, "smart_wedding_budget")
        assert app.session_state[WORKING_KEY] == working
        assert app.text_input(key="wedding_budget_editor_name_haldi:catering").value == "Draft breakfast"
        logout.assert_not_called()
    assert app.session_state["active_wedding_project_id"] == row["project_id"]
    assert app.session_state[ACCESS_TOKEN_KEY] == access
    assert app.session_state[REFRESH_TOKEN_KEY] == refresh
    assert not app.exception


def test_consistent_in_app_back_and_breadcrumbs(environment):
    _repo, create, _open, _ai = environment
    _row, app = create()
    assert app.button(key="planning_back").label == "Back"
    labels = [link.proto.label for link in app.get("page_link")]
    assert labels == ["Overview", "Budget Dashboard", "Calendar & Timeline", "Wedding Invitations"]
    app.button(key="planning_back").click().run()
    assert app.session_state["page"] == "budget_dashboard"
    client_visit(app, "step_by_step")
    app.button(key="edit_ceremony_plans").click().run()
    assert app.session_state["page"] == "ceremony_planning"
    app.button(key="planning_back").click().run()
    assert app.session_state["page"] == "step_by_step"
    assert ACCESS_TOKEN_KEY in app.session_state
    assert not app.exception


@pytest.mark.parametrize("page", [key for key in ROUTES if key not in {"login", "signup"}])
def test_all_native_protected_routes_require_an_authenticated_session(page):
    app = AppTest.from_file("../app.py")
    app.session_state["page"] = page
    with patch("ai_event_planning_platform.home.project_repository_from_state") as repo:
        app.run()
        repo.assert_not_called()
    assert app.title[0].value == "Log in"
    assert app.session_state["page"] == "login"
    assert ACCESS_TOKEN_KEY not in app.session_state
    assert not app.exception


def test_native_url_paths_contain_only_public_route_names(environment):
    _repo, create, _open, _ai = environment
    _row, app = create()
    paths = {info.get("url_pathname") for info in app._registered_pages.values()}
    assert paths == {"", *(route for route in ROUTES if route != "login")}
    assert not app.query_params
