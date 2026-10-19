from dataclasses import replace
from datetime import date
from unittest.mock import patch

from ai_event_planning_platform.auth import AuthSessionTokens
from ai_event_planning_platform.wedding_profile import (
    CulturalBackground, WeddingDateStatus, WeddingLocationPreference, WeddingProfile, WeddingStyle,
)
from ai_event_planning_platform.wedding_summary import wedding_glance_fields
from test_budget_engine import ceremony, profile
from test_smart_wedding_budget import environment


def panel(app):
    return next(c for c in app.container if c.key == "wedding_glance_panel")


def fields(app):
    return [item.value for item in panel(app).text]


def test_summary_shows_saved_context_before_financial_totals(environment):
    _repo, create, _open, _ai = environment
    p = profile(city="Jaipur", state="Rajasthan", guest_count=300,
        wedding_date_status=WeddingDateStatus.FIXED_DATE, wedding_date=date(2027, 11, 20),
        location_preference=WeddingLocationPreference.DESTINATION, style=WeddingStyle.MIXED,
        partner_1_culture=CulturalBackground("Punjabi", "Punjab"),
        partner_2_culture=CulturalBackground("Tamil", "Chennai"), additional_preferences="Vegetarian food and a quiet ceremony.")
    _row, app = create(p=p, ceremonies=[ceremony(), ceremony("reception")])
    values = fields(app)
    assert "Project: Budget wedding" in values
    assert "City and state: Jaipur, Rajasthan" in values
    assert "Wedding date: 20 November 2027 (fixed date)" in values
    assert "Wedding location: Destination" in values
    assert "Total wedding guests: 300" in values
    assert "Total wedding budget: ₹10,00,000" in values
    assert "Wedding style: Mixed" in values
    assert "Partner 1 cultural background: Punjabi, Punjab" in values
    assert "Partner 2 cultural background: Tamil, Chennai" in values
    assert "Confirmed ceremonies: Haldi / Halad, Reception" in values
    assert "Additional preferences: Vegetarian food and a quiet ceremony." in values
    nodes = list(app.main)
    assert nodes.index(panel(app)) < next(i for i, node in enumerate(nodes) if node.type == "metric")
    assert not app.exception


def test_missing_fields_are_never_invented_and_optional_text_is_omitted(environment):
    _repo, create, _open, _ai = environment
    _row, app = create(p=WeddingProfile())
    values = fields(app)
    assert "City and state: Not provided" in values
    assert "Wedding date: Not provided (not decided yet)" in values
    assert "Total wedding budget: Not provided" in values
    assert "Total wedding guests: Not provided" in values
    assert "Wedding style: Not provided" in values
    assert not any("cultural background" in v or "Additional preferences" in v for v in values)
    assert not app.exception


def test_approximate_dates_and_explicit_zeros_are_preserved():
    p = WeddingProfile(guest_count=0, budget_inr=0, wedding_date_status=WeddingDateStatus.APPROXIMATE_DATE,
                       wedding_date=date(2027, 11, 1))
    result = dict(wedding_glance_fields("Wedding", p, []))
    assert result["Wedding date"] == "November 2027 (approximate date)"
    assert result["Total wedding guests"] == "0"
    assert result["Total wedding budget"] == "₹0"
    assert result["Confirmed ceremonies"] == "Not provided"


def test_saved_profile_edits_update_glance_without_overwriting_profile(environment):
    repo, create, _open, _ai = environment
    row, app = create(p=profile(city="Jaipur"))
    assert "City and state: Jaipur" in fields(app)
    app.session_state["page"] = "step_by_step"
    app.run()
    app.text_input(key=f"wedding_{row['project_id']}_city_value").set_value("Manali")
    app.button(key=f"wedding_{row['project_id']}_save_profile").click().run()
    app.button(key="wedding_budget_open").click().run()
    assert "City and state: Manali" in fields(app)
    assert repo.load_selected_project(row["project_id"])["wedding_profile"]["city"] == "Manali"
    assert not app.exception


def test_unsubmitted_profile_changes_do_not_replace_verified_saved_summary(environment):
    _repo, create, _open, _ai = environment
    row, app = create(p=profile(city="Jaipur"))
    app.session_state["page"] = "step_by_step"
    app.run()
    app.text_input(key=f"wedding_{row['project_id']}_city_value").set_value("Unsaved city").run()
    app.button(key="wedding_budget_open").click().run()
    assert "City and state: Jaipur" in fields(app)
    assert not any("Unsaved city" in v for v in fields(app))
    assert not app.exception


def test_switching_projects_never_mixes_saved_wedding_context(environment):
    repo, create, _open, _ai = environment
    first, app = create(p=profile(city="Jaipur"), ceremonies=[ceremony()])
    second = repo.create_wedding_project("Manali wedding", profile(city="Manali"), [ceremony("reception")],
                                        planning_step="smart_wedding_budget")
    app.button(key="open_wedding_projects").click().run()
    app.button(key=f"continue_wedding_project_{second['project_id']}").click().run()
    app.button(key="wedding_dashboard_budget").click().run()
    app.button(key="wedding_dashboard_edit_budget").click().run()
    assert "City and state: Manali" in fields(app)
    assert "Project: Manali wedding" in fields(app)
    assert "Confirmed ceremonies: Reception" in fields(app)
    assert not any("Jaipur" in value or "Haldi" in value for value in fields(app))
    app.button(key="open_wedding_projects").click().run()
    app.button(key=f"continue_wedding_project_{first['project_id']}").click().run()
    app.button(key="wedding_dashboard_budget").click().run()
    app.button(key="wedding_dashboard_edit_budget").click().run()
    assert "City and state: Jaipur" in fields(app)
    assert not app.exception


def test_summary_uses_saved_snapshot_without_extra_database_calls_on_reruns(environment):
    repo, create, _open, ai = environment
    _row, app = create(p=profile(city="Jaipur"))
    with (patch.object(repo, "load_selected_project", wraps=repo.load_selected_project) as load,
          patch.object(repo, "update_project", wraps=repo.update_project) as update):
        app.run()
        app.checkbox(key="wedding_budget_use_ai").uncheck().run()
        app.run()
        load.assert_not_called()
        update.assert_not_called()
    ai.assert_not_called()
    assert "City and state: Jaipur" in fields(app)


def test_summary_restores_after_logout_login_and_project_reopen(environment):
    _repo, create, _open, _ai = environment
    row, app = create(p=profile(city="Jaipur", state="Rajasthan"))
    before = fields(app)
    with (patch("ai_event_planning_platform.home.logout_user", return_value=True),
          patch("ai_event_planning_platform.home.login_user", return_value=AuthSessionTokens("a", "r"))):
        app.button(key="logout").click().run()
        app.text_input(key="login_email").set_value("planner@example.invalid")
        app.text_input(key="login_password").set_value("not-stored")
        app.button(key="login_submit").click().run()
        app.button(key=f"continue_wedding_project_{row['project_id']}").click().run()
    app.button(key="wedding_dashboard_budget").click().run()
    app.button(key="wedding_dashboard_edit_budget").click().run()
    assert fields(app) == before
    assert not app.exception
