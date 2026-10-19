from copy import deepcopy
from dataclasses import replace
from datetime import date, time
import json

import pytest

from ai_event_planning_platform.invitations import (
    Invitation, TYPES, date_label, invitation_to_json, invitation_from_json,
    invitations_from_json, invitations_to_json, missing_details, new_invitation,
    resolve_invitation, update_selection, use_latest_wedding_details,
)
from ai_event_planning_platform.wedding_profile import WeddingDateStatus
from ai_event_planning_platform.wedding_timeline import fallback_timeline
from test_budget_engine import profile
from test_wedding_timeline import DAY, dated_ceremony


def ready_card(kind="combined"):
    p = profile(bride_name="Priya", groom_name="Rahul", city="Pune", state="Maharashtra")
    c = dated_ceremony()
    c = replace(c, plan=replace(c.plan, venue_notes="The family courtyard"))
    card = replace(new_invitation([c]), invitation_type=kind,
        family={"hosts": "Our family", "parents": "Names entered by this family"} if kind.endswith("family") else {})
    return p, [c], card


def test_autofill_uses_saved_dates_preferred_timing_and_accepted_explicit_clock_only():
    p, cs, card = ready_card()
    result = resolve_invitation(card, p, cs)
    assert result["general"]["bride_name"] == "Priya"
    assert result["events"][0]["date"] == DAY.isoformat()
    assert result["events"][0]["time"] == ""
    assert result["events"][0]["preferred_timing"] == "morning"
    assert result["events"][0]["venue"] == "The family courtyard"
    timeline = fallback_timeline(p, cs)
    timeline = replace(timeline, schedules=(replace(timeline.schedules[0], start_time=time(10)),))
    assert resolve_invitation(card, p, cs, timeline)["events"][0]["time"] == ""
    assert resolve_invitation(card, p, cs, replace(timeline, accepted=True))["events"][0]["time"] == "10:00"


def test_overrides_and_family_selections_never_mutate_profile_or_ceremony_plans():
    p, cs, card = ready_card("bride_family")
    before = deepcopy((p, cs))
    bride = replace(card, overrides={"bride_name": "Invitation-only name"},
        event_overrides={"haldi": {"venue": "Bride-side venue", "date": "2028-01-01", "certainty": "tentative"}})
    groom = replace(new_invitation(), invitation_type="groom_family", family={"hosts": "Groom-side hosts"})
    assert resolve_invitation(bride, p, cs)["events"][0]["venue"] == "Bride-side venue"
    assert resolve_invitation(bride, p, cs)["general"]["bride_name"] == "Invitation-only name"
    assert resolve_invitation(groom, p, cs)["events"][0]["id"] == "general"
    assert (p, cs) == before and groom.selected_ceremony_ids == ()


@pytest.mark.parametrize("kind", tuple(TYPES))
def test_all_card_types_roundtrip_without_photos_or_profile_writes(kind):
    p, cs, card = ready_card(kind)
    assert invitation_from_json(json.loads(json.dumps(invitation_to_json(card)))) == card
    assert invitations_from_json(invitations_to_json((card,))) == (card,)
    assert not any(blocking for _, _, blocking in missing_details(card, resolve_invitation(card, p, cs)))
    assert "photo" not in json.dumps(invitation_to_json(card)).lower().replace("photo_expected", "").replace("photo_fit", "").replace("photo_role", "")


def test_approximate_overall_month_is_not_an_exact_ceremony_day():
    p = profile(wedding_date_status=WeddingDateStatus.APPROXIMATE_DATE, wedding_date=date(2027, 11, 1))
    card = new_invitation()
    result = resolve_invitation(card, p, [])
    label = date_label(result["events"][0]["date"], result["events"][0]["certainty"])
    assert label == "November 2027 (approximate)" and "01" not in label
    explicit = resolve_invitation(new_invitation([dated_ceremony()]), p, [dated_ceremony()])
    assert explicit["events"][0]["date"] == DAY.isoformat()


