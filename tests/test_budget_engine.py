from dataclasses import replace
import json

import pytest

from ai_event_planning_platform.budget_engine import (
    CostAssumptions, assess_budget, budget_plan_from_json, budget_plan_to_json,
    edit_allocations, format_inr, generate_budget, infer_ceremony_needs,
)
from ai_event_planning_platform.ceremony_plan import CeremonyPlan, PreferredTiming, VenuePreference
from ai_event_planning_platform.wedding_ceremonies import CEREMONY_BY_ID
from ai_event_planning_platform.wedding_profile import WeddingLocationPreference, WeddingProfile, WeddingStyle


def ceremony(cid="haldi", *, guests=50, timing=PreferredTiming.MORNING,
             venue=VenuePreference.HOME, budget=None):
    c = replace(CEREMONY_BY_ID[cid], selected=True)
    return replace(c, plan=CeremonyPlan(cid, c.name, guest_count=guests,
        preferred_timing=timing, venue_preference=venue, estimated_budget_inr=budget))


def profile(budget=1000000, **kwargs):
    return WeddingProfile(budget_inr=budget, **kwargs)


def reviewed_plan(p, ceremonies):
    return generate_budget(p, ceremonies, [replace(n, reviewed=True) for n in infer_ceremony_needs(p, ceremonies)])


@pytest.mark.parametrize("cid,timing,meal", [
    ("haldi", PreferredTiming.MORNING, "breakfast"),
    ("mehendi", PreferredTiming.EVENING, "snacks"),
    ("reception", PreferredTiming.NIGHT, "dinner"),
    ("wedding_ceremony", PreferredTiming.AFTERNOON, "lunch"),
])
def test_contextual_meals_remain_unconfirmed(cid, timing, meal):
    c = ceremony(cid, timing=timing)
    p = profile()
    needs = infer_ceremony_needs(p, [c])
    assert needs[0].catering == meal
    assert not needs[0].reviewed
    assert needs[0].source == "rules"
    assert c.plan.estimated_budget_inr is None
    assert assess_budget(generate_budget(p, [c]), p, [c]).feasible


def test_guest_count_and_catering_influence_costs_without_budget_scaling():
    p = profile()
    small, large = ceremony(guests=20), ceremony(guests=200)
    a, b = generate_budget(p, [small]), generate_budget(p, [large])
    food = lambda plan: next(e for e in plan.expenses if e.category == "catering")
    assert food(b).minimum_inr == food(a).minimum_inr * 10
    dinner = replace(a.needs[0], catering="dinner")
    assert food(generate_budget(p, [small], [dinner])).minimum_inr > food(a).minimum_inr
    low_budget = profile(1000)
    assert generate_budget(low_budget, [large]).expenses == b.expenses


@pytest.mark.parametrize("venue", [VenuePreference.BANQUET_HALL, VenuePreference.HOTEL, VenuePreference.OUTDOOR])
def test_paid_venues_have_explicit_rental_unlike_home(venue):
    p = profile()
    home, paid = ceremony(), ceremony(venue=venue)
    assert not any(e.category == "venue_rental" for e in generate_budget(p, [home]).expenses)
    rental = next(e for e in generate_budget(p, [paid]).expenses if e.category == "venue_rental")
    assert rental.minimum_inr == CostAssumptions().venue_rates[venue.value][0]


def test_explicit_home_rental_is_zero_but_other_home_services_cost_money():
    c, p = ceremony(), profile()
    n = infer_ceremony_needs(p, [c])[0]
    plan = generate_budget(p, [c], [replace(n, services=(*n.services, "venue_rental"))])
    assert next(e for e in plan.expenses if e.category == "venue_rental").allocated_inr == 0
    assert sum(e.allocated_inr for e in plan.expenses if e.ceremony_id) > 0


def test_missing_ceremony_budgets_generate_with_overall_guest_fallback_and_zero_is_preserved():
    c = ceremony(guests=None)
    p = profile(guest_count=90)
    plan = generate_budget(p, [c])
    assert plan.needs[0].guest_count == 90
    assert assess_budget(plan, p, [c]).feasible
    zero = ceremony(guests=0, budget=0)
    assert generate_budget(p, [zero]).needs[0].guest_count == 0
    assert not assess_budget(generate_budget(p, [zero]), p, [zero]).feasible
    assert zero.plan.estimated_budget_inr == 0


def test_unknown_guests_prevent_false_feasibility():
    c, p = ceremony(guests=None), profile()
    assessment = assess_budget(generate_budget(p, [c]), p, [c])
    assert not assessment.feasible
    assert any("unknown" in issue for issue in assessment.issues)


def test_entered_budget_is_reserved_and_never_overwritten():
    c, p = ceremony(budget=200000), profile()
    plan = generate_budget(p, [c])
    assessment = assess_budget(plan, p, [c])
    direct = sum(e.allocated_inr for e in plan.expenses if e.ceremony_id)
    assert assessment.ceremony_allocations["haldi"] == 200000
    assert assessment.ceremony_reserves["haldi"] == 200000 - direct
    assert assessment.total_inr == 200000 + assessment.shared_inr + 100000
    assert c.plan.estimated_budget_inr == 200000


def test_fractional_user_budget_is_reserved_upward_and_original_preserved():
    c, p = ceremony(budget=200000.25), profile()
    assessment = assess_budget(generate_budget(p, [c]), p, [c])
    assert assessment.ceremony_allocations["haldi"] == 200001
    assert c.plan.estimated_budget_inr == 200000.25


def test_user_constraint_below_minimum_has_named_warning():
    c, p = ceremony(budget=100), profile()
    assessment = assess_budget(generate_budget(p, [c]), p, [c])
    assert not assessment.feasible
    assert any("Haldi / Halad: minimum" in issue for issue in assessment.issues)


def test_midpoint_above_ceremony_cap_does_not_silently_shrink_allocations():
    c, p = ceremony(budget=20000), profile()
    plan = generate_budget(p, [c])
    assessment = assess_budget(plan, p, [c])
    assert not assessment.feasible
    assert any("allocations exceed your ceremony budget" in i for i in assessment.issues)
    edited = edit_allocations(plan, {e.key: e.minimum_inr for e in plan.expenses if e.ceremony_id})
    assert assess_budget(edited, p, [c]).feasible
    assert c.plan.estimated_budget_inr == 20000


def test_shared_photography_and_destination_guests_are_counted_once():
    ceremonies = [ceremony(guests=50), ceremony("reception", guests=100)]
    p = profile(location_preference=WeddingLocationPreference.DESTINATION)
    plan = generate_budget(p, ceremonies)
    shared = [e for e in plan.expenses if e.ceremony_id is None]
    assert len(shared) == 2
    assert next(e for e in shared if e.category == "photography").minimum_inr == 15000
    assert next(e for e in shared if e.category == "travel_accommodation").minimum_inr == 150000
    assert not any(e.ceremony_id and e.category in {"photography", "travel_accommodation"} for e in plan.expenses)
    a = assess_budget(plan, p, ceremonies)
    assert a.total_inr == sum(a.ceremony_allocations.values()) + sum(e.allocated_inr for e in shared) + a.contingency_inr


def test_duplicate_or_omitted_shared_expense_is_rejected():
    c, p = ceremony(), profile()
    plan = generate_budget(p, [c])
    with pytest.raises(ValueError, match="duplicate"):
        assess_budget(replace(plan, expenses=(*plan.expenses, plan.expenses[-1])), p, [c])
    with pytest.raises(ValueError, match="exactly once"):
        assess_budget(replace(plan, expenses=plan.expenses[:-1]), p, [c])


def test_minimum_shortfall_and_overallocation_have_distinct_warnings():
    c = ceremony(guests=100)
    tiny = profile(1000)
    a = assess_budget(generate_budget(tiny, [c]), tiny, [c])
    assert a.minimum_inr > 1000 and a.remaining_inr < 0
    assert any("Minimum requirements" in i and "shortfall" in i for i in a.issues)
    p = profile(65000)
    b = assess_budget(generate_budget(p, [c]), p, [c])
    assert b.minimum_inr <= p.budget_inr < b.total_inr
    assert any("not been scaled down" in i for i in b.issues)


def test_edits_recalculate_exact_totals_and_reject_silent_below_minimum_fit():
    c, p = ceremony(), profile()
    plan = reviewed_plan(p, [c])
    before = assess_budget(plan, p, [c])
    e = plan.expenses[0]
    edited = edit_allocations(plan, {e.key: e.allocated_inr + 1234}, contingency_inr=30000)
    after = assess_budget(edited, p, [c])
    assert after.total_inr == before.total_inr + 1234 - before.contingency_inr + 30000
    under = edit_allocations(plan, {e.key: 0})
    assert any("below illustrative minimum" in i for i in assess_budget(under, p, [c]).issues)
    assert not edited.accepted


@pytest.mark.parametrize("value", [-1, True, 2.5, float("nan"), float("inf")])
def test_invalid_edits_are_rejected(value):
    plan = generate_budget(profile(), [ceremony()])
    with pytest.raises(ValueError):
        edit_allocations(plan, {plan.expenses[0].key: value})


def test_configurable_rates_styles_and_contingency_are_explicit():
    c, p = ceremony(), profile(style=WeddingStyle.LUXURY, city="Pune")
    assumptions = CostAssumptions()
    assumptions = replace(assumptions, meal_rates={**assumptions.meal_rates, "breakfast": (10, 20)}, contingency_percent=5)
    plan = generate_budget(p, [c], assumptions=assumptions)
    food = next(e for e in plan.expenses if e.category == "catering")
    assert (food.minimum_inr, food.maximum_inr) == (750, 1500)
    assert plan.contingency_inr == 50000
    assert generate_budget(replace(p, city="Jaipur"), [c], assumptions=assumptions).expenses == plan.expenses


def test_budget_round_trip_and_invalid_versions():
    plan = replace(reviewed_plan(profile(), [ceremony()]), accepted=True)
    assert budget_plan_from_json(json.loads(json.dumps(budget_plan_to_json(plan)))) == plan
    for raw in ({"version": True}, {"version": 2}, {"version": 1}, None):
        with pytest.raises(ValueError):
            budget_plan_from_json(raw)


def test_context_changes_invalidate_accepted_plan():
    c, p = ceremony(), profile()
    plan = reviewed_plan(p, [c])
    assert any("context changed" in i for i in assess_budget(plan, replace(p, city="Delhi"), [c]).issues)


@pytest.mark.parametrize("value,expected", [(12345678, "₹1,23,45,678"), (1000.5, "₹1,000.50"), (-200000, "-₹2,00,000"), (0, "₹0")])
def test_indian_currency_formatting(value, expected):
    assert format_inr(value) == expected


def test_generation_requires_saved_overall_budget_and_confirmed_ceremonies():
    with pytest.raises(ValueError, match="total wedding budget"):
        generate_budget(WeddingProfile(), [ceremony()])
    with pytest.raises(ValueError, match="confirmed"):
        generate_budget(profile(), [])
