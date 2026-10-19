"""Illustrative INR planning arithmetic. Rates are assumptions, never vendor prices."""

from dataclasses import asdict, dataclass, field, replace
from decimal import Decimal, ROUND_CEILING
import hashlib
import json
from collections.abc import Mapping, Sequence
from uuid import uuid4

from ai_event_planning_platform.ceremony_plan import CeremonyPlan, VenuePreference
from ai_event_planning_platform.wedding_ceremonies import Ceremony
from ai_event_planning_platform.wedding_profile import WeddingProfile


MEALS = ("breakfast", "lunch", "dinner", "snacks", "none")
SERVICES = (
    "catering", "service_staff", "seating", "venue_rental", "decoration",
    "lighting_sound", "photography", "entertainment", "ceremony_supplies",
    "travel_accommodation",
)
SHARED_SERVICES = frozenset({"photography", "travel_accommodation"})


@dataclass(frozen=True)
class CostAssumptions:
    """Configurable low/high rupee ranges; intentionally independent of city/budget."""

    meal_rates: dict[str, tuple[int, int]] = field(default_factory=lambda: {
        "breakfast": (150, 250), "snacks": (100, 200),
        "lunch": (300, 500), "dinner": (400, 700), "none": (0, 0),
    })
    venue_rates: dict[str, tuple[int, int]] = field(default_factory=lambda: {
        "home": (0, 0), "banquet_hall": (20000, 50000), "hotel": (40000, 90000),
        "outdoor": (15000, 40000), "other": (15000, 40000),
        "undecided": (15000, 50000),
    })
    service_rates: dict[str, tuple[int, int]] = field(default_factory=lambda: {
        "service_staff": (30, 60), "seating": (40, 80),
        "decoration": (5000, 15000), "lighting_sound": (5000, 15000),
        "photography": (15000, 35000), "entertainment": (8000, 20000),
        "ceremony_supplies": (2000, 5000), "travel_accommodation": (1500, 3500),
    })
    style_percent: dict[str, int] = field(default_factory=lambda: {
        "simple": 85, "traditional": 100, "modern": 100, "mixed": 100, "luxury": 150,
    })
    contingency_percent: int = 10

    def __post_init__(self):
        expected = ((self.meal_rates, set(MEALS)),
                    (self.venue_rates, {v.value for v in VenuePreference}),
                    (self.service_rates, set(SERVICES) - {"catering", "venue_rental"}))
        for rates, keys in expected:
            if not isinstance(rates, dict) or set(rates) != keys:
                raise ValueError("Cost assumptions have unsupported categories.")
            for bounds in rates.values():
                if (not isinstance(bounds, (list, tuple)) or len(bounds) != 2
                        or any(type(v) is not int or v < 0 for v in bounds)
                        or bounds[0] > bounds[1]):
                    raise ValueError("Cost assumptions require non-negative, ordered INR ranges.")
        if (not isinstance(self.style_percent, dict)
                or set(self.style_percent) != {"simple", "traditional", "modern", "mixed", "luxury"}
                or any(type(v) is not int or v <= 0 for v in self.style_percent.values())):
            raise ValueError("Style multipliers must be positive whole percentages.")
        if type(self.contingency_percent) is not int or not 0 <= self.contingency_percent <= 100:
            raise ValueError("Contingency percentage must be between 0 and 100.")


@dataclass(frozen=True)
class CeremonyNeeds:
    ceremony_id: str
    guest_count: int | None
    catering: str
    venue: str
    services: tuple[str, ...]
    explanation: str
    source: str = "rules"
    reviewed: bool = False


@dataclass(frozen=True)
class Expense:
    ceremony_id: str | None
    category: str
    minimum_inr: int
    maximum_inr: int
    allocated_inr: int
    basis: str
    name: str | None = None
    expense_id: str | None = None

    @property
    def key(self) -> str:
        return self.expense_id or f"{self.ceremony_id or 'shared'}:{self.category}"

    @property
    def display_name(self) -> str:
        return self.name if self.name is not None else self.category.replace("_", " ").capitalize()