def test_missing_fields_require_explicit_announcement_consent_and_names():
    p, card = profile(), new_invitation()
    result = resolve_invitation(card, p, [])
    assert {key for key, _, blocked in missing_details(card, result) if blocked} == {"bride_name", "groom_name", "general:date", "general:time", "general:venue"}
    announcement = replace(card, announcement=True, accept_tba=True)
    needs = missing_details(announcement, resolve_invitation(announcement, p, []))
    assert {key for key, _, blocked in needs if blocked} == {"bride_name", "groom_name"}
    named = replace(announcement, overrides={"bride_name": "Priya", "groom_name": "Rahul"})
    assert not any(v[2] for v in missing_details(named, resolve_invitation(named, p, [])))


def test_individual_selection_unknown_ids_and_template_placeholders_block_export():
    p, cs, card = ready_card("ceremony")
    empty = update_selection(card, ())
    assert any(v[0] == "selection" and v[2] for v in missing_details(empty, resolve_invitation(empty, p, cs)))
    assert any(v[0] == "selection" for v in missing_details(card, resolve_invitation(card, p, [])))
    unfinished = replace(card, wording="Dear [Guest], come to {{venue}}.")
    assert any(v[0] == "wording" for v in missing_details(unfinished, resolve_invitation(unfinished, p, cs)))
    with pytest.raises(ValueError):
        invitation_to_json(replace(card, selected_ceremony_ids=("haldi", "reception")))


@pytest.mark.parametrize("field", ["venue", "location", "ceremony_name"])
def test_announcement_consent_does_not_allow_unfinished_printed_tokens(field):
    p, cs, card = ready_card()
    card = replace(card, announcement=True, accept_tba=True)
    if field == "venue":
        card = replace(card, event_overrides={"haldi": {"venue": "[Venue address]"}})
    elif field == "location":
        card = replace(card, overrides={"location": "{{city}}"})
    else:
        cs = [replace(cs[0], name="[Ceremony name]")]
    assert any(v[2] for v in missing_details(card, resolve_invitation(card, p, cs)))


def test_announcement_consent_allows_explicit_tba_venue():
    p, cs, card = ready_card()
    card = replace(card, announcement=True, accept_tba=True,
                   event_overrides={"haldi": {"venue": "To be announced"}})
    assert not any(v[2] for v in missing_details(card, resolve_invitation(card, p, cs)))


@pytest.mark.parametrize("change", [
    {"template_id": "copied-template"}, {"photo_fit": "remote_cartoon"},
    {"overrides": {"date": "2027-02-30"}}, {"overrides": {"time": "25:00"}},
    {"overrides": {"photo_base64": "data:image/png;base64,never"}},
    {"family": {"hosts": b"binary"}}, {"invitation_id": "not-a-uuid"},
])
def test_malformed_invitation_data_is_rejected(change):
    with pytest.raises(ValueError):
        invitation_to_json(replace(new_invitation(), **change))


def test_legacy_collection_and_duplicate_ids_handled_safely():
    assert invitations_from_json(None) == ()
    with pytest.raises(ValueError):
        invitations_from_json({"version": 99, "invitations": []})
    card = new_invitation()
    with pytest.raises(ValueError):
        invitations_to_json((card, card))
    missing_stamp = invitation_to_json(card)
    missing_stamp.pop("updated_at")
    with pytest.raises(ValueError):
        invitation_from_json(missing_stamp)


def test_legacy_overrides_remain_unchanged_until_explicit_relink():
    p, cs, card = ready_card()
    card = replace(card, overrides={"bride_name": "Possibly intentional name", "date": "2027-11", "certainty": "approximate"},
                   event_overrides={"haldi": {"venue": "Possibly intentional venue"}})
    raw = invitation_to_json(card)
    assert raw.pop("linking_version") == 1
    legacy = invitation_from_json(raw)
    assert legacy.linking_version == 0
    assert invitation_from_json(invitation_to_json(legacy)) == legacy
    resolved = resolve_invitation(legacy, p, cs)
    assert resolved["general"]["bride_name"] == "Possibly intentional name"
    assert resolved["events"][0]["venue"] == "Possibly intentional venue"


