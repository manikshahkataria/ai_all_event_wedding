from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum


class WeddingDateStatus(str, Enum):
    FIXED_DATE = "fixed_date"
    APPROXIMATE_DATE = "approximate_date"
    NOT_DECIDED_YET = "not_decided_yet"


class WeddingLocationPreference(str, Enum):
    LOCAL = "local"
    DESTINATION = "destination"


class WeddingStyle(str, Enum):
    SIMPLE = "simple"
    TRADITIONAL = "traditional"
    LUXURY = "luxury"
    MODERN = "modern"
    MIXED = "mixed"


class TraditionPreference(str, Enum):
    RELIGIOUS = "religious"
    SECULAR = "secular"
    CUSTOM = "custom"


@dataclass(frozen=True)
class CulturalBackground:
    background: str | None = None
    region: str | None = None


@dataclass(frozen=True)
class WeddingProfile:
    """Straightforward optional wedding requirements for forms or future AI input."""

    wedding_date_status: WeddingDateStatus = WeddingDateStatus.NOT_DECIDED_YET
    wedding_date: date | str | None = None
    city: str | None = None
    state: str | None = None
    location_preference: WeddingLocationPreference | None = None
    guest_count: int | None = None
    budget_inr: int | None = None
    style: WeddingStyle | None = None
    partner_1_culture: CulturalBackground | None = None
    partner_2_culture: CulturalBackground | None = None
    traditions: tuple[TraditionPreference, ...] = ()
    additional_preferences: str | None = None


def _legacy_value(value: object) -> object:
    return getattr(value, "value", value)


def _enum_value(value: object) -> object:
    return getattr(value, "value", value)


def _clean_text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _coerce_enum(enum_type: type[Enum], value: object) -> Enum | None:
    try:
        return enum_type(_enum_value(value))
    except (TypeError, ValueError):
        return None


def _coerce_date(value: object, *, approximate: bool = False) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    try:
        if approximate and len(value) == 7:
            return date.fromisoformat(f"{value}-01")
        return date.fromisoformat(value)
    except ValueError:
        return None


def _legacy_answer(profile: object, field_name: str) -> tuple[object, str | None]:
    answer = getattr(profile, field_name, None)
    certainty = _enum_value(getattr(answer, "certainty", None))
    return _legacy_value(answer), certainty if isinstance(certainty, str) else None


def _legacy_culture(value: object) -> CulturalBackground | None:
    value = _legacy_value(value)
    if value is None:
        return None
    if isinstance(value, CulturalBackground):
        return value
    background = _clean_text(getattr(value, "background", None))
    region = _clean_text(getattr(value, "region", None))
    return CulturalBackground(background, region) if background or region else None


def migrate_wedding_profile(value: object) -> WeddingProfile | None:
    """Convert an older certainty-wrapped session draft without losing entered values."""
    if isinstance(value, WeddingProfile):
        return value
    if value is None:
        return None

    raw_date, date_certainty = _legacy_answer(value, "wedding_date")
    if date_certainty == "confirmed":
        date_status = WeddingDateStatus.FIXED_DATE
    elif date_certainty == "approximate":
        date_status = WeddingDateStatus.APPROXIMATE_DATE
    else:
        date_status = WeddingDateStatus.NOT_DECIDED_YET
    wedding_date = (
        _coerce_date(
            raw_date, approximate=date_status is WeddingDateStatus.APPROXIMATE_DATE
        )
        if date_status is not WeddingDateStatus.NOT_DECIDED_YET
        else None
    )

    city, _ = _legacy_answer(value, "city")
    state, _ = _legacy_answer(value, "state")
    location, _ = _legacy_answer(value, "location_preference")
    guest_count, _ = _legacy_answer(value, "guest_count")
    budget, _ = _legacy_answer(value, "budget_inr")
    style, _ = _legacy_answer(value, "style")
    partner_1_culture, _ = _legacy_answer(value, "partner_1_culture")
    partner_2_culture, _ = _legacy_answer(value, "partner_2_culture")
    traditions, _ = _legacy_answer(value, "traditions")
    additional_preferences, _ = _legacy_answer(value, "additional_preferences")

    legacy_traditions = tuple(
        tradition
        for tradition in (
            _coerce_enum(TraditionPreference, item)
            for item in (traditions or ())
        )
        if isinstance(tradition, TraditionPreference)
    )
    return WeddingProfile(
        wedding_date_status=date_status,
        wedding_date=wedding_date,
        city=_clean_text(city),
        state=_clean_text(state),
        location_preference=_coerce_enum(WeddingLocationPreference, location),
        guest_count=guest_count if type(guest_count) is int else None,
        budget_inr=budget if type(budget) is int else None,
        style=_coerce_enum(WeddingStyle, style),
        partner_1_culture=_legacy_culture(partner_1_culture),
        partner_2_culture=_legacy_culture(partner_2_culture),
        traditions=legacy_traditions,
        additional_preferences=_clean_text(additional_preferences),
    )