@dataclass(frozen=True)
class BudgetPlan:
    context_fingerprint: str
    needs: tuple[CeremonyNeeds, ...]
    expenses: tuple[Expense, ...]
    contingency_inr: int
    assumptions: CostAssumptions
    accepted: bool = False
    removed_expenses: tuple[Expense, ...] = ()
    ceremony_budgets: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class BudgetAssessment:
    ceremony_allocations: dict[str, int]
    ceremony_reserves: dict[str, int]
    shared_inr: int
    contingency_inr: int
    total_inr: int
    remaining_inr: int
    minimum_inr: int
    maximum_inr: int
    issues: tuple[str, ...]
    warnings: tuple[str, ...]
    ceremony_expenses: dict[str, int] = field(default_factory=dict)
    ceremony_remaining: dict[str, int] = field(default_factory=dict)

    @property
    def feasible(self) -> bool:
        return not self.issues


def format_inr(value: int | float) -> str:
    amount = Decimal(str(value))
    sign = "-" if amount < 0 else ""
    whole, fraction = f"{abs(amount):.2f}".split(".")
    tail, rest = whole[-3:], whole[:-3]
    groups = []
    while rest:
        groups.insert(0, rest[-2:])
        rest = rest[:-2]
    number = ",".join([*groups, tail])
    return f"{sign}₹{number}" + (f".{fraction}" if fraction != "00" else "")


def budget_context(profile: WeddingProfile, ceremonies: Sequence[Ceremony]) -> dict:
    return {
        # Display-only names must not invalidate accepted budgets or legacy hashes.
        "profile": {k: v for k, v in asdict(profile).items() if k not in {"bride_name", "groom_name"}},
        "ceremonies": [asdict(c) for c in sorted(ceremonies, key=lambda c: c.ceremony_id)],
    }


def context_fingerprint(profile: WeddingProfile, ceremonies: Sequence[Ceremony]) -> str:
    return hashlib.sha256(json.dumps(
        budget_context(profile, ceremonies), sort_keys=True, default=str,
    ).encode()).hexdigest()


def infer_ceremony_needs(profile: WeddingProfile, ceremonies: Sequence[Ceremony]) -> tuple[CeremonyNeeds, ...]:
    """Unconfirmed defaults; no ceremony plans or user budgets are mutated."""
    result = []
    for ceremony in ceremonies:
        plan = ceremony.plan or CeremonyPlan(ceremony.ceremony_id, ceremony.name)
        timing = plan.preferred_timing.value if plan.preferred_timing else "flexible"
        meal = {"morning": "breakfast", "afternoon": "lunch", "evening": "snacks", "night": "dinner"}.get(timing, "snacks")
        services = ["catering", "service_staff", "seating", "decoration", "photography", "ceremony_supplies"]
        if plan.venue_preference is not VenuePreference.HOME:
            services.append("venue_rental")
        if timing in {"evening", "night"}:
            services.append("lighting_sound")
        if ceremony.ceremony_id in {"reception", "sangeet", "mehendi"}:
            services.append("entertainment")
        if profile.location_preference and profile.location_preference.value == "destination":
            services.append("travel_accommodation")
        result.append(CeremonyNeeds(
            ceremony.ceremony_id,
            plan.guest_count if plan.guest_count is not None else profile.guest_count,
            meal, plan.venue_preference.value, tuple(services),
            f"{timing.title()} suggests {meal}. Venue: {plan.venue_preference.value}. "
            "Missing ceremony guests use the overall guest count as a suggestion. "
            "Review meal, services and any venue package inclusions.",
        ))
    return tuple(result)


def _whole(value: object) -> bool:
    return type(value) is int and 0 <= value <= (1 << 53) - 1


def validate_needs(needs: Sequence[CeremonyNeeds], ceremonies: Sequence[Ceremony]) -> None:
    ids = [c.ceremony_id for c in ceremonies]
    if not ids or len(ids) != len(set(ids)) or any(not c.selected for c in ceremonies):
        raise ValueError("Choose unique confirmed ceremonies first.")
    if {n.ceremony_id for n in needs} != set(ids) or len(needs) != len(ids):
        raise ValueError("Service needs must match all confirmed ceremonies exactly once.")
    for n in needs:
        if (n.guest_count is not None and not _whole(n.guest_count)
                or n.catering not in MEALS or n.venue not in {v.value for v in VenuePreference}
                or not isinstance(n.services, tuple) or len(set(n.services)) != len(n.services)
                or not set(n.services) <= set(SERVICES)
                or n.source not in {"rules", "groq"} or type(n.reviewed) is not bool
                or not isinstance(n.explanation, str) or len(n.explanation) > 2000):
            raise ValueError("Invalid service needs.")


