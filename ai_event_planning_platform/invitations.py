"""Invitation-only drafts, saved-data projections and deterministic export readiness."""

from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, timezone
import re
from uuid import UUID, uuid4

from ai_event_planning_platform.wedding_profile import WeddingDateStatus
from ai_event_planning_platform.wedding_calendar import saved_calendar_data

TYPES = {"combined": "Combined wedding invitation", "bride_family": "Bride's family invitation",
         "groom_family": "Groom's family invitation", "ceremony": "Individual ceremony invitation"}
TEMPLATES = {"royal": "Traditional Royal", "floral": "Elegant Floral",
             "minimal": "Modern Minimal", "festive": "Festive Celebration"}
TONES = ("Traditional", "Elegant", "Warm", "Modern", "Simple")
DEFAULT_WORDING = "Please join us in celebrating this special occasion.\nYour presence would mean so much to us."
GENERAL_FIELDS = {"bride_name", "groom_name", "date", "certainty", "time", "venue", "location", "details"}
EVENT_FIELDS = {"date", "certainty", "time", "venue", "details"}
FAMILY_FIELDS = {"hosts", "parents", "relatives", "message"}
LINKED_GENERAL_FIELDS = GENERAL_FIELDS - {"details"}
LINKED_EVENT_FIELDS = EVENT_FIELDS - {"details"}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Invitation:
    invitation_id: str
    invitation_type: str = "combined"
    selected_ceremony_ids: tuple[str, ...] = ()
    template_id: str = "floral"
    wording: str = DEFAULT_WORDING
    overrides: dict = field(default_factory=dict)
    event_overrides: dict = field(default_factory=dict)
    family: dict = field(default_factory=dict)
    tone: str = "Warm"
    palette: str = "original"
    announcement: bool = False
    accept_tba: bool = False
    photo_expected: bool = False
    photo_role: str = "couple"
    photo_fit: str = "contain"
    wording_source: str = "starter"
    linking_version: int = 1
    updated_at: str = field(default_factory=now_iso)


def new_invitation(ceremonies=()):
    return Invitation(str(uuid4()), selected_ceremony_ids=tuple(c.ceremony_id for c in ceremonies))


def validate_invitation(card):
    def text(value, limit):
        return isinstance(value, str) and len(value) <= limit and not any(ord(c) < 32 and c not in "\n\t" for c in value)
    try:
        UUID(card.invitation_id)
        stamp = datetime.fromisoformat(card.updated_at)
        if stamp.tzinfo is None:
            raise ValueError
    except (AttributeError, TypeError, ValueError):
        raise ValueError("Invalid invitation identity or timestamp.") from None
    if (not isinstance(card, Invitation) or card.invitation_type not in TYPES or card.template_id not in TEMPLATES
            or not isinstance(card.selected_ceremony_ids, tuple) or len(card.selected_ceremony_ids) > 60
            or len(set(card.selected_ceremony_ids)) != len(card.selected_ceremony_ids)
            or any(not text(cid, 80) or not cid for cid in card.selected_ceremony_ids)
            or (card.invitation_type == "ceremony" and len(card.selected_ceremony_ids) > 1)
            or not text(card.wording, 1200) or card.tone not in TONES or card.palette not in {"original", "alternate"}
            or any(type(v) is not bool for v in (card.announcement, card.accept_tba, card.photo_expected))
            or card.photo_role not in {"bride", "groom", "couple"} or card.photo_fit not in {"contain", "cover"}
            or card.wording_source not in {"starter", "user", "groq", "fallback"}
            or type(card.linking_version) is not int or card.linking_version not in {0, 1}):
        raise ValueError("Invalid invitation design fields.")
    def overrides(value, fields):
        if not isinstance(value, dict) or not set(value) <= fields or any(not text(v, 600) for v in value.values()):
            raise ValueError("Invalid invitation override fields.")
        if value.get("date"):
            raw = value["date"]
            date.fromisoformat(raw + "-01" if len(raw) == 7 else raw)
        if value.get("certainty", "undecided") not in {"fixed", "tentative", "approximate", "undecided"}:
            raise ValueError("Invalid invitation date certainty.")
        if value.get("time") and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value["time"]):
            raise ValueError("Enter clock times in HH:MM format.")
    overrides(card.overrides, GENERAL_FIELDS)
    if not isinstance(card.event_overrides, dict) or not set(card.event_overrides) <= set(card.selected_ceremony_ids):
        raise ValueError("Event overrides must belong to this card's selected ceremonies.")
    for value in card.event_overrides.values():
        overrides(value, EVENT_FIELDS)
    if not isinstance(card.family, dict) or not set(card.family) <= FAMILY_FIELDS or any(not text(v, 600) for v in card.family.values()):
        raise ValueError("Invalid invitation family details.")
    for key in ("bride_name", "groom_name"):
        if key in card.overrides and len(card.overrides[key]) > 120:
            raise ValueError("Names must be at most 120 characters.")