def test_explicit_latest_details_only_removes_shared_overrides():
    p, cs, card = ready_card("bride_family")
    legacy = replace(card, linking_version=0, template_id="royal", palette="alternate", wording="Keep my invitation message",
        photo_expected=True, photo_role="bride", photo_fit="cover", announcement=True, accept_tba=True,
        overrides={"bride_name": "Old bride", "groom_name": "Old groom", "date": "2027-01-01", "certainty": "fixed",
                   "time": "11:00", "venue": "Old venue", "location": "Old city", "details": "Keep general details"},
        event_overrides={"haldi": {"date": "2027-01-02", "certainty": "tentative", "time": "12:00",
                                    "venue": "Old event venue", "details": "Keep event details"}})
    updated = use_latest_wedding_details(legacy)
    assert updated.overrides == {"details": "Keep general details"}
    assert updated.event_overrides == {"haldi": {"details": "Keep event details"}}
    assert updated.linking_version == 1
    assert replace(updated, overrides=legacy.overrides, event_overrides=legacy.event_overrides,
                   linking_version=0, updated_at=legacy.updated_at) == legacy
    assert resolve_invitation(updated, p, cs)["general"]["bride_name"] == p.bride_name
    assert resolve_invitation(updated, p, cs)["events"][0]["venue"] == cs[0].plan.venue_notes
    assert legacy.overrides["bride_name"] == "Old bride"


@pytest.mark.parametrize("initial_status,initial_date", [
    (WeddingDateStatus.APPROXIMATE_DATE, "2027-11"),
    (WeddingDateStatus.NOT_DECIDED_YET, None),
])
def test_linked_missing_or_approximate_wedding_details_follow_latest_saved_profile(initial_status, initial_date):
    initial = profile(bride_name="Previous bride", groom_name="Previous groom", city="Old city",
                      wedding_date_status=initial_status, wedding_date=initial_date)
    card = new_invitation()
    before = resolve_invitation(card, initial, [])
    latest = replace(initial, bride_name="Esha", groom_name="Vishal", city="Manali",
                     wedding_date_status=WeddingDateStatus.FIXED_DATE, wedding_date=date(2027, 11, 18))
    resolved = resolve_invitation(card, latest, [])
    assert resolved["general"] == {**before["general"], "bride_name": "Esha", "groom_name": "Vishal",
                                   "location": "Manali", "date": "2027-11-18", "certainty": "fixed"}
    assert resolved["events"][0]["date"] == "2027-11-18"
    assert not card.overrides and card.linking_version == 1


def test_saved_ceremony_and_accepted_timeline_changes_refresh_only_linked_fields():
    p, cs, card = ready_card()
    timeline = replace(fallback_timeline(p, cs), accepted=True)
    timeline = replace(timeline, schedules=(replace(timeline.schedules[0], start_time=time(10)),))
    latest_ceremonies = [replace(cs[0], plan=replace(cs[0].plan, ceremony_date=date(2027, 11, 19), venue_notes="New saved venue"))]
    latest_timeline = replace(timeline, schedules=(replace(timeline.schedules[0], start_time=time(14, 30)),))
    before = resolve_invitation(card, p, cs, timeline)
    after = resolve_invitation(card, p, latest_ceremonies, latest_timeline)
    assert before["events"][0]["time"] == "10:00"
    assert {k: after["events"][0][k] for k in ("date", "time", "venue")} == {
        "date": "2027-11-19", "time": "14:30", "venue": "New saved venue"}
    custom = replace(card, overrides={"bride_name": "Printed name", "location": "Private location"},
        event_overrides={"haldi": {"date": "2027-12-01", "certainty": "tentative", "time": "09:00", "venue": "Private venue"}})
    resolved = resolve_invitation(custom, replace(p, bride_name="Updated source"), latest_ceremonies, latest_timeline)
    assert resolved["general"]["bride_name"] == "Printed name" and resolved["general"]["location"] == "Private location"
    assert {k: resolved["events"][0][k] for k in custom.event_overrides["haldi"]} == custom.event_overrides["haldi"]


@pytest.mark.parametrize("value", [True, -1, 2, "1", None])
def test_unknown_or_malformed_linking_version_is_rejected(value):
    with pytest.raises(ValueError):
        invitation_to_json(replace(new_invitation(), linking_version=value))
