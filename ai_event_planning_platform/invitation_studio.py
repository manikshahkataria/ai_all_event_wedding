"""A guided, local-preview invitation studio with session-only original photographs."""

from dataclasses import replace
from datetime import date, time
from io import BytesIO
import hashlib
import json
from zipfile import ZipFile, ZIP_STORED

import streamlit as st

from ai_event_planning_platform.invitations import (
    TYPES, TEMPLATES, TONES, invitation_to_json, new_invitation, now_iso,
    resolve_invitation, missing_details, update_selection, validate_invitation,
    use_latest_wedding_details, LINKED_GENERAL_FIELDS, LINKED_EVENT_FIELDS,
)
from ai_event_planning_platform.invitation_rendering import render_invitation, validate_photo
from ai_event_planning_platform.invitation_wording import cached_wording
from ai_event_planning_platform.invitation_state import (
    ACTIVE_CARD_KEY, WORKSPACE_KEY, STAGE_KEY, PHOTOS_KEY, active_card,
    save_invitation_draft, refresh_invitation_context, refresh_invitation_sources,
)
from ai_event_planning_platform.wedding_project_state import (
    ACTIVE_PROJECT_ID_KEY, PROFILE_SAVED_KEY, TIMELINE_SAVED_KEY,
    INVITATIONS_SAVED_KEY, INVITATIONS_ERROR_KEY, PENDING_INVITATION_KEY,
    saved_project_ceremonies,
)

STAGES = ("Choose a card", "Details", "Photo", "Preview", "Save & download")
PREFIX = "wedding_invitation_widget_"


def _key(card, field):
    return PREFIX + card.invitation_id + "_" + field


def _current():
    return st.session_state[WORKSPACE_KEY][st.session_state[ACTIVE_CARD_KEY]]


def _clear_widgets(card):
    for key in tuple(st.session_state):
        if key.startswith(PREFIX + card.invitation_id + "_"):
            del st.session_state[key]


def _store(card):
    workspace = dict(st.session_state[WORKSPACE_KEY])
    workspace[card.invitation_id] = replace(card, updated_at=now_iso())
    st.session_state[WORKSPACE_KEY] = workspace
    st.session_state["wedding_invitation_reviewed"] = False
    st.session_state.pop(_key(card, "use_latest_confirm"), None)
    st.session_state.pop("wedding_invitation_save_notice", None)


def _edit(group, field, key):
    card, value = _current(), st.session_state[key]
    if group == "card":
        if field == "invitation_type":
            versions = dict(st.session_state.get("wedding_invitation_type_versions", {}))
            versions[(card.invitation_id, card.invitation_type)] = card
            prior = versions.get((card.invitation_id, value))
            ids = tuple(c.ceremony_id for c in saved_project_ceremonies(st.session_state)) if value == "combined" else ()
            updated = prior or replace(card, invitation_type=value, selected_ceremony_ids=ids, family={}, event_overrides={})
            st.session_state["wedding_invitation_type_versions"] = versions
            _clear_widgets(card)
            _store(updated)
            return
        if field == "selected_ceremony_ids":
            ids = (value,) if isinstance(value, str) else tuple(value or ())
            _store(update_selection(card, ids))
            return
        _store(replace(card, **{field: value}, **({"wording_source": "user"} if field == "wording" else {})))
        return
    if field == "date" and isinstance(value, date):
        value = value.isoformat()
    if field == "time" and isinstance(value, time):
        value = value.strftime("%H:%M")
    if value is None:
        value = ""
    if group == "family":
        _store(replace(card, family={**card.family, field: value}))
        return
    profile, ceremonies = st.session_state[PROFILE_SAVED_KEY], saved_project_ceremonies(st.session_state)
    resolved = resolve_invitation(card, profile, ceremonies, st.session_state.get(TIMELINE_SAVED_KEY))
    old = resolved["general"] if group == "general" else next(e for e in resolved["events"] if e["id"] == group)
    changes = {field: value}
    if field == "certainty" and value in {"fixed", "tentative"} and old.get("certainty") in {"approximate", "undecided"}:
        changes["date"] = ""  # A month/undecided date is never promoted to an exact day.
        st.session_state.pop(_key(card, group + "_date"), None)
    if group == "general":
        _store(replace(card, overrides={**card.overrides, **changes}))
    else:
        events = dict(card.event_overrides)
        events[group] = {**events.get(group, {}), **changes}
        _store(replace(card, event_overrides=events))


