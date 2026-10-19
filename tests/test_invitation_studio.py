from dataclasses import replace
from datetime import date, time
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock, patch
from zipfile import ZipFile

import pytest
from streamlit.testing.v1 import AppTest

from ai_event_planning_platform.auth import AuthSessionTokens, store_auth_session
from ai_event_planning_platform.invitation_state import ACTIVE_CARD_KEY, WORKSPACE_KEY, PHOTOS_KEY, STAGE_KEY
from ai_event_planning_platform.invitation_studio import PREFIX, _download_data, _upload, _remove_photo
from ai_event_planning_platform.invitation_wording import PHRASES
from ai_event_planning_platform.wedding_profile import WeddingProfile, WeddingDateStatus
from ai_event_planning_platform.wedding_project_state import INVITATIONS_SAVED_KEY, PROJECT_DIRTY_KEY, PROFILE_SAVED_KEY, TIMELINE_SAVED_KEY
from ai_event_planning_platform.wedding_projects import WeddingProjectRepository
from test_invitation_rendering import photo_bytes
from test_invitations import ready_card
from test_wedding_projects import FakeClient, OWNER_ONE


@pytest.fixture
def environment(monkeypatch):
    repo = WeddingProjectRepository(FakeClient(), OWNER_ONE)
    monkeypatch.setattr("ai_event_planning_platform.home.restore_authenticated_session", lambda a, r: AuthSessionTokens(a, r))
    monkeypatch.setattr("ai_event_planning_platform.home.project_repository_from_state", lambda s: repo)
    monkeypatch.setattr("ai_event_planning_platform.wedding_project_state.project_repository_from_state", lambda s: repo)
    monkeypatch.setattr("ai_event_planning_platform.invitation_state.project_repository_from_state", lambda s: repo)
    ai = Mock(return_value=PHRASES[0])
    monkeypatch.setattr("ai_event_planning_platform.invitation_wording.suggest_wording", ai)
    def reopen(project_id):
        app = AppTest.from_file("../app.py", default_timeout=20)
        store_auth_session(app.session_state, AuthSessionTokens("test-access", "test-refresh"))
        app.session_state["page"] = "wedding_projects"
        app.run()
        app.button(key=f"continue_wedding_project_{project_id}").click().run()
        app.button(key="wedding_dashboard_invitations").click().run()
        assert not app.exception
        return app
    def create(minimal=False):
        p, cs, _card = ready_card()
        row = repo.create_wedding_project("Our invitation wedding", WeddingProfile() if minimal else p, [] if minimal else cs)
        return row, reopen(row["project_id"])
    return repo, create, reopen, ai


def current(app):
    return app.session_state[WORKSPACE_KEY][app.session_state[ACTIVE_CARD_KEY]]


def key(app, field):
    return PREFIX + current(app).invitation_id + "_" + field


def advance(app, count=1):
    for _ in range(count):
        app.button(key="wedding_invitation_continue").click().run()
        assert not app.exception
    return app


def native_route(app, route):
    app._page_hash = next(k for k, info in app._registered_pages.items() if info.get("url_pathname") == route)
    app.run()
    assert not app.exception


def test_studio_available_immediately_for_minimal_project_and_has_four_real_previews(environment):
    _repo, create, _open, ai = environment
    _row, app = create(minimal=True)
    assert app.title[0].value == "Wedding Invitation Studio"
    assert len(app.get("image")) == 4
    assert len([b for b in app.button if (b.key or "").startswith("wedding_invitation_template_")]) == 4
    advance(app, 4)
    assert app.get("download_button")[0].proto.disabled
    app.checkbox(key="wedding_invitation_reviewed").check().run()
    assert app.get("download_button")[0].proto.disabled
    app.button(key="wedding_invitation_save").click().run()
    assert any("read back" in s.value for s in app.success)
    ai.assert_not_called()


def test_wizard_back_template_switch_and_overrides_preserve_unsaved_work(environment):
    repo, create, _open, ai = environment
    row, app = create()
    app.button(key="wedding_invitation_template_royal").click().run()
    assert current(app).template_id == "royal"
    advance(app)
    app.text_input(key=key(app, "general_bride_name")).set_value("Invitation-only Priya").run()
    advance(app, 2)
    app.button(key="wedding_invitation_back").click().run()
    app.button(key="wedding_invitation_back").click().run()
    assert app.text_input(key=key(app, "general_bride_name")).value == "Invitation-only Priya"
    assert repo.load_selected_project(row["project_id"])["wedding_profile"]["bride_name"] == "Priya"
    ai.assert_not_called()
    assert not app.exception


