from datetime import date
from enum import Enum
from types import SimpleNamespace

import pytest

from ai_event_planning_platform.wedding_profile import (
    CulturalBackground,
    TraditionPreference,
    WeddingLocationPreference,
    WeddingDateStatus,
    WeddingProfile,
    WeddingStyle,
    migrate_wedding_profile,
    validate_wedding_profile,
)


TODAY = date(2026, 10, 14)


def test_incomplete_profile_is_valid_when_unknowns_are_undecided() -> None:
    assert validate_wedding_profile(WeddingProfile(), today=TODAY) == ()


def test_valid_profile_uses_straightforward_optional_values() -> None:
    profile = WeddingProfile(
        wedding_date_status=WeddingDateStatus.APPROXIMATE_DATE,
        wedding_date=date(2027, 5, 1),
        city="Jaipur",
        state="Rajasthan",
        location_preference=WeddingLocationPreference.DESTINATION,
        guest_count=250,
        budget_inr=2500000,
        style=WeddingStyle.TRADITIONAL,
        partner_1_culture=CulturalBackground("Punjabi", "Punjab"),
        partner_2_culture=CulturalBackground("Tamil", "Tamil Nadu"),
        traditions=(TraditionPreference.RELIGIOUS, TraditionPreference.CUSTOM),
        additional_preferences="Outdoor venue",
    )

    assert validate_wedding_profile(profile, today=TODAY) == ()


@pytest.mark.parametrize("invalid_date", ["2027-02-30", "not-a-date", "2026-2"])
def test_invalid_fixed_dates_are_rejected(invalid_date: str) -> None:
    errors = validate_wedding_profile(
        WeddingProfile(
            wedding_date_status=WeddingDateStatus.FIXED_DATE,
            wedding_date=invalid_date,
        ),
        today=TODAY,
    )

    assert "Enter a valid wedding date." in errors


def test_past_confirmed_date_is_rejected() -> None:
    errors = validate_wedding_profile(
        WeddingProfile(
            wedding_date_status=WeddingDateStatus.FIXED_DATE,
            wedding_date=date(2026, 10, 13),
        ),
        today=TODAY,
    )

    assert "Wedding date cannot be in the past." in errors


@pytest.mark.parametrize("budget", [-1, -100])
def test_negative_budget_is_rejected(budget: int) -> None:
    errors = validate_wedding_profile(
        WeddingProfile(budget_inr=budget),
        today=TODAY,
    )

    assert "Wedding budget must be a non-negative whole number of INR." in errors


def test_zero_budget_is_allowed() -> None:
    profile = WeddingProfile(budget_inr=0)

    assert validate_wedding_profile(profile, today=TODAY) == ()


@pytest.mark.parametrize("guest_count", [-1, 0, 1.5, True])
def test_invalid_guest_counts_are_rejected(guest_count: int | float | bool) -> None:
    errors = validate_wedding_profile(
        WeddingProfile(guest_count=guest_count),
        today=TODAY,
    )

    assert "Guest count must be a positive whole number." in errors


@pytest.mark.parametrize("invalid_budget", [True, 1500.5, "1500"])
def test_budget_requires_integer_type(invalid_budget: object) -> None:
    errors = validate_wedding_profile(
        WeddingProfile(budget_inr=invalid_budget), today=TODAY  # type: ignore[arg-type]
    )

    assert "Wedding budget must be a non-negative whole number of INR." in errors


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("city", 12),
        ("state", ["Uttar Pradesh"]),
        ("location_preference", "local"),
        ("style", "modern"),
        ("partner_1_culture", "Punjabi"),
        ("traditions", ["religious"]),
        ("additional_preferences", 42),
    ],
)
def test_profile_rejects_invalid_optional_field_types(
    field_name: str, invalid_value: object
) -> None:
    profile = WeddingProfile(**{field_name: invalid_value})  # type: ignore[arg-type]

    assert validate_wedding_profile(profile, today=TODAY)


def test_approximate_date_cannot_be_in_the_past() -> None:
    errors = validate_wedding_profile(
        WeddingProfile(
            wedding_date_status=WeddingDateStatus.APPROXIMATE_DATE,
            wedding_date=date(2026, 9, 1),
        ),
        today=TODAY,
    )

    assert "Approximate wedding date cannot be in the past." in errors


def test_not_decided_date_cannot_retain_a_date_value() -> None:
    errors = validate_wedding_profile(
        WeddingProfile(wedding_date=date(2027, 5, 1)), today=TODAY
    )

    assert "A date must be blank when marked not decided yet." in errors


def test_legacy_certainty_profile_migrates_entered_values() -> None:
    class LegacyCertainty(Enum):
        CONFIRMED = "confirmed"
        APPROXIMATE = "approximate"
        UNDECIDED = "undecided"

    def answer(value: object, certainty: LegacyCertainty) -> SimpleNamespace:
        return SimpleNamespace(value=value, certainty=certainty)

    old_profile = SimpleNamespace(
        wedding_date=answer("2027-05", LegacyCertainty.APPROXIMATE),
        city=answer("Bareilly", LegacyCertainty.CONFIRMED),
        state=answer("Uttar Pradesh", LegacyCertainty.CONFIRMED),
        location_preference=answer(
            SimpleNamespace(value="destination"), LegacyCertainty.CONFIRMED
        ),
        guest_count=answer(300, LegacyCertainty.APPROXIMATE),
        budget_inr=answer(1300000, LegacyCertainty.CONFIRMED),
        style=answer(SimpleNamespace(value="traditional"), LegacyCertainty.CONFIRMED),
        partner_1_culture=answer(
            SimpleNamespace(background="Punjabi", region="Punjab"),
            LegacyCertainty.CONFIRMED,
        ),
        partner_2_culture=answer(None, LegacyCertainty.UNDECIDED),
        traditions=answer(
            (SimpleNamespace(value="religious"),), LegacyCertainty.CONFIRMED
        ),
        additional_preferences=answer("Outdoor venue", LegacyCertainty.APPROXIMATE),
    )

    migrated = migrate_wedding_profile(old_profile)

    assert migrated is not None
    assert migrated.wedding_date_status is WeddingDateStatus.APPROXIMATE_DATE
    assert migrated.wedding_date == date(2027, 5, 1)
    assert migrated.city == "Bareilly"
    assert migrated.state == "Uttar Pradesh"
    assert migrated.location_preference is WeddingLocationPreference.DESTINATION
    assert migrated.guest_count == 300
    assert migrated.budget_inr == 1300000
    assert migrated.style is WeddingStyle.TRADITIONAL
    assert migrated.partner_1_culture == CulturalBackground("Punjabi", "Punjab")
    assert migrated.traditions == (TraditionPreference.RELIGIOUS,)
    assert migrated.additional_preferences == "Outdoor venue"