def _input(widget, label, card, group, field, value, **kwargs):
    key = _key(card, group + "_" + field)
    # The draft/resolved source is authoritative. Widget defaults alone do not
    # refresh a keyed control after Wedding Details change. Callbacks have
    # already copied deliberate edits into the draft before this point.
    st.session_state[key] = list(value) if widget is st.multiselect else value
    return widget(label, key=key, on_change=_edit, args=(group, field, key), **kwargs)


def _reload_saved_details():
    """Callbacks run before the review checkbox is instantiated on the rerun."""
    try:
        refresh_invitation_context(st.session_state)
    except Exception:
        st.session_state["wedding_invitation_reload_error"] = "Saved details could not be loaded. Your draft remains available."
    else:
        st.session_state.pop("wedding_invitation_reload_error", None)
        st.session_state.pop("wedding_invitation_source_error", None)
        st.session_state["wedding_invitation_reviewed"] = False
        st.session_state["wedding_invitation_comparing"] = True


def _has_linked_overrides(card):
    return bool(set(card.overrides) & LINKED_GENERAL_FIELDS
                or any(set(v) & LINKED_EVENT_FIELDS for v in card.event_overrides.values()))


def _use_latest_details():
    card = _current()
    if _has_linked_overrides(card) and not st.session_state.get(_key(card, "use_latest_confirm")):
        return
    try:
        refresh_invitation_sources(st.session_state)
    except Exception:
        st.session_state["wedding_invitation_reload_error"] = "Saved details could not be loaded. Your draft remains available."
        return
    _clear_widgets(card)
    _store(use_latest_wedding_details(card))
    st.session_state.pop("wedding_invitation_source_error", None)
    st.session_state.pop("wedding_invitation_reload_error", None)


def _source_controls(card):
    with st.expander("Linked wedding details", expanded=False):
        if card.linking_version == 0:
            st.caption("This older draft may contain intentional card-only values. Review them before replacing its shared details.")
        else:
            st.caption("Linked fields follow saved wedding details. Fields you edit here become invitation-only overrides.")
        overridden = _has_linked_overrides(card)
        confirmed = not overridden or st.checkbox(
            "Replace card-only names, dates, times, venues and location with saved wedding details",
            key=_key(card, "use_latest_confirm"))
        st.button("Use Latest Wedding Details", key="wedding_invitation_use_latest", on_click=_use_latest_details,
                  disabled=not confirmed)
        st.caption("Custom wording, family details, event selections, design and photos are kept.")


def _select_card():
    cid = st.session_state["wedding_invitation_picker"]
    workspace = dict(st.session_state.get(WORKSPACE_KEY, {}))
    saved = st.session_state.get(INVITATIONS_SAVED_KEY, {})
    # A browser can send the previous formatted option while a template change
    # changes its label. Ignore that transient, invalid selection.
    if not isinstance(cid, str) or cid not in {**saved, **workspace}:
        return
    workspace.setdefault(cid, st.session_state.get(INVITATIONS_SAVED_KEY, {}).get(cid))
    st.session_state[WORKSPACE_KEY] = workspace
    st.session_state[ACTIVE_CARD_KEY] = cid
    st.session_state[STAGE_KEY] = 3
    st.session_state["wedding_invitation_reviewed"] = False


def _new_card():
    card = new_invitation(saved_project_ceremonies(st.session_state))
    workspace = dict(st.session_state.get(WORKSPACE_KEY, {}))
    workspace[card.invitation_id] = card
    st.session_state[WORKSPACE_KEY] = workspace
    st.session_state.setdefault("wedding_invitation_initial", {})[card.invitation_id] = card
    st.session_state[ACTIVE_CARD_KEY] = card.invitation_id
    st.session_state["wedding_invitation_picker"] = card.invitation_id
    st.session_state[STAGE_KEY] = 0
    st.session_state["wedding_invitation_reviewed"] = False