def test_bride_and_groom_type_versions_keep_distinct_family_details_and_selections(environment):
    _repo, create, _open, _ai = environment
    _row, app = create()
    app.selectbox(key=key(app, "card_invitation_type")).set_value("bride_family").run()
    assert current(app).selected_ceremony_ids == ()
    app.multiselect(key=key(app, "card_selected_ceremony_ids")).set_value(["haldi"]).run()
    advance(app)
    app.selectbox(key=key(app, "section")).set_value("Hosting family").run()
    app.text_input(key=key(app, "family_hosts")).set_value("Bride-side family").run()
    app.text_input(key=key(app, "family_parents")).set_value("Names supplied by bride's family").run()
    app.button(key="wedding_invitation_back").click().run()
    app.selectbox(key=key(app, "card_invitation_type")).set_value("groom_family").run()
    assert current(app).family == {} and current(app).selected_ceremony_ids == ()
    app.selectbox(key=key(app, "card_invitation_type")).set_value("bride_family").run()
    assert current(app).family["hosts"] == "Bride-side family"
    assert current(app).selected_ceremony_ids == ("haldi",)


def test_wording_requires_explicit_acceptance_and_never_calls_ai_on_preview_edits(environment):
    repo, create, _open, ai = environment
    _row, app = create()
    advance(app)
    original = current(app).wording
    app.button(key="wedding_invitation_suggest").click().run()
    assert current(app).wording == original and ai.call_count == 1
    app.button(key="wedding_invitation_use_wording").click().run()
    assert current(app).wording == PHRASES[0]
    with patch.object(repo, "load_selected_project", wraps=repo.load_selected_project) as load:
        app.text_area(key=key(app, "card_wording")).set_value("Our own invitation message").run()
        advance(app, 3)
        app.checkbox(key="wedding_invitation_reviewed").check().run()
        load.assert_not_called()
    assert ai.call_count == 1
    assert not app.get("download_button")[0].proto.disabled


def test_explicit_announcement_acceptance_enables_unknown_event_details_only(environment):
    _repo, create, _open, _ai = environment
    _row, app = create(minimal=True)
    advance(app)
    app.text_input(key=key(app, "general_bride_name")).set_value("Priya").run()
    app.text_input(key=key(app, "general_groom_name")).set_value("Rahul").run()
    advance(app, 2)
    app.checkbox(key=key(app, "card_announcement")).check().run()
    app.checkbox(key=key(app, "card_accept_tba")).check().run()
    advance(app)
    app.checkbox(key="wedding_invitation_reviewed").check().run()
    assert not app.get("download_button")[0].proto.disabled


def test_multiple_designs_save_reopen_and_isolate_between_weddings(environment):
    repo, create, reopen, _ai = environment
    first, app = create()
    initial_id = current(app).invitation_id
    advance(app, 4)
    app.button(key="wedding_invitation_save").click().run()
    app.button(key="wedding_invitation_new").click().run()
    app.selectbox(key=key(app, "card_invitation_type")).set_value("ceremony").run()
    app.selectbox(key=key(app, "card_selected_ceremony_ids")).set_value("haldi").run()
    second_id = current(app).invitation_id
    advance(app, 4)
    app.button(key="wedding_invitation_save").click().run()
    assert len(app.session_state[INVITATIONS_SAVED_KEY]) == 2
    app = reopen(first["project_id"])
    app.selectbox(key="wedding_invitation_picker").set_value(second_id).run()
    assert current(app).selected_ceremony_ids == ("haldi",) and initial_id != second_id
    other = repo.create_wedding_project("Another wedding", WeddingProfile(), [])
    app.button(key="open_wedding_projects").click().run()
    app.button(key=f"continue_wedding_project_{other['project_id']}").click().run()
    app.button(key="wedding_dashboard_invitations").click().run()
    assert not app.session_state[INVITATIONS_SAVED_KEY]
    assert current(app).invitation_id not in {initial_id, second_id}
    assert PHOTOS_KEY not in app.session_state