def invitation_to_json(card):
    validate_invitation(card)
    return {**asdict(card), "selected_ceremony_ids": list(card.selected_ceremony_ids)}


def invitation_from_json(raw):
    try:
        if not isinstance(raw, dict) or not {"invitation_id", "invitation_type", "selected_ceremony_ids", "template_id", "wording", "updated_at"} <= set(raw):
            raise ValueError
        card = Invitation(**{**raw, "selected_ceremony_ids": tuple(raw["selected_ceremony_ids"]),
                            "linking_version": raw.get("linking_version", 0)})
        validate_invitation(card)
        return card
    except (TypeError, ValueError, KeyError, AttributeError):
        raise ValueError("Invalid saved invitation draft.") from None


def invitations_from_json(raw):
    if raw is None:
        return ()
    if (not isinstance(raw, dict) or set(raw) != {"version", "invitations"}
            or type(raw["version"]) is not int or raw["version"] != 1
            or not isinstance(raw["invitations"], list) or len(raw["invitations"]) > 60):
        raise ValueError("Unsupported invitation collection.")
    cards = tuple(invitation_from_json(value) for value in raw["invitations"])
    if len({c.invitation_id for c in cards}) != len(cards):
        raise ValueError("Saved invitation IDs must be unique.")
    return cards


def invitations_to_json(cards):
    raw = {"version": 1, "invitations": [invitation_to_json(c) for c in cards]}
    invitations_from_json(raw)
    return raw


def _saved_date(value):
    if isinstance(value, datetime):
        value = value.date()
    return value.isoformat() if isinstance(value, date) else value or ""


def resolve_invitation(card, profile, ceremonies, timeline=None):
    """Read saved source fields each time; invitation overrides never mutate them."""
    schedules, _ = saved_calendar_data(profile, ceremonies, timeline)
    by_id, clocks = {c.ceremony_id: c for c in ceremonies}, {s.ceremony_id: s for s in schedules}
    certainty = {WeddingDateStatus.FIXED_DATE: "fixed", WeddingDateStatus.APPROXIMATE_DATE: "approximate"}.get(profile.wedding_date_status, "undecided")
    base = {"bride_name": profile.bride_name or "", "groom_name": profile.groom_name or "",
        "date": _saved_date(profile.wedding_date), "certainty": certainty, "time": "", "venue": "",
        "location": ", ".join(v for v in (profile.city, profile.state) if v), "details": ""}
    general = {**base, **card.overrides}
    events = []
    for cid in card.selected_ceremony_ids:
        c = by_id.get(cid)
        if c is None:
            continue
        p, s = c.plan, clocks[cid]
        event = {"id": cid, "name": c.name, "date": _saved_date(p.ceremony_date) if p else "",
            "certainty": p.date_certainty.value if p else "undecided",
            "time": s.start_time.strftime("%H:%M") if s.start_time else "",
            "preferred_timing": p.preferred_timing.value if p and p.preferred_timing else "",
            "venue": p.venue_notes or "" if p else "", "details": ""}
        override = card.event_overrides.get(cid, {})
        events.append({**event, **override, **({"preferred_timing": ""} if "time" in override else {})})
    if not card.selected_ceremony_ids:
        events.append({"id": "general", "name": "Wedding celebration", "preferred_timing": "", **general})
    return {"general": general, "events": tuple(events), "family": card.family,
            "unknown_ids": tuple(cid for cid in card.selected_ceremony_ids if cid not in by_id)}