def _move(step):
    st.session_state[STAGE_KEY] = max(0, min(len(STAGES)-1, st.session_state.get(STAGE_KEY, 0) + step))


def _complete_details(field):
    if field == "selection":
        st.session_state[STAGE_KEY] = 0
        return
    section = "Hosting family" if field == "hosts" else "Event details" if ":" in field else "Names & wording"
    card = _current()
    st.session_state[_key(card, "section")] = section
    if ":" in field:
        st.session_state[_key(card, "event_picker")] = field.split(":", 1)[0]
    st.session_state[STAGE_KEY] = 1


def _choose_template(template):
    card = _current()
    st.session_state.pop(_key(card, "card_template_id"), None)
    _store(replace(card, template_id=template))


def _render(card, resolved, photo):
    fingerprint = hashlib.sha256((json.dumps(invitation_to_json(card), sort_keys=True) + json.dumps(resolved, sort_keys=True)).encode() + (photo or b"")).hexdigest()
    cache = dict(st.session_state.get("wedding_invitation_render_cache", {}))
    if fingerprint not in cache:
        cache[fingerprint] = render_invitation(card, resolved, photo)
        while len(cache) > 6:
            cache.pop(next(iter(cache)))
    st.session_state["wedding_invitation_render_cache"] = cache
    return cache[fingerprint], fingerprint


def _preview(card, resolved, photo):
    try:
        rendered, fingerprint = _render(card, resolved, photo)
    except ValueError as error:
        st.error("Check invitation details: " + str(error))
        return None, None
    page = 1
    if len(rendered.pages) > 1:
        page = st.selectbox("Preview page", range(1, len(rendered.pages)+1), key=_key(card, "preview_page"))
        st.caption(f"{len(rendered.pages)} readable pages; download includes every page.")
    st.image(rendered.pages[page-1], width="stretch", output_format="PNG", alt=f"{TEMPLATES[card.template_id]} invitation preview, page {page}")
    for notice in rendered.warnings:
        st.warning(notice)
    return rendered, fingerprint


def _word_context(card, resolved, profile):
    return {"type": TYPES[card.invitation_type], "tone": card.tone,
        "ceremonies": resolved["events"], "hosting_family": card.family,
        "couple": {k: resolved["general"][k] for k in ("bride_name", "groom_name")},
        "cultural_preferences": {"traditions": [v.value for v in profile.traditions],
            "partner_1": str(profile.partner_1_culture or ""), "partner_2": str(profile.partner_2_culture or ""),
            "preferences": profile.additional_preferences or ""}}


def _adopt_wording():
    card = _current()
    proposal = st.session_state["wedding_invitation_wording_proposal"]
    _clear_widgets(card)
    _store(replace(card, wording=proposal["message"], wording_source="fallback" if proposal["notice"] else "groq"))