def test_failed_save_keeps_draft_and_recovers_after_comparing_concurrent_card(environment):
    repo, create, _open, _ai = environment
    row, app = create()
    card = current(app)
    remote = replace(card, wording="Other session's saved message")
    repo.save_invitation(row["project_id"], remote)
    advance(app, 4)
    app.button(key="wedding_invitation_save").click().run()
    assert not app.success and app.session_state[PROJECT_DIRTY_KEY]
    assert current(app) == card
    app.button(key="wedding_invitation_reload").click().run()
    assert current(app) == card
    assert app.session_state[INVITATIONS_SAVED_KEY][card.invitation_id] == remote
    app.button(key="wedding_invitation_save").click().run()
    assert any("read back" in s.value for s in app.success)


def test_loading_latest_details_after_review_does_not_mutate_an_instantiated_widget(environment):
    _repo, create, _open, ai = environment
    _row, app = create()
    advance(app)
    app.text_area(key=key(app, "card_wording")).set_value("Our unsaved personal message").run()
    draft = current(app)
    advance(app, 3)
    app.checkbox(key="wedding_invitation_reviewed").check().run()
    app.button(key="wedding_invitation_reload").click().run()
    assert not app.exception
    assert not app.checkbox(key="wedding_invitation_reviewed").value
    assert current(app) == draft
    app.checkbox(key="wedding_invitation_reviewed").check().run()
    app.button(key="wedding_invitation_save").click().run()
    assert not app.exception
    assert any("read back" in s.value for s in app.success)
    ai.assert_not_called()


def test_review_reruns_and_use_latest_callback_preserve_draft_and_invalidate_review(environment):
    _repo, create, _open, _ai = environment
    _row, app = create()
    advance(app)
    app.text_input(key=key(app, "general_bride_name")).set_value("Card-only name").run()
    app.text_area(key=key(app, "card_wording")).set_value("Keep this message across review and design changes").run()
    advance(app, 3)
    app.checkbox(key="wedding_invitation_reviewed").check().run()
    app.run()
    assert not app.exception and app.checkbox(key="wedding_invitation_reviewed").value
    app.checkbox(key=key(app, "use_latest_confirm")).check().run()
    app.button(key="wedding_invitation_use_latest").click().run()
    assert not app.exception and not app.checkbox(key="wedding_invitation_reviewed").value
    assert current(app).wording == "Keep this message across review and design changes"
    app.button(key="wedding_invitation_back").click().run()
    app.selectbox(key=key(app, "card_template_id")).set_value("royal").run()
    app.selectbox(key=key(app, "card_palette")).set_value("alternate").run()
    advance(app)
    assert not app.checkbox(key="wedding_invitation_reviewed").value
    assert current(app).template_id == "royal" and current(app).palette == "alternate"
    assert current(app).wording == "Keep this message across review and design changes"


def test_photo_callbacks_validate_replace_remove_and_keep_pixels_out_of_json(monkeypatch):
    from ai_event_planning_platform.invitations import invitation_to_json
    card = ready_card()[2]
    state = {WORKSPACE_KEY: {card.invitation_id: card}, ACTIVE_CARD_KEY: card.invitation_id}
    import ai_event_planning_platform.invitation_studio as studio
    monkeypatch.setattr(studio.st, "session_state", state)
    raw = photo_bytes()
    state["upload"] = SimpleNamespace(size=len(raw), name="photo.png", getvalue=lambda: raw)
    _upload("upload")
    assert state[WORKSPACE_KEY][card.invitation_id].photo_expected
    before = state[PHOTOS_KEY][card.invitation_id]
    other = photo_bytes((500, 300))
    state["upload"] = SimpleNamespace(size=len(other), name="replacement.png", getvalue=lambda: other)
    _upload("upload")
    assert state[PHOTOS_KEY][card.invitation_id] != before
    state["upload"] = SimpleNamespace(size=3, name="broken.png", getvalue=lambda: b"bad")
    _upload("upload")
    assert state["wedding_invitation_photo_error"]
    _remove_photo()
    assert card.invitation_id not in state[PHOTOS_KEY] and not state[WORKSPACE_KEY][card.invitation_id].photo_expected
    assert not any(isinstance(v, bytes) for v in invitation_to_json(state[WORKSPACE_KEY][card.invitation_id]).values())


@pytest.mark.parametrize("stale_selection", [None, "outdated formatted option"])
def test_stale_browser_design_picker_value_does_not_replace_the_active_draft(monkeypatch, stale_selection):
    import ai_event_planning_platform.invitation_studio as studio
    card = ready_card()[2]
    state = {WORKSPACE_KEY: {card.invitation_id: card}, ACTIVE_CARD_KEY: card.invitation_id,
             "wedding_invitation_picker": stale_selection}
    monkeypatch.setattr(studio.st, "session_state", state)
    studio._select_card()
    assert state[ACTIVE_CARD_KEY] == card.invitation_id
    assert state[WORKSPACE_KEY] == {card.invitation_id: card}


