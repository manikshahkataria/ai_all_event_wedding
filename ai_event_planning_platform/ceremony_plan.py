"""Validated ceremony plans and deterministic, opt-in planning suggestions."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
import math

from ai_event_planning_platform.wedding_profile import WeddingDateStatus, WeddingProfile


class CeremonyDateCertainty(str, Enum):
    FIXED = "fixed"
    TENTATIVE = "tentative"
    UNDECIDED = "undecided"


class PreferredTiming(str, Enum):
    MORNING = "morning"
    AFTERNOON = "afternoon"
    EVENING = "evening"
    NIGHT = "night"
    FLEXIBLE = "flexible"


class VenuePreference(str, Enum):
    HOME = "home"
    BANQUET_HALL = "banquet_hall"
    HOTEL = "hotel"
    OUTDOOR = "outdoor"
    OTHER = "other"
    UNDECIDED = "undecided"


@dataclass(frozen=True)
class CeremonyPlan:
    ceremony_id: str
    ceremony_name: str
    guest_count: int | None = None
    ceremony_date: date | None = None
    date_certainty: CeremonyDateCertainty = CeremonyDateCertainty.UNDECIDED
    preferred_timing: PreferredTiming | None = None
    venue_preference: VenuePreference = VenuePreference.UNDECIDED
    venue_notes: str | None = None
    estimated_budget_inr: int | float | None = None
    additional_preferences: str | None = None


def validate_ceremony_plan(plan: CeremonyPlan) -> tuple[str, ...]:
    if not isinstance(plan, CeremonyPlan):
        return ("Ceremony plan has an invalid format.",)
    errors = []
    if not isinstance(plan.ceremony_id, str) or not plan.ceremony_id.strip():
        errors.append("A confirmed ceremony ID is required.")
    if not isinstance(plan.ceremony_name, str) or not plan.ceremony_name.strip():
        errors.append("A ceremony name is required.")
    if plan.guest_count is not None and (
        type(plan.guest_count) is not int or plan.guest_count < 0
    ):
        errors.append("Guest count must be a non-negative whole number.")
    if not isinstance(plan.date_certainty, CeremonyDateCertainty):
        errors.append("Choose a valid date certainty.")
    if plan.ceremony_date is not None:
        if not isinstance(plan.ceremony_date, date) or isinstance(plan.ceremony_date, datetime):
            errors.append("Enter a valid calendar date.")
        if plan.date_certainty is CeremonyDateCertainty.UNDECIDED:
            errors.append("Choose Fixed or Tentative for an entered date, or clear the date.")
    if plan.preferred_timing is not None and not isinstance(plan.preferred_timing, PreferredTiming):
        errors.append("Choose a valid preferred timing.")
    if not isinstance(plan.venue_preference, VenuePreference):
        errors.append("Choose a valid venue preference.")
    budget = plan.estimated_budget_inr
    if budget is not None and (
        type(budget) not in (int, float) or budget < 0
        or (type(budget) is float and not math.isfinite(budget))
    ):
        errors.append("Estimated budget must be a non-negative, finite INR amount.")
    for label, text in (
        ("Venue name or notes", plan.venue_notes),
        ("Additional preferences", plan.additional_preferences),
    ):
        if text is not None and not isinstance(text, str):
            errors.append(f"{label} must be text.")
    return tuple(errors)


def missing_plan_details(plan: CeremonyPlan) -> tuple[str, ...]:
    """Missing optional details indicate completeness, without preventing saving."""
    values = {
        "guest count": plan.guest_count is not None,
        "date": plan.ceremony_date is not None,
        "preferred timing": plan.preferred_timing is not None,
        "venue preference": plan.venue_preference is not VenuePreference.UNDECIDED,
        "estimated budget": plan.estimated_budget_inr is not None,
    }
    return tuple(label for label, provided in values.items() if not provided)


def ceremony_plan_warnings(plan: CeremonyPlan, profile: WeddingProfile | None) -> tuple[str, ...]:
    if (profile and type(profile.guest_count) is int and type(plan.guest_count) is int
            and plan.guest_count > profile.guest_count):
        return ("This ceremony's guest count exceeds the overall wedding guest count. You can still save it.",)
    return ()


def ceremony_plan_suggestions(plan: CeremonyPlan, profile: WeddingProfile | None) -> dict[str, object]:
    suggestions = {}
    if profile is None:
        return suggestions
    if plan.guest_count is None and type(profile.guest_count) is int and profile.guest_count > 0:
        suggestions["guest_count"] = profile.guest_count
    if (
        plan.ceremony_id == "wedding_ceremony" and plan.ceremony_date is None
        and plan.date_certainty is CeremonyDateCertainty.UNDECIDED
        and profile.wedding_date_status is WeddingDateStatus.FIXED_DATE
    ):
        wedding_date = profile.wedding_date
        if isinstance(wedding_date, datetime):
            wedding_date = wedding_date.date()
        if isinstance(wedding_date, str):
            try:
                wedding_date = date.fromisoformat(wedding_date)
            except ValueError:
                wedding_date = None
        if isinstance(wedding_date, date):
            suggestions["ceremony_date"] = wedding_date
            suggestions["date_certainty"] = CeremonyDateCertainty.TENTATIVE
    return suggestions


def ceremony_plan_to_json(plan: CeremonyPlan) -> dict[str, object]:
    errors = validate_ceremony_plan(plan)
    if errors:
        raise ValueError("Ceremony plan failed validation.")
    # ID and name come from the enclosing confirmed ceremony, avoiding duplicate identity.
    return {
        "version": 1,
        "guest_count": plan.guest_count,
        "ceremony_date": plan.ceremony_date.isoformat() if plan.ceremony_date else None,
        "date_certainty": plan.date_certainty.value,
        "preferred_timing": plan.preferred_timing.value if plan.preferred_timing else None,
        "venue_preference": plan.venue_preference.value,
        "venue_notes": plan.venue_notes,
        "estimated_budget_inr": plan.estimated_budget_inr,
        "additional_preferences": plan.additional_preferences,
    }


def ceremony_plan_from_json(value: object, *, ceremony_id: str, ceremony_name: str) -> CeremonyPlan:
    if not isinstance(value, Mapping) or type(value.get("version")) is not int or value["version"] != 1:
        raise ValueError("Unsupported ceremony plan JSON.")
    raw_date = value.get("ceremony_date")
    if raw_date is not None and not isinstance(raw_date, str):
        raise ValueError("Invalid ceremony date JSON.")
    timing = value.get("preferred_timing")
    plan = CeremonyPlan(
        ceremony_id=ceremony_id,
        ceremony_name=ceremony_name,
        guest_count=value.get("guest_count"),
        ceremony_date=date.fromisoformat(raw_date) if raw_date is not None else None,
        date_certainty=CeremonyDateCertainty(value.get("date_certainty", "undecided")),
        preferred_timing=PreferredTiming(timing) if timing is not None else None,
        venue_preference=VenuePreference(value.get("venue_preference", "undecided")),
        venue_notes=value.get("venue_notes"),
        estimated_budget_inr=value.get("estimated_budget_inr"),
        additional_preferences=value.get("additional_preferences"),
    )
    if validate_ceremony_plan(plan):
        raise ValueError("Invalid ceremony plan JSON fields.")
    return plan
