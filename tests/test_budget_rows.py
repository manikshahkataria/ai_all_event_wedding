from dataclasses import replace
import json

import pytest

from ai_event_planning_platform.budget_engine import (
    add_expense, assess_budget, budget_plan_from_json, budget_plan_to_json,
    edit_allocations, recalculate_scope, remove_expense, restore_expense,
    revise_budget_needs, validate_budget_plan,
)
from ai_event_planning_platform.wedding_project_state import BUDGET_SAVED_KEY, restore_project_workspace
from ai_event_planning_platform.wedding_projects import WeddingProjectRepository
from test_budget_engine import ceremony, profile, reviewed_plan
from test_wedding_projects import FakeClient, OWNER_ONE


def setup_plan():
    p = profile()
    ceremonies = [ceremony(budget=200000), ceremony("reception", budget=200000)]
    return p, ceremonies, reviewed_plan(p, ceremonies)


def test_custom_rows_have_stable_unique_ids_and_survive_renaming():
    _p, _ceremonies, plan = setup_plan()
    working = add_expense(add_expense(plan, "haldi", "Welcome drinks", 1000), "haldi", "Welcome drinks", 2000)
    first, second = working.expenses[-2:]
    assert first.key != second.key
    renamed = replace(first, name="Fresh juice")
    assert renamed.key == first.key
    working = replace(working, expenses=(*working.expenses[:-2], renamed, second))
    validate_budget_plan(working)
    assert budget_plan_from_json(budget_plan_to_json(working)) == working


def test_recalculate_one_ceremony_preserves_other_rows_shared_costs_and_contingency():
    p, ceremonies, plan = setup_plan()
    working = add_expense(plan, "haldi", "Welcome drinks", 1000)
    working = edit_allocations(working, {"reception:catering": 100000}, contingency_inr=1)
    recalculated = recalculate_scope(plan, working, "haldi")
    assert [e for e in recalculated.expenses if e.ceremony_id != "haldi"] == [e for e in plan.expenses if e.ceremony_id != "haldi"]
    assert recalculated.contingency_inr == plan.contingency_inr
    before, after = assess_budget(plan, p, ceremonies), assess_budget(recalculated, p, ceremonies)
    assert after.ceremony_expenses["haldi"] == before.ceremony_expenses["haldi"] + 1000
    assert after.ceremony_allocations == before.ceremony_allocations
    assert after.ceremony_remaining["haldi"] == before.ceremony_remaining["haldi"] - 1000


def test_removal_is_explicit_persistent_and_never_recreated_by_recalculation():
    p, ceremonies, plan = setup_plan()
    working = remove_expense(plan, "haldi:catering")
    recalculated = recalculate_scope(plan, working, "haldi")
    again = recalculate_scope(recalculated, recalculated, "haldi")
    assert not any(e.key == "haldi:catering" for e in again.expenses)
    assert any(e.key == "haldi:catering" for e in again.removed_expenses)
    assert any("estimate may be incomplete" in w for w in assess_budget(again, p, ceremonies).warnings)
    restored = restore_expense(again, "haldi:catering")
    assert next(e for e in restored.expenses if e.key == "haldi:catering") == next(e for e in plan.expenses if e.key == "haldi:catering")
    assert not restored.removed_expenses


def test_removed_custom_row_can_be_restored_with_same_id_name_and_amount():
    _p, _ceremonies, plan = setup_plan()
    working = add_expense(plan, "haldi", "Extra arrangements", 500)
    expense = working.expenses[-1]
    removed = remove_expense(working, expense.key)
    restored = restore_expense(removed, expense.key)
    assert restored.expenses[-1] == expense
    with pytest.raises(ValueError):
        restore_expense(restored, expense.key)


def test_shared_scope_stays_separate_and_is_counted_once_after_edit():
    p, ceremonies, plan = setup_plan()
    working = edit_allocations(plan, {"shared:photography": 30000}, contingency_inr=50000)
    updated = recalculate_scope(plan, working, None)
    a = assess_budget(updated, p, ceremonies)
    assert a.shared_inr == 30000 and a.contingency_inr == 50000
    assert a.ceremony_expenses == assess_budget(plan, p, ceremonies).ceremony_expenses
    assert a.total_inr == 400000 + 30000 + 50000
    assert plan.assumptions.contingency_percent == updated.assumptions.contingency_percent == 10


def test_moving_shared_cost_into_ceremony_or_duplicating_service_is_rejected():
    _p, _ceremonies, plan = setup_plan()
    photo = next(e for e in plan.expenses if e.category == "photography")
    with pytest.raises(ValueError, match="shared"):
        validate_budget_plan(replace(plan, expenses=tuple(replace(e, ceremony_id="haldi") if e == photo else e for e in plan.expenses)))
    with pytest.raises(ValueError, match="duplicate"):
        validate_budget_plan(replace(plan, removed_expenses=(photo,)))


def test_fixed_constraint_is_distinct_from_overspending_expenses_and_blocks_save():
    p, ceremonies, plan = setup_plan()
    working = add_expense(plan, "haldi", "Special arrangements", 250000)
    updated = recalculate_scope(plan, working, "haldi")
    a = assess_budget(updated, p, ceremonies)
    assert a.ceremony_allocations["haldi"] == 200000
    assert a.ceremony_expenses["haldi"] > 200000
    assert a.ceremony_remaining["haldi"] < 0
    assert not a.feasible
    assert ceremonies[0].plan.estimated_budget_inr == 200000


def test_suggested_allocation_only_changes_when_explicitly_edited():
    p, ceremonies = profile(), [ceremony()]
    plan = reviewed_plan(p, ceremonies)
    original = plan.ceremony_budgets["haldi"]
    working = add_expense(plan, "haldi", "Drinks", 1000)
    updated = recalculate_scope(plan, working, "haldi")
    assert updated.ceremony_budgets["haldi"] == original
    assert not assess_budget(updated, p, ceremonies).feasible
    working = replace(updated, ceremony_budgets={"haldi": original + 1000})
    updated = recalculate_scope(updated, working, "haldi")
    assert assess_budget(updated, p, ceremonies).feasible


def test_apply_needs_preserves_custom_names_amounts_and_deletions():
    p, ceremonies, plan = setup_plan()
    working = add_expense(remove_expense(plan, "haldi:catering"), "haldi", "Welcome drinks", 900)
    custom = working.expenses[-1]
    needs = [replace(n, catering="lunch") for n in working.needs]
    updated = revise_budget_needs(working, p, ceremonies, needs, working.assumptions)
    assert custom in updated.expenses
    assert not any(e.key == "haldi:catering" for e in updated.expenses)
    assert updated.removed_expenses == working.removed_expenses
    assert updated.ceremony_budgets == working.ceremony_budgets


def test_version_one_budget_reads_and_upgrades_without_schema_change():
    _p, _ceremonies, plan = setup_plan()
    raw = budget_plan_to_json(plan)
    raw["version"] = 1
    raw.pop("ceremony_budgets")
    raw.pop("removed_expenses")
    for e in raw["expenses"]:
        e.pop("expense_id")
        e.pop("name")
    restored = budget_plan_from_json(json.loads(json.dumps(raw)))
    assert restored.expenses == plan.expenses
    assert restored.removed_expenses == ()
    assert budget_plan_to_json(restored)["version"] == 2


def test_saved_row_edits_additions_and_removals_restore_in_new_workspace():
    p, ceremonies, plan = setup_plan()
    repo = WeddingProjectRepository(FakeClient(), OWNER_ONE)
    row = repo.create_wedding_project("Wedding", p, ceremonies)
    edited = add_expense(remove_expense(plan, "haldi:catering"), "haldi", "Welcome drinks", 900)
    edited = replace(edited, expenses=tuple(replace(e, name="Floral design") if e.key == "reception:decoration" else e for e in edited.expenses), accepted=True)
    saved = repo.save_budget_plan(row["project_id"], edited)
    state = {}
    restore_project_workspace(state, saved)
    assert state[BUDGET_SAVED_KEY] == edited
    assert saved["selected_ceremonies"] == row["selected_ceremonies"]


@pytest.mark.parametrize("amount", [-1, True, 1.5, float("nan"), float("inf")])
def test_custom_amount_validation(amount):
    _p, _ceremonies, plan = setup_plan()
    with pytest.raises(ValueError):
        add_expense(plan, "haldi", "Expense", amount)


def test_recalculation_rejects_invalid_name_and_amount_without_changing_previous_plan():
    _p, _ceremonies, plan = setup_plan()
    first = plan.expenses[0]
    for changed in (replace(first, name="  "), replace(first, allocated_inr=-1)):
        working = replace(plan, expenses=(changed, *plan.expenses[1:]))
        with pytest.raises(ValueError):
            recalculate_scope(plan, working, "haldi")
    assert plan.expenses[0] == first