def test_recovering_a_previous_invalid_picker_keeps_valid_unsaved_cards():
    from ai_event_planning_platform.invitation_state import active_card
    card = replace(ready_card()[2], wording="Unsaved wording to keep")
    state = {WORKSPACE_KEY: {card.invitation_id: card, None: None}, ACTIVE_CARD_KEY: None}
    assert active_card(state) == card
    assert state[WORKSPACE_KEY] == {card.invitation_id: card}


def test_photo_expected_after_new_session_requires_reupload_or_removal(environment):
    repo, create, reopen, _ai = environment
    row, app = create()
    card = replace(current(app), photo_expected=True)
    repo.save_invitation(row["project_id"], card)
    app = reopen(row["project_id"])
    advance(app, 4)
    app.checkbox(key="wedding_invitation_reviewed").check().run()
    assert app.get("download_button")[0].proto.disabled
    assert any("session-only photo" in c.value for c in app.caption)
    app.button(key="wedding_invitation_back").click().run()
    app.button(key="wedding_invitation_back").click().run()
    app.button(key="wedding_invitation_remove_photo").click().run()
    advance(app, 2)
    app.checkbox(key="wedding_invitation_reviewed").check().run()
    assert not app.get("download_button")[0].proto.disabled


def test_invitation_route_requires_authentication():
    app = AppTest.from_file("../app.py")
    app.session_state["page"] = "wedding_invitations"
    app.run()
    assert app.title[0].value == "Log in" and not app.exception


def test_native_page_selection_back_forward_preserves_card_and_refreshes_saved_sources_once(environment):
    repo, create, _open, ai = environment
    row, app = create()
    advance(app)
    app.text_input(key=key(app, "general_bride_name")).set_value("Card-only name").run()
    before = current(app)
    with patch.object(repo, "load_selected_project", wraps=repo.load_selected_project) as load:
        for route in ("wedding_dashboard", "wedding_invitations"):
            native_route(app, route)
        load.assert_called_once_with(row["project_id"])
    assert current(app) == before and app.session_state["active_wedding_project_id"] == row["project_id"]
    assert app.text_input(key=key(app, "general_bride_name")).value == "Card-only name"
    ai.assert_not_called()


def test_logout_login_restores_card_metadata_and_clears_photo_pixels(environment):
    _repo, create, _open, _ai = environment
    row, app = create()
    card = replace(current(app), photo_expected=True)
    workspace = dict(app.session_state[WORKSPACE_KEY])
    workspace[card.invitation_id] = card
    app.session_state[WORKSPACE_KEY] = workspace
    app.session_state[PHOTOS_KEY] = {card.invitation_id: photo_bytes()}
    advance(app, 4)
    app.button(key="wedding_invitation_save").click().run()
    assert any("read back" in s.value for s in app.success)
    with (patch("ai_event_planning_platform.home.logout_user", return_value=True),
          patch("ai_event_planning_platform.home.login_user", return_value=AuthSessionTokens("a", "r"))):
        app.button(key="logout").click().run()
        assert PHOTOS_KEY not in app.session_state
        app.text_input(key="login_email").set_value("planner@example.invalid")
        app.text_input(key="login_password").set_value("not-stored")
        app.button(key="login_submit").click().run()
        app.button(key=f"continue_wedding_project_{row['project_id']}").click().run()
        app.button(key="wedding_dashboard_invitations").click().run()
    assert current(app) == card and PHOTOS_KEY not in app.session_state
    assert any("photo is not available" in i.value for i in app.info)


def test_approximate_month_is_not_promoted_to_a_fixed_day_and_location_is_invitation_only(environment):
    repo, create, _open, _ai = environment
    row, app = create(minimal=True)
    advance(app)
    app.selectbox(key=key(app, "section")).set_value("Event details").run()
    app.selectbox(key=key(app, "general_certainty")).set_value("approximate").run()
    app.text_input(key=key(app, "general_date")).set_value("2027-11").run()
    app.text_input(key=key(app, "general_location")).set_value("Card-specific city").run()
    assert current(app).overrides["date"] == "2027-11"
    assert repo.load_selected_project(row["project_id"])["wedding_profile"]["city"] is None
    app.selectbox(key=key(app, "general_certainty")).set_value("fixed").run()
    assert app.date_input(key=key(app, "general_date")).value is None
    assert current(app).overrides["date"] == ""