def _details(card, resolved, profile):
    options = ["Names & wording"]
    if card.invitation_type in {"bride_family", "groom_family"}:
        options.append("Hosting family")
    options.append("Event details")
    section = st.selectbox("Edit details", options, key=_key(card, "section"))
    needs = missing_details(card, resolved)
    if needs:
        for _, question, blocking in needs[:2]:
            st.caption(("Needs attention: " if blocking else "Announcement: ") + question)
        if len(needs) > 2:
            st.caption("You can review other missing details at the preview step.")
    if section == "Names & wording":
        _input(st.text_input, "Bride's name", card, "general", "bride_name", resolved["general"]["bride_name"], max_chars=120)
        _input(st.text_input, "Groom's name", card, "general", "groom_name", resolved["general"]["groom_name"], max_chars=120)
        _input(st.text_area, "Invitation message", card, "card", "wording", card.wording, max_chars=1200, height=150)
        _input(st.selectbox, "Tone", card, "card", "tone", card.tone, options=TONES)
        context = _word_context(card, resolved, profile)
        context_key = hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest()
        if st.button("Suggest Invitation Wording", key="wedding_invitation_suggest"):
            with st.spinner("Choosing wording for your invitation…"):
                message, notice = cached_wording(st.session_state, context)
            st.session_state["wedding_invitation_wording_proposal"] = {"card": card.invitation_id, "context": context_key, "message": message, "notice": notice}
        proposal = st.session_state.get("wedding_invitation_wording_proposal", {})
        if proposal.get("card") == card.invitation_id and proposal.get("context") == context_key:
            st.info(proposal["notice"] or "Suggested wording — review before using. Groq selects verified phrases and cannot add event facts.")
            st.write(proposal["message"])
            st.button("Use this wording", key="wedding_invitation_use_wording", on_click=_adopt_wording)
            if proposal["notice"] and st.button("Retry wording suggestion", key="wedding_invitation_retry_wording"):
                message, notice = cached_wording(st.session_state, context, retry=True)
                st.session_state["wedding_invitation_wording_proposal"] = {"card": card.invitation_id, "context": context_key, "message": message, "notice": notice}
                st.rerun()
    elif section == "Hosting family":
        st.caption("Only details you enter appear. The other family's card can use different hosts and ceremonies.")
        for field, label in (("hosts", "Who is inviting the guests?"), ("parents", "Parents' names (optional)"),
                             ("relatives", "Grandparents or relatives (optional)"), ("message", "Family invitation message (optional)")):
            _input(st.text_area if field == "message" else st.text_input, label, card, "family", field, card.family.get(field, ""), max_chars=600)
    else:
        if not resolved["events"]:
            st.info("Choose a ceremony at the first step to enter its details.")
            return
        events = {e["id"]: e for e in resolved["events"]}
        eid = st.selectbox("Which occasion?", tuple(events), format_func=lambda v: events[v]["name"], key=_key(card, "event_picker"))
        event = events[eid]
        group = "general" if eid == "general" else eid
        _input(st.selectbox, "Date certainty", card, group, "certainty", event["certainty"],
               options=("fixed", "tentative", "approximate", "undecided"), format_func=str.capitalize)
        if event["certainty"] == "approximate":
            _input(st.text_input, "Month (YYYY-MM)", card, group, "date", event["date"][:7], max_chars=7)
        else:
            try:
                value = date.fromisoformat(event["date"]) if event["date"] else None
            except ValueError:
                value = None
            _input(st.date_input, "Date (optional)", card, group, "date", value, min_value=date.min, max_value=date.max,
                   disabled=event["certainty"] == "undecided")
        clock = time.fromisoformat(event["time"]) if event["time"] else None
        _input(st.time_input, "Start time (optional)", card, group, "time", clock, step=60)
        if event.get("preferred_timing") and not event["time"]:
            st.caption("Saved preferred timing: " + event["preferred_timing"] + ". This is labelled as a preference on the card.")
        _input(st.text_area, "Venue/address to print", card, group, "venue", event["venue"], max_chars=600)
        _input(st.text_input, "City / location line (optional)", card, "general", "location", resolved["general"]["location"], max_chars=600)
        _input(st.text_area, "Event or family-specific details (optional)", card, group, "details", event.get("details", ""), max_chars=600)


def _upload(key):
    upload = st.session_state.get(key)
    if upload is None:
        return
    try:
        if upload.size > 8 * 1024 * 1024:
            raise ValueError("Choose a photo of at most 8 MB.")
        normalized = validate_photo(upload.getvalue(), upload.name)
    except ValueError as error:
        st.session_state["wedding_invitation_photo_error"] = str(error)
        return
    card = _current()
    photos = dict(st.session_state.get(PHOTOS_KEY, {}))
    photos[card.invitation_id] = normalized
    st.session_state[PHOTOS_KEY] = photos
    st.session_state.pop("wedding_invitation_photo_error", None)
    _store(replace(card, photo_expected=True))


def _remove_photo():
    card = _current()
    photos = dict(st.session_state.get(PHOTOS_KEY, {}))
    photos.pop(card.invitation_id, None)
    st.session_state[PHOTOS_KEY] = photos
    st.session_state["wedding_invitation_upload_epoch"] = st.session_state.get("wedding_invitation_upload_epoch", 0) + 1
    st.session_state.pop("wedding_invitation_photo_error", None)
    _store(replace(card, photo_expected=False))


def _reset():
    card = _current()
    original = st.session_state.get(INVITATIONS_SAVED_KEY, {}).get(card.invitation_id) or st.session_state.get("wedding_invitation_initial", {}).get(card.invitation_id)
    if original:
        _clear_widgets(card)
        photos = dict(st.session_state.get(PHOTOS_KEY, {}))
        photos.pop(card.invitation_id, None)
        st.session_state[PHOTOS_KEY] = photos
        st.session_state["wedding_invitation_upload_epoch"] = st.session_state.get("wedding_invitation_upload_epoch", 0) + 1
        _store(original)


def _advanced(card):
    with st.expander("Design & announcement options", expanded=False):
        _input(st.selectbox, "Template", card, "card", "template_id", card.template_id, options=tuple(TEMPLATES), format_func=TEMPLATES.get)
        _input(st.selectbox, "Color palette", card, "card", "palette", card.palette, options=("original", "alternate"), format_func=str.capitalize)
        _input(st.checkbox, "Save the Date / announcement-style card", card, "card", "announcement", card.announcement)
        if card.announcement:
            _input(st.checkbox, "Use 'To be announced' for unresolved dates, times and venues", card, "card", "accept_tba", card.accept_tba)
        else:
            st.caption("For undecided event details, choose announcement style and explicitly accept 'To be announced'. Names and hosts still need review.")
        reset = st.checkbox("Discard unsaved design changes and remove the local photo", key=_key(card, "reset_confirm"))
        st.button("Reset to saved / initial design", key="wedding_invitation_reset", disabled=not reset or bool(st.session_state.get(PENDING_INVITATION_KEY)), on_click=_reset)


def _download_data(card, pages):
    if len(pages) == 1:
        return pages[0], f"invitation-{card.invitation_id[:8]}.png", "image/png"
    target = BytesIO()
    with ZipFile(target, "w", compression=ZIP_STORED) as archive:
        for i, page in enumerate(pages, 1):
            archive.writestr(f"invitation-page-{i:02}.png", page)
    return target.getvalue(), f"invitation-{card.invitation_id[:8]}-pages.zip", "application/zip"