def generate_budget(
    profile: WeddingProfile, ceremonies: Sequence[Ceremony],
    needs: Sequence[CeremonyNeeds] | None = None,
    assumptions: CostAssumptions | None = None,
) -> BudgetPlan:
    if not _whole(profile.budget_inr):
        raise ValueError("Enter and save the total wedding budget first.")
    assumptions = assumptions or CostAssumptions()
    needs = tuple(needs) if needs is not None else infer_ceremony_needs(profile, ceremonies)
    validate_needs(needs, ceremonies)
    multiplier = assumptions.style_percent.get(profile.style.value, 100) if profile.style else 100
    expenses = []

    def add(ceremony_id, category, rates, units, basis, style=True):
        factor = multiplier if style else 100
        low, high = ((rate * units * factor + 99) // 100 for rate in rates)
        expenses.append(Expense(ceremony_id, category, low, high, (low + high + 1) // 2, basis))

    for n in needs:
        guests = n.guest_count or 0
        for service in n.services:
            if service in SHARED_SERVICES:
                continue
            if service == "catering":
                add(n.ceremony_id, service, assumptions.meal_rates[n.catering], guests,
                    f"{guests} guests × {n.catering} rate; style {multiplier}%.")
            elif service == "venue_rental":
                add(n.ceremony_id, service, assumptions.venue_rates[n.venue], 1,
                    f"One {n.venue} rental; style {multiplier}%. Home rental is zero.")
            else:
                per_guest = service in {"service_staff", "seating"}
                add(n.ceremony_id, service, assumptions.service_rates[service], guests if per_guest else 1,
                    f"{'Per guest' if per_guest else 'Per ceremony'}; style {multiplier}%.")
    for category in sorted(SHARED_SERVICES):
        covered = [n for n in needs if category in n.services]
        if not covered:
            continue
        if category == "photography":
            add(None, category, assumptions.service_rates[category], 1,
                "One shared package across selected ceremonies. Confirm coverage/days; no per-ceremony duplicate.")
        else:
            guests = max((n.guest_count or 0 for n in covered), default=0)
            add(None, category, assumptions.service_rates[category], guests,
                f"{guests} guests counted once (largest ceremony), one guest-night travel/accommodation placeholder.", False)
    return BudgetPlan(
        context_fingerprint(profile, ceremonies), needs, tuple(expenses),
        (profile.budget_inr * assumptions.contingency_percent + 99) // 100, assumptions,
        ceremony_budgets={
            c.ceremony_id: _ceremony_constraint(c) if _ceremony_constraint(c) is not None else
            sum(e.allocated_inr for e in expenses if e.ceremony_id == c.ceremony_id)
            for c in ceremonies
        },
    )


def edit_allocations(plan: BudgetPlan, changes: Mapping[str, int], *, contingency_inr: int | None = None) -> BudgetPlan:
    known = {e.key for e in plan.expenses}
    if not set(changes) <= known or any(not _whole(v) for v in changes.values()):
        raise ValueError("Allocation edits must use known expenses and non-negative whole INR.")
    contingency = plan.contingency_inr if contingency_inr is None else contingency_inr
    if not _whole(contingency):
        raise ValueError("Contingency must be non-negative whole INR.")
    return replace(plan, accepted=False, contingency_inr=contingency, expenses=tuple(
        replace(e, allocated_inr=changes.get(e.key, e.allocated_inr), **(
            {"minimum_inr": changes[e.key], "maximum_inr": changes[e.key]}
            if e.category == "custom" and e.key in changes else {}
        )) for e in plan.expenses
    ))


def add_expense(plan: BudgetPlan, ceremony_id: str | None, name: str = "New expense", amount: int = 0) -> BudgetPlan:
    if ceremony_id is not None and ceremony_id not in {n.ceremony_id for n in plan.needs}:
        raise ValueError("Expenses must belong to this project's ceremonies or shared costs.")
    if not isinstance(name, str) or not name.strip() or len(name) > 120 or not _whole(amount):
        raise ValueError("Enter an expense name and a non-negative whole INR amount.")
    expense = Expense(ceremony_id, "custom", amount, amount, amount,
                      "User-added planning amount; no illustrative price is asserted.",
                      name=name.strip(), expense_id=f"custom:{uuid4()}")
    return replace(plan, accepted=False, expenses=(*plan.expenses, expense))


def remove_expense(plan: BudgetPlan, key: str) -> BudgetPlan:
    expense = next((e for e in plan.expenses if e.key == key), None)
    if expense is None:
        raise ValueError("This expense is no longer in the draft.")
    return replace(plan, accepted=False,
                   expenses=tuple(e for e in plan.expenses if e.key != key),
                   removed_expenses=(*plan.removed_expenses, expense))


def restore_expense(plan: BudgetPlan, key: str) -> BudgetPlan:
    expense = next((e for e in plan.removed_expenses if e.key == key), None)
    if expense is None or any(e.key == key for e in plan.expenses):
        raise ValueError("This expense cannot be restored.")
    return replace(plan, accepted=False, expenses=(*plan.expenses, expense),
                   removed_expenses=tuple(e for e in plan.removed_expenses if e.key != key))


def recalculate_scope(plan: BudgetPlan, working: BudgetPlan, ceremony_id: str | None) -> BudgetPlan:
    """Apply only one ceremony (or shared costs) without regenerating suggestions."""
    if (plan.context_fingerprint != working.context_fingerprint or plan.needs != working.needs
            or plan.assumptions != working.assumptions):
        raise ValueError("The budget context changed. Reopen the current draft.")
    budgets = dict(plan.ceremony_budgets)
    if ceremony_id is not None:
        if ceremony_id not in {n.ceremony_id for n in plan.needs}:
            raise ValueError("This ceremony does not belong to the budget.")
        budgets[ceremony_id] = working.ceremony_budgets[ceremony_id]
    def merge(original, replacements):
        rows, inserted = [], False
        for row in original:
            if row.ceremony_id == ceremony_id:
                if not inserted:
                    rows.extend(replacements)
                    inserted = True
            else:
                rows.append(row)
        if not inserted:
            rows.extend(replacements)
        return tuple(rows)

    updated = replace(plan, accepted=False, ceremony_budgets=budgets,
        expenses=merge(plan.expenses, [e for e in working.expenses if e.ceremony_id == ceremony_id]),
        removed_expenses=merge(plan.removed_expenses, [e for e in working.removed_expenses if e.ceremony_id == ceremony_id]),
        contingency_inr=working.contingency_inr if ceremony_id is None else plan.contingency_inr)
    validate_budget_plan(updated)
    return plan if replace(updated, accepted=plan.accepted) == plan else updated


def revise_budget_needs(plan: BudgetPlan, profile: WeddingProfile, ceremonies: Sequence[Ceremony],
                        needs: Sequence[CeremonyNeeds], assumptions: CostAssumptions) -> BudgetPlan:
    """Refresh ranges while preserving custom rows, edits, budgets and explicit removals."""
    generated = generate_budget(profile, ceremonies, needs, assumptions)
    previous = {e.key: e for e in plan.expenses}
    removed = {e.key: e for e in plan.removed_expenses}
    expenses = tuple(
        replace(e, name=previous[e.key].name, allocated_inr=previous[e.key].allocated_inr)
        if e.key in previous else e
        for e in generated.expenses if e.key not in removed
    ) + tuple(e for e in plan.expenses if e.category == "custom")
    generated_keys = {e.key for e in generated.expenses}
    updated = replace(generated, expenses=expenses,
        removed_expenses=tuple(e for e in removed.values() if e.category == "custom" or e.key in generated_keys),
        ceremony_budgets=dict(plan.ceremony_budgets),
        contingency_inr=plan.contingency_inr if assumptions.contingency_percent == plan.assumptions.contingency_percent
        else generated.contingency_inr)
    validate_budget_plan(updated)
    return updated


def _ceremony_constraint(ceremony: Ceremony) -> int | None:
    value = ceremony.plan.estimated_budget_inr if ceremony.plan else None
    if value is None:
        return None
    # Reserve the full entered amount; round fractional rupees upward transparently.
    return int(Decimal(str(value)).to_integral_value(rounding=ROUND_CEILING))


def assess_budget(plan: BudgetPlan, profile: WeddingProfile, ceremonies: Sequence[Ceremony]) -> BudgetAssessment:
    validate_budget_plan(plan)
    validate_needs(plan.needs, ceremonies)
    issues, warnings = [], []
    if plan.context_fingerprint != context_fingerprint(profile, ceremonies):
        issues.append("Wedding context changed. Generate and review a new budget before saving.")
    if not _whole(profile.budget_inr):
        issues.append("Save an overall wedding budget to assess feasibility.")
    if any(n.guest_count is None for n in plan.needs):
        issues.append("Some ceremony guest counts are unknown. Review them before accepting costs.")
    if any(n.venue == "undecided" and "venue_rental" in n.services for n in plan.needs):
        warnings.append("An undecided venue uses a provisional paid-venue range; review the venue choice.")
    if any(n.catering == "none" and "catering" in n.services for n in plan.needs):
        warnings.append("No meal has been selected for one or more catering lines.")
    if any(e.allocated_inr < e.minimum_inr for e in plan.expenses):
        issues.append("Some edited allocations are below illustrative minimum requirements. Adjust the assumptions or reviewed service needs.")
    allocations, reserves, expense_totals, ceremony_remaining = {}, {}, {}, {}
    minimum = maximum = 0
    for ceremony in ceremonies:
        lines = [e for e in plan.expenses if e.ceremony_id == ceremony.ceremony_id]
        subtotal = sum(e.allocated_inr for e in lines)
        low, high = sum(e.minimum_inr for e in lines), sum(e.maximum_inr for e in lines)
        constraint = _ceremony_constraint(ceremony)
        allocation = constraint if constraint is not None else plan.ceremony_budgets.get(ceremony.ceremony_id, subtotal)
        reserve = max(0, allocation - subtotal)
        allocations[ceremony.ceremony_id] = allocation
        expense_totals[ceremony.ceremony_id] = subtotal
        ceremony_remaining[ceremony.ceremony_id] = allocation - subtotal
        reserves[ceremony.ceremony_id] = reserve
        minimum += max(low, constraint or 0)
        maximum += max(high, allocation)
        if low > allocation:
            issues.append(f"{ceremony.name}: minimum {format_inr(low)} exceeds your ceremony budget {format_inr(allocation)}.")
        elif subtotal > allocation:
            issues.append(f"{ceremony.name}: allocations exceed your ceremony budget by {format_inr(subtotal - allocation)}. Review services or explicitly revise the ceremony allocation.")
    shared = [e for e in plan.expenses if e.ceremony_id is None]
    shared_total = sum(e.allocated_inr for e in shared)
    minimum += sum(e.minimum_inr for e in shared) + plan.contingency_inr
    maximum += sum(e.maximum_inr for e in shared) + plan.contingency_inr
    total = sum(max(allocations[cid], expense_totals[cid]) for cid in allocations) + shared_total + plan.contingency_inr
    remaining = (profile.budget_inr or 0) - total
    if minimum > (profile.budget_inr or 0):
        issues.append(f"Minimum requirements including preserved ceremony budgets and contingency need {format_inr(minimum)}; shortfall {format_inr(minimum - (profile.budget_inr or 0))}. Reduce guests, choose home venues, remove optional services or revise the budget.")
    elif remaining < 0:
        issues.append("Suggested allocations exceed the overall ceiling. Review trade-offs or choose lower allocations within the displayed ranges; costs have not been scaled down.")
    if "travel_accommodation" in {e.category for e in shared}:
        warnings.append("Destination logistics assume the largest ceremony guest list attends once for one night. Review guest overlap, nights and travel separately.")
    names = {c.ceremony_id: c.name for c in ceremonies}
    for e in plan.removed_expenses:
        if e.category in {"catering", "venue_rental"}:
            warnings.append(f"{names.get(e.ceremony_id, 'Shared expenses')}: {e.category.replace('_', ' ')} was removed. This estimate may be incomplete; it will not be re-added automatically.")
    return BudgetAssessment(allocations, reserves, shared_total, plan.contingency_inr, total,
                            remaining, minimum, maximum, tuple(issues), tuple(warnings),
                            expense_totals, ceremony_remaining)


def validate_budget_plan(plan: BudgetPlan) -> None:
    if (not isinstance(plan, BudgetPlan) or type(plan.accepted) is not bool
            or not isinstance(plan.context_fingerprint, str) or len(plan.context_fingerprint) != 64
            or not _whole(plan.contingency_inr) or not isinstance(plan.assumptions, CostAssumptions)):
        raise ValueError("Invalid budget plan.")
    if not plan.needs or any(not isinstance(n, CeremonyNeeds) for n in plan.needs):
        raise ValueError("Invalid budget needs.")
    ids = {n.ceremony_id for n in plan.needs}
    if (not isinstance(plan.ceremony_budgets, dict)
            or (plan.ceremony_budgets and set(plan.ceremony_budgets) != ids)
            or any(not _whole(v) for v in plan.ceremony_budgets.values())):
        raise ValueError("Invalid ceremony budget allocations.")
    keys, pairs = set(), set()
    for e in (*plan.expenses, *plan.removed_expenses):
        if (not isinstance(e, Expense) or e.key in keys or e.category not in (*SERVICES, "custom")
                or not all(_whole(v) for v in (e.minimum_inr, e.maximum_inr, e.allocated_inr))
                or e.minimum_inr > e.maximum_inr or not isinstance(e.basis, str)
                or (e.name is not None and (not isinstance(e.name, str) or not e.name.strip() or len(e.name) > 120))
                or (e.ceremony_id is not None and e.ceremony_id not in ids)):
            raise ValueError("Invalid or duplicate budget expense.")
        keys.add(e.key)
        if e.category == "custom":
            if not isinstance(e.expense_id, str) or not e.expense_id.startswith("custom:") or len(e.expense_id) > 80 or e.name is None:
                raise ValueError("Custom expenses require stable IDs and names.")
        else:
            pair = (e.ceremony_id, e.category)
            if (pair in pairs or e.expense_id is not None
                    or (e.category in SHARED_SERVICES) != (e.ceremony_id is None)):
                raise ValueError("Duplicate or misplaced shared budget expense.")
            pairs.add(pair)
    # Require the expected expense shape to prevent dropped or duplicated costs.
    dummy = [Ceremony(n.ceremony_id, n.ceremony_id, "Budget validation", selected=True) for n in plan.needs]
    validate_needs(plan.needs, dummy)
    expected_pairs = {
        (None if service in SHARED_SERVICES else n.ceremony_id, service)
        for n in plan.needs for service in n.services
    }
    if pairs != expected_pairs:
        raise ValueError("Budget expenses must cover reviewed services exactly once.")


def budget_plan_to_json(plan: BudgetPlan) -> dict:
    validate_budget_plan(plan)
    return json.loads(json.dumps({"version": 2, **asdict(plan)}))


def budget_plan_from_json(value: object) -> BudgetPlan:
    if not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] not in (1, 2):
        raise ValueError("Unsupported budget plan JSON.")
    try:
        data = {k: v for k, v in value.items() if k != "version"}
        data["needs"] = tuple(CeremonyNeeds(**{**n, "services": tuple(n["services"])}) for n in data["needs"])
        data["expenses"] = tuple(Expense(**e) for e in data["expenses"])
        data["removed_expenses"] = tuple(Expense(**e) for e in data.get("removed_expenses", ()))
        if "ceremony_budgets" not in data:
            data["ceremony_budgets"] = {
                n.ceremony_id: sum(e.allocated_inr for e in data["expenses"] if e.ceremony_id == n.ceremony_id)
                for n in data["needs"]
            }
        assumptions = dict(data["assumptions"])
        for category in ("meal_rates", "venue_rates", "service_rates"):
            assumptions[category] = {k: tuple(v) for k, v in assumptions[category].items()}
        data["assumptions"] = CostAssumptions(**assumptions)
        plan = BudgetPlan(**data)
        validate_budget_plan(plan)
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ValueError("Invalid budget plan JSON fields.") from None
    return plan