def test_source_detail_changes_invalidate_export_review(environment):
    from ai_event_planning_platform.wedding_project_state import PROFILE_SAVED_KEY
    _repo, create, _open, _ai = environment
    _row, app = create()
    advance(app, 4)
    app.checkbox(key="wedding_invitation_reviewed").check().run()
    assert not app.get("download_button")[0].proto.disabled
    app.session_state[PROFILE_SAVED_KEY] = replace(app.session_state[PROFILE_SAVED_KEY], bride_name="New saved name")
    app.run()
    assert not app.checkbox(key="wedding_invitation_reviewed").value
    assert app.get("download_button")[0].proto.disabled


@pytest.mark.parametrize("initial_status", [WeddingDateStatus.APPROXIMATE_DATE, WeddingDateStatus.NOT_DECIDED_YET])
def test_reopened_linked_controls_follow_latest_saved_wedding_details_without_overrides(environment, initial_status):
    repo, create, _reopen, ai = environment
    row, app = create(minimal=True)
    initial = WeddingProfile(wedding_date_status=initial_status,
                            wedding_date=date(2027, 11, 1) if initial_status == WeddingDateStatus.APPROXIMATE_DATE else None)
    repo.update_project(row["project_id"], profile=initial)
    native_route(app, "wedding_dashboard")
    native_route(app, "wedding_invitations")
    advance(app)
    app.text_area(key=key(app, "card_wording")).set_value("Keep our unsaved personal wording").run()
    draft = current(app)
    old_preview = app.get("image")[0].proto.SerializeToString()
    latest = replace(initial, bride_name="Esha", groom_name="Vishal", city="Manali",
                     wedding_date_status=WeddingDateStatus.FIXED_DATE, wedding_date=date(2027, 11, 18))
    repo.update_project(row["project_id"], profile=latest)
    native_route(app, "wedding_dashboard")
    native_route(app, "wedding_invitations")
    assert app.text_input(key=key(app, "general_bride_name")).value == "Esha"
    assert app.text_input(key=key(app, "general_groom_name")).value == "Vishal"
    assert app.get("image")[0].proto.SerializeToString() != old_preview
    app.selectbox(key=key(app, "section")).set_value("Event details").run()
    assert app.selectbox(key=key(app, "general_certainty")).value == "fixed"
    assert app.date_input(key=key(app, "general_date")).value == date(2027, 11, 18)
    assert app.text_input(key=key(app, "general_location")).value == "Manali"
    assert current(app) == draft and not draft.overrides
    ai.assert_not_called()


def test_linked_widgets_refresh_in_place_but_keep_deliberate_invitation_overrides(environment):
    _repo, create, _open, ai = environment
    _row, app = create()
    advance(app)
    app.text_input(key=key(app, "general_bride_name")).set_value("Card-only bride").run()
    app.session_state[PROFILE_SAVED_KEY] = replace(app.session_state[PROFILE_SAVED_KEY], bride_name="Latest bride", groom_name="Latest groom")
    app.run()
    assert not app.exception
    assert app.text_input(key=key(app, "general_bride_name")).value == "Card-only bride"
    assert app.text_input(key=key(app, "general_groom_name")).value == "Latest groom"
    assert current(app).overrides == {"bride_name": "Card-only bride"}
    ai.assert_not_called()


def test_ceremony_venue_date_and_accepted_time_refresh_visible_controls(environment):
    from ai_event_planning_platform.wedding_ceremonies import CEREMONIES_SAVED_KEY
    from ai_event_planning_platform.wedding_timeline import fallback_timeline
    _repo, create, _open, ai = environment
    _row, app = create()
    advance(app)
    app.selectbox(key=key(app, "section")).set_value("Event details").run()
    ceremony = app.session_state[CEREMONIES_SAVED_KEY][0]
    updated = replace(ceremony, plan=replace(ceremony.plan, venue_notes="New saved venue", ceremony_date=date(2027, 11, 18)))
    app.session_state[CEREMONIES_SAVED_KEY] = [updated]
    timeline = fallback_timeline(app.session_state[PROFILE_SAVED_KEY], [updated])
    app.session_state[TIMELINE_SAVED_KEY] = replace(timeline, accepted=True,
        schedules=(replace(timeline.schedules[0], start_time=time(14, 30)),))
    app.run()
    assert not app.exception
    assert app.text_area(key=key(app, "haldi_venue")).value == "New saved venue"
    assert app.date_input(key=key(app, "haldi_date")).value == date(2027, 11, 18)
    assert app.time_input(key=key(app, "haldi_time")).value == time(14, 30)
    assert not current(app).event_overrides
    app.text_area(key=key(app, "haldi_venue")).set_value("Our private venue override").run()
    changed = replace(updated, plan=replace(updated.plan, venue_notes="Another saved venue"))
    app.session_state[CEREMONIES_SAVED_KEY] = [changed]
    app.run()
    assert not app.exception
    assert app.text_area(key=key(app, "haldi_venue")).value == "Our private venue override"
    ai.assert_not_called()