def _is_valid_date(value: object) -> bool:
    if isinstance(value, datetime):
        return True
    if isinstance(value, date):
        return True
    if not isinstance(value, str):
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def validate_wedding_profile(
    profile: WeddingProfile, *, today: date | None = None
) -> tuple[str, ...]:
    """Return deterministic errors while allowing optional, incomplete answers."""
    if not isinstance(profile, WeddingProfile):
        return ("Wedding requirements have an invalid profile format.",)

    errors: list[str] = []
    current_date = today or date.today()
    if not isinstance(profile.wedding_date_status, WeddingDateStatus):
        errors.append("Choose a valid wedding date status.")
    elif profile.wedding_date_status is WeddingDateStatus.NOT_DECIDED_YET:
        if profile.wedding_date is not None:
            errors.append("A date must be blank when marked not decided yet.")
    elif profile.wedding_date is None or not _is_valid_date(profile.wedding_date):
        errors.append("Enter a valid wedding date.")
    else:
        parsed_date = _coerce_date(
            profile.wedding_date,
            approximate=profile.wedding_date_status
            is WeddingDateStatus.APPROXIMATE_DATE,
        )
        if parsed_date is None:
            errors.append("Enter a valid wedding date.")
        elif profile.wedding_date_status is WeddingDateStatus.FIXED_DATE:
            if parsed_date < current_date:
                errors.append("Wedding date cannot be in the past.")
        elif (parsed_date.year, parsed_date.month) < (current_date.year, current_date.month):
            errors.append("Approximate wedding date cannot be in the past.")

    for name, value in (
        ("City", profile.city),
        ("State", profile.state),
        ("Additional preferences", profile.additional_preferences),
    ):
        if value is not None and not isinstance(value, str):
            errors.append(f"{name} must be text.")

    if profile.location_preference is not None and not isinstance(
        profile.location_preference, WeddingLocationPreference
    ):
        errors.append("Choose a valid local or destination preference.")
    if profile.guest_count is not None and (
        type(profile.guest_count) is not int or profile.guest_count <= 0
    ):
        errors.append("Guest count must be a positive whole number.")
    if profile.budget_inr is not None and (
        type(profile.budget_inr) is not int or profile.budget_inr < 0
    ):
        errors.append("Wedding budget must be a non-negative whole number of INR.")
    if profile.style is not None and not isinstance(profile.style, WeddingStyle):
        errors.append("Choose a valid wedding style.")

    for name, culture in (
        ("Partner 1", profile.partner_1_culture),
        ("Partner 2", profile.partner_2_culture),
    ):
        if culture is not None and not isinstance(culture, CulturalBackground):
            errors.append(f"{name} cultural background has an invalid format.")
        elif isinstance(culture, CulturalBackground):
            if culture.background is not None and not isinstance(culture.background, str):
                errors.append(f"{name} cultural background must be text.")
            if culture.region is not None and not isinstance(culture.region, str):
                errors.append(f"{name} region must be text.")

    if not isinstance(profile.traditions, tuple) or any(
        not isinstance(item, TraditionPreference) for item in profile.traditions
    ):
        errors.append("Tradition preferences have an invalid format.")

    return tuple(errors)