from dataclasses import replace
from datetime import date
import json

import pytest

from ai_event_planning_platform.ceremony_plan import (
    CeremonyDateCertainty, CeremonyPlan, PreferredTiming, VenuePreference,
    ceremony_plan_from_json, ceremony_plan_suggestions, ceremony_plan_to_json,
    ceremony_plan_warnings, missing_plan_details, validate_ceremony_plan,
)
from ai_event_planning_platform.wedding_profile import WeddingDateStatus, WeddingProfile


def test_partial_plans_keep_missing_values_blank_and_allow_zero():
    plan = CeremonyPlan("reception", "Reception")
    assert not validate_ceremony_plan(plan)
    assert plan.guest_count is None and plan.ceremony_date is None
    assert plan.estimated_budget_inr is None and plan.preferred_timing is None
    assert len(missing_plan_details(plan)) == 5
    assert not validate_ceremony_plan(replace(plan, guest_count=0, estimated_budget_inr=0))


@pytest.mark.parametrize("count", [-1, 1.5, "80", True, float("nan"), float("inf")])
def test_invalid_guest_counts_are_rejected(count):
    assert "Guest count" in " ".join(validate_ceremony_plan(CeremonyPlan("haldi", "Haldi", guest_count=count)))


@pytest.mark.parametrize("budget", [-0.01, float("nan"), float("inf"), float("-inf"), "1000", True])
def test_negative_nonfinite_and_invalid_budgets_are_rejected(budget):
    assert "budget" in " ".join(validate_ceremony_plan(CeremonyPlan("haldi", "Haldi", estimated_budget_inr=budget)))


@pytest.mark.parametrize("certainty", [CeremonyDateCertainty.FIXED, CeremonyDateCertainty.TENTATIVE])
def test_date_certainty_and_optional_details_round_trip(certainty):
    plan = CeremonyPlan(
        "reception", "Reception", guest_count=80, ceremony_date=date(2027, 10, 24),
        date_certainty=certainty, preferred_timing=PreferredTiming.EVENING,
        venue_preference=VenuePreference.HOME, venue_notes="Family home",
        estimated_budget_inr=60000.50, additional_preferences="Outdoor seating",
    )
    value = json.loads(json.dumps(ceremony_plan_to_json(plan)))
    assert ceremony_plan_from_json(value, ceremony_id="reception", ceremony_name="Reception") == plan
    assert not missing_plan_details(plan)


def test_invalid_calendar_dates_and_undecided_date_are_rejected():
    plan = CeremonyPlan("haldi", "Haldi", ceremony_date="2027-02-30", date_certainty=CeremonyDateCertainty.TENTATIVE)
    assert "calendar date" in " ".join(validate_ceremony_plan(plan))
    raw = ceremony_plan_to_json(CeremonyPlan("haldi", "Haldi"))
    raw.update(ceremony_date="2027-02-30", date_certainty="tentative")
    with pytest.raises(ValueError):
        ceremony_plan_from_json(raw, ceremony_id="haldi", ceremony_name="Haldi")
    plan = CeremonyPlan("haldi", "Haldi", ceremony_date=date(2027, 10, 24))
    assert "Fixed or Tentative" in " ".join(validate_ceremony_plan(plan))


def test_exceeding_wedding_guests_is_a_warning_and_different_counts_are_allowed():
    plan = CeremonyPlan("haldi", "Haldi", guest_count=150)
    assert not validate_ceremony_plan(plan)
    assert ceremony_plan_warnings(plan, WeddingProfile(guest_count=100))
    assert not ceremony_plan_warnings(replace(plan, guest_count=40), WeddingProfile(guest_count=100))


def test_suggestions_are_optional_tentative_and_do_not_modify_the_plan():
    plan = CeremonyPlan("wedding_ceremony", "Wedding ceremony")
    profile = WeddingProfile(guest_count=100, wedding_date_status=WeddingDateStatus.FIXED_DATE, wedding_date=date(2027, 10, 24))
    suggestions = ceremony_plan_suggestions(plan, profile)
    assert suggestions["guest_count"] == 100
    assert suggestions["date_certainty"] is CeremonyDateCertainty.TENTATIVE
    assert plan.guest_count is None and plan.ceremony_date is None
    assert plan.date_certainty is CeremonyDateCertainty.UNDECIDED
    entered = replace(plan, guest_count=40, ceremony_date=date(2027, 10, 23), date_certainty=CeremonyDateCertainty.FIXED)
    assert ceremony_plan_suggestions(entered, profile) == {}


@pytest.mark.parametrize("status", [WeddingDateStatus.APPROXIMATE_DATE, WeddingDateStatus.NOT_DECIDED_YET])
def test_suggestions_never_invent_dates_for_unknown_wedding_dates(status):
    profile = WeddingProfile(wedding_date_status=status, wedding_date=date(2027, 10, 1) if status is WeddingDateStatus.APPROXIMATE_DATE else None)
    assert "ceremony_date" not in ceremony_plan_suggestions(CeremonyPlan("wedding_ceremony", "Wedding ceremony"), profile)


def test_suggestions_do_not_assign_dates_or_timings_to_religious_ceremonies():
    profile = WeddingProfile(wedding_date_status=WeddingDateStatus.FIXED_DATE, wedding_date=date(2027, 10, 24))
    assert ceremony_plan_suggestions(CeremonyPlan("muslim_nikah", "Nikah"), profile) == {}


@pytest.mark.parametrize("changes", [{"date_certainty": "fixed"}, {"preferred_timing": "evening"}, {"venue_preference": "home"}])
def test_model_requires_valid_enum_values(changes):
    assert validate_ceremony_plan(replace(CeremonyPlan("haldi", "Haldi"), **changes))


def test_unknown_plan_versions_are_rejected_instead_of_losing_data():
    with pytest.raises(ValueError, match="Unsupported"):
        ceremony_plan_from_json({"version": 2}, ceremony_id="reception", ceremony_name="Reception")