@pytest.mark.parametrize("legacy", [False, True])
def test_use_latest_requires_confirmation_and_preserves_other_card_edits_and_photo(environment, legacy):
    repo, create, reopen, ai = environment
    row, app = create()
    saved = replace(current(app), invitation_type="bride_family", template_id="festive",
        wording="Keep our custom wording", family={"hosts": "Our chosen hosts"}, photo_expected=True,
        overrides={"bride_name": "Card-only name", "location": "Card-only city"},
        event_overrides={"haldi": {"venue": "Card-only venue", "details": "Keep this event note"}})
    repo.save_invitation(row["project_id"], saved)
    if legacy:
        repo._client.rows[0]["wedding_profile"]["wedding_invitations"]["invitations"][0].pop("linking_version")
    app = reopen(row["project_id"])
    photo = photo_bytes()
    app.session_state[PHOTOS_KEY] = {saved.invitation_id: photo}
    advance(app)
    assert current(app).linking_version == (0 if legacy else 1)
    assert app.button(key="wedding_invitation_use_latest").disabled
    assert app.text_input(key=key(app, "general_bride_name")).value == "Card-only name"
    app.checkbox(key=key(app, "use_latest_confirm")).check().run()
    app.button(key="wedding_invitation_use_latest").click().run()
    assert not app.exception
    updated = current(app)
    assert not updated.overrides and updated.event_overrides == {"haldi": {"details": "Keep this event note"}}
    assert updated.linking_version == 1 and updated.wording == saved.wording and updated.family == saved.family
    assert updated.template_id == saved.template_id and updated.selected_ceremony_ids == saved.selected_ceremony_ids
    assert app.session_state[PHOTOS_KEY][saved.invitation_id] == photo
    assert app.text_input(key=key(app, "general_bride_name")).value == "Priya"
    assert repo.load_selected_project(row["project_id"])["wedding_profile"]["bride_name"] == "Priya"
    ai.assert_not_called()


def test_download_checklist_links_directly_to_missing_details_and_keeps_save_available(environment):
    _repo, create, _open, _ai = environment
    _row, app = create(minimal=True)
    advance(app, 4)
    assert any(w.value == "Complete or confirm these invitation details to download your card." for w in app.warning)
    captions = " ".join(c.value for c in app.caption)
    assert "What bride's name" in captions and "What venue" in captions and "What time" in captions
    assert app.get("download_button")[0].proto.disabled
    assert not app.button(key="wedding_invitation_save").disabled
    app.button(key="wedding_invitation_complete").click().run()
    assert not app.exception
    assert app.selectbox(key=key(app, "section")).value == "Names & wording"
    app.text_input(key=key(app, "general_bride_name")).set_value("Esha").run()
    app.text_input(key=key(app, "general_groom_name")).set_value("Vishal").run()
    advance(app, 3)
    app.button(key="wedding_invitation_complete").click().run()
    assert app.selectbox(key=key(app, "section")).value == "Event details"


def test_multi_page_download_contains_actual_png_files():
    from ai_event_planning_platform.invitation_rendering import render_invitation
    from ai_event_planning_platform.invitations import resolve_invitation
    p, cs, card = ready_card()
    page = render_invitation(card, resolve_invitation(card, p, cs)).pages[0]
    data, filename, mime = _download_data(card, (page, page))
    assert filename.endswith(".zip") and mime == "application/zip"
    with ZipFile(BytesIO(data)) as archive:
        assert len(archive.namelist()) == 2
        assert all(archive.read(name).startswith(b"\x89PNG") for name in archive.namelist())