def render_invitation_studio():
    st.title("Wedding Invitation Studio")
    profile = st.session_state.get(PROFILE_SAVED_KEY)
    if not st.session_state.get(ACTIVE_PROJECT_ID_KEY) or profile is None:
        st.info("Open or create a wedding project to begin. Completing the planning forms is not required.")
        return
    try:
        ceremonies = saved_project_ceremonies(st.session_state)
    except ValueError:
        st.error("Saved ceremonies are unavailable. Reopen this wedding project.")
        return
    if st.session_state.get(INVITATIONS_ERROR_KEY):
        st.warning(st.session_state[INVITATIONS_ERROR_KEY])
    if st.session_state.get("wedding_invitation_source_error"):
        st.warning(st.session_state["wedding_invitation_source_error"])
    card = active_card(st.session_state, ceremonies)
    saved = st.session_state.get(INVITATIONS_SAVED_KEY, {})
    workspace = st.session_state[WORKSPACE_KEY]
    ids = tuple(dict.fromkeys([*workspace, *saved]))
    lookup = {**saved, **workspace}
    with st.container(horizontal=True):
        st.session_state["wedding_invitation_picker"] = card.invitation_id
        st.selectbox("Your invitation designs", ids, index=ids.index(card.invitation_id), key="wedding_invitation_picker",
            format_func=lambda v: TYPES[lookup[v].invitation_type] + " · " + TEMPLATES[lookup[v].template_id] + " · " + v[:6],
            on_change=_select_card, disabled=bool(st.session_state.get(PENDING_INVITATION_KEY)))
        st.button("New invitation", key="wedding_invitation_new", on_click=_new_card,
                  disabled=len(ids) >= 60 or bool(st.session_state.get(PENDING_INVITATION_KEY)))
    stage = st.session_state.setdefault(STAGE_KEY, 0)
    st.caption(f"{stage+1} of {len(STAGES)} · {STAGES[stage]} · " + ("Saved draft" if saved.get(card.invitation_id) == card else "Unsaved draft"))
    st.caption("Saved wedding details prefill your card. Edits here stay on this invitation. Save before switching weddings or logging out.")
    resolved = resolve_invitation(card, profile, ceremonies, st.session_state.get(TIMELINE_SAVED_KEY))
    photo = st.session_state.get(PHOTOS_KEY, {}).get(card.invitation_id)
    if card.photo_expected and not photo:
        st.info("This design's photo is not available in this session. Re-upload it at the Photo step or choose to remove it.")
    if stage == 0:
        _input(st.selectbox, "Invitation type", card, "card", "invitation_type", card.invitation_type, options=tuple(TYPES), format_func=TYPES.get)
        names = {c.ceremony_id: c.name for c in ceremonies}
        missing_ids = [cid for cid in card.selected_ceremony_ids if cid not in names]
        options = [*names, *missing_ids]
        if card.invitation_type == "ceremony":
            _input(st.selectbox, "Ceremony for this card", card, "card", "selected_ceremony_ids",
                card.selected_ceremony_ids[0] if card.selected_ceremony_ids else None,
                options=[None, *options], format_func=lambda v: names.get(v, "Choose a ceremony" if v is None else "No longer selected: " + v))
        else:
            _input(st.multiselect, "Ceremonies to include", card, "card", "selected_ceremony_ids", card.selected_ceremony_ids,
                options=options, format_func=lambda v: names.get(v, "No longer selected: " + v))
        if not ceremonies:
            st.info("Start with a wedding announcement now. Confirm ceremonies later to create individual ceremony cards.")
        st.subheader("Choose your style")
        for col, (tid, name) in zip(st.columns(4), TEMPLATES.items()):
            with col:
                try:
                    sample = {**resolved, "events": resolved["events"][:2]}
                    image, _ = _render(replace(card, template_id=tid), sample, photo)
                    st.image(image.pages[0], width="stretch", output_format="PNG", alt=name + " template thumbnail")
                except ValueError:
                    st.caption("Fix invalid details to refresh this template preview.")
                st.button(name, key="wedding_invitation_template_" + tid, type="secondary",
                    icon=":material/check_circle:" if card.template_id == tid else None,
                    on_click=_choose_template, args=(tid,), width="stretch")
        st.caption("Original code-drawn designs · Photos supported in every style · Gallery shows the first events")
    else:
        left, right = st.columns((3, 2))
        with left:
            rendered, fingerprint = _preview(card, resolved, photo)
        with right:
            if stage == 1:
                _details(card, resolved, profile)
                _source_controls(card)
            elif stage == 2:
                st.subheader("Add a photo (optional)")
                st.caption("Private to this browser session. Never sent to Groq or stored in wedding JSONB. Re-upload after logout or reopening a new session.")
                _input(st.selectbox, "Photograph", card, "card", "photo_role", card.photo_role,
                       options=("couple", "bride", "groom"), format_func=lambda v: v.capitalize() + " photograph")
                upload_key = _key(card, "photo_upload_" + str(st.session_state.get("wedding_invitation_upload_epoch", 0)))
                st.file_uploader("Choose or replace photo", type=("jpg", "jpeg", "png"), max_upload_size=8,
                    key=upload_key, on_change=_upload, args=(upload_key,))
                if st.session_state.get("wedding_invitation_photo_error"):
                    st.error(st.session_state["wedding_invitation_photo_error"])
                _input(st.selectbox, "Photo framing", card, "card", "photo_fit", card.photo_fit,
                       options=("contain", "cover"), format_func=lambda v: "Use entire original photo" if v == "contain" else "Crop to fill frame")
                st.button("Remove photo / use design without it", key="wedding_invitation_remove_photo", on_click=_remove_photo,
                          disabled=not photo and not card.photo_expected)
                st.caption("Original-photo support only. AI cartoon portraits are deferred to Sprint 6B.")
            elif stage == 3:
                st.subheader("Review your invitation")
                needs = missing_details(card, resolved)
                blocked = [question for _, question, blocking in needs if blocking]
                if blocked:
                    st.warning("Still to review:\n\n" + "\n\n".join(blocked))
                for _, question, blocking in needs:
                    if not blocking:
                        st.caption(question + " Accepted as 'To be announced'.")
                if not needs:
                    st.caption("All essential details are provided. Review wording, tentative labels and spelling before downloading.")
                _advanced(card)
                _source_controls(card)
                st.button("Edit invitation details", key="wedding_invitation_edit_details", on_click=lambda: st.session_state.update({STAGE_KEY: 1}))
            else:
                st.subheader("Save & download")
                st.caption("Saving keeps the text, family details, selections and design. Original photos remain session-only.")
                if fingerprint != st.session_state.get("wedding_invitation_review_fingerprint"):
                    st.session_state["wedding_invitation_reviewed"] = False
                    st.session_state["wedding_invitation_review_fingerprint"] = fingerprint
                reviewed = st.checkbox("I reviewed all names, event details, tentative labels and text rendering", key="wedding_invitation_reviewed")
                needs = missing_details(card, resolved)
                blockers = [(field, question) for field, question, blocking in needs if blocking]
                photo_missing = card.photo_expected and not photo
                if blockers or photo_missing or not reviewed:
                    st.warning("Complete or confirm these invitation details to download your card.")
                    for _, question in blockers:
                        st.caption(question)
                    if not reviewed:
                        st.caption("Confirm the review checkbox after checking the preview.")
                if photo_missing:
                    st.caption("Re-upload or remove the missing session-only photo to enable download.")
                if blockers:
                    st.button("Complete invitation details", key="wedding_invitation_complete",
                              on_click=_complete_details, args=(blockers[0][0],))
                if st.button("Save invitation draft", key="wedding_invitation_save", type="primary"):
                    try:
                        validate_invitation(card)
                        save_invitation_draft(st.session_state, card)
                    except Exception:
                        st.error("The invitation could not be saved and verified. Your draft and local photo are retained. Resolve other pending project saves or compare the latest saved invitation before retrying.")
                    else:
                        st.session_state["wedding_invitation_save_notice"] = card.invitation_id
                        st.rerun()
                if st.session_state.get("wedding_invitation_save_notice") == card.invitation_id:
                    st.success("Invitation draft saved and read back from Supabase. Photo pixels remain session-only.")
                if rendered:
                    data, filename, mime = _download_data(card, rendered.pages)
                    st.download_button("Download PNG" if len(rendered.pages) == 1 else "Download all PNG pages (ZIP)", data,
                        file_name=filename, mime=mime, key="wedding_invitation_download", on_click="ignore",
                        disabled=not reviewed or any(v[2] for v in needs) or photo_missing)
                st.button("Load latest saved details for comparison", key="wedding_invitation_reload", on_click=_reload_saved_details)
                if st.session_state.get("wedding_invitation_reload_error"):
                    st.error(st.session_state["wedding_invitation_reload_error"])
                if st.session_state.get("wedding_invitation_comparing"):
                    latest = saved.get(card.invitation_id)
                    st.caption("Latest saved wording: " + (latest.wording if latest else "No saved copy of this invitation."))
                    st.info("Your local draft is retained. Review before saving a replacement of the current saved card.")
                _source_controls(card)
    with st.container(horizontal=True):
        st.button("Back a step", key="wedding_invitation_back", on_click=_move, args=(-1,), disabled=stage == 0)
        if stage < len(STAGES)-1:
            blocked = card.invitation_type == "ceremony" and not card.selected_ceremony_ids
            st.button("Continue" if stage < 3 else "Continue to save & download", key="wedding_invitation_continue",
                type="primary", on_click=_move, args=(1,), disabled=blocked)