def use_latest_wedding_details(card):
    """Explicitly relink shared fields while retaining the rest of this draft.

    Older cards do not say whether saved override values were copied defaults or
    deliberate edits. Keep honoring them until the user chooses this action.
    """
    return replace(card,
        overrides={k: v for k, v in card.overrides.items() if k not in LINKED_GENERAL_FIELDS},
        event_overrides={cid: {k: v for k, v in values.items() if k not in LINKED_EVENT_FIELDS}
                         for cid, values in card.event_overrides.items()},
        linking_version=1, updated_at=now_iso())


def date_label(value, certainty):
    if not value or certainty == "undecided":
        return ""
    parsed = date.fromisoformat(value + "-01" if len(value) == 7 else value)
    if certainty == "approximate" or len(value) == 7:
        return parsed.strftime("%B %Y") + " (approximate)"
    return parsed.strftime("%d %B %Y") + (" (tentative)" if certainty == "tentative" else "")


def placeholder_text(value, *, allow_tba=False):
    clean = value.strip().casefold()
    return bool(re.search(r"\{\{.*?\}\}|\[[^]]+\]|<[^>]+>", clean)
                or (not allow_tba and re.search(r"\b(?:tba|tbd)\b|to be announced", clean))
                or clean in {"your name", "bride's name", "groom's name", "???"})


def missing_details(card, resolved):
    """Return (key, concise question, blocking) without invoking AI."""
    result = []
    general = resolved["general"]
    accepted = card.announcement and card.accept_tba
    for field, label in (("bride_name", "What bride's name should appear?"), ("groom_name", "What groom's name should appear?")):
        if not general[field].strip() or placeholder_text(general[field]):
            result.append((field, label, True))
    if card.invitation_type in {"bride_family", "groom_family"} and (not card.family.get("hosts", "").strip() or placeholder_text(card.family.get("hosts", ""))):
        result.append(("hosts", "Who is inviting the guests?", True))
    if card.invitation_type == "ceremony" and len(card.selected_ceremony_ids) != 1:
        result.append(("selection", "Choose one confirmed ceremony for this card.", True))
    if resolved["unknown_ids"]:
        result.append(("selection", "Some included ceremonies are no longer confirmed. Review your selection.", True))
    for event in resolved["events"]:
        for field in ("date", "venue", "time"):
            known = bool(event.get(field, "").strip())
            if field == "date":
                known = known and event["certainty"] != "undecided"
            if field == "time" and event.get("preferred_timing"):
                known = True
            unfinished = placeholder_text(event.get(field, ""), allow_tba=accepted)
            if not known or unfinished:
                question = f"What {field} should appear for {event['name']}?"
                result.append((event["id"] + ":" + field, question, unfinished or not accepted))
    printed_text = (card.wording, *card.family.values(), general.get("details", ""),
                    general.get("location", ""), *(e.get("details", "") for e in resolved["events"]))
    if (not card.wording.strip()
            or any(placeholder_text(v, allow_tba=accepted) for v in printed_text)
            or any(placeholder_text(e["name"]) for e in resolved["events"])):
        result.append(("wording", "Review unfinished placeholder wording before exporting.", True))
    return tuple(result)


def update_selection(card, ids):
    ids = tuple(ids)
    return replace(card, selected_ceremony_ids=ids,
        event_overrides={k: v for k, v in card.event_overrides.items() if k in ids}, updated_at=now_iso())
