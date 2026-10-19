"""Ceremony-level budget editing with staged rows and deterministic recalculation."""

from dataclasses import asdict, replace

import streamlit as st

from ai_event_planning_platform.budget_engine import (
    MEALS, SERVICES, add_expense, assess_budget, context_fingerprint, format_inr,
    generate_budget, infer_ceremony_needs, recalculate_scope, remove_expense,
    restore_expense, revise_budget_needs,
)
from ai_event_planning_platform.budget_intelligence import cached_budget_needs
from ai_event_planning_platform.ceremony_plan import VenuePreference
from ai_event_planning_platform.wedding_summary import render_wedding_at_a_glance
from ai_event_planning_platform.wedding_project_state import (
    ACTIVE_PROJECT_ID_KEY, BUDGET_DRAFT_KEY, BUDGET_ERROR_KEY, BUDGET_SAVED_KEY,
    PENDING_PLAN_SAVES_KEY, PENDING_SAVE_KEY, PROFILE_SAVED_KEY,
    navigate_project_to_page, persist_budget_plan, saved_project_ceremonies,
)


SMART_BUDGET_PAGE = "smart_wedding_budget"
EDITOR_PREFIX = "wedding_budget_editor_"
WORKING_KEY = "wedding_budget_working_rows"
MAX_INR = (1 << 53) - 1


def _label(value):
    return value.replace("_", " ").capitalize()


def _navigate(page):
    navigate_project_to_page(st.session_state, page)


def _new_draft(plan, *, reset_review=True):
    st.session_state[BUDGET_DRAFT_KEY] = plan
    st.session_state[WORKING_KEY] = plan
    row_prefixes = tuple(EDITOR_PREFIX + v for v in ("name_", "allocation_", "budget_", "error_", "accept", "contingency"))
    for key in tuple(st.session_state):
        if key.startswith(EDITOR_PREFIX if reset_review else row_prefixes):
            st.session_state.pop(key, None)


def _set_working(plan):
    st.session_state[WORKING_KEY] = replace(plan, accepted=False)
    st.session_state[EDITOR_PREFIX + "accept"] = False


def _capture_row(key):
    working = st.session_state[WORKING_KEY]
    updated = []
    for row in working.expenses:
        if row.key == key:
            name = st.session_state.get(EDITOR_PREFIX + "name_" + key, row.display_name)
            amount = st.session_state.get(EDITOR_PREFIX + "allocation_" + key, row.allocated_inr)
            row = replace(row, name=name if name != row.display_name else row.name, allocated_inr=amount,
                          **({"minimum_inr": amount, "maximum_inr": amount} if row.category == "custom" else {}))
        updated.append(row)
    _set_working(replace(working, expenses=tuple(updated)))


def _capture_budget(ceremony_id):
    working = st.session_state[WORKING_KEY]
    _set_working(replace(working, ceremony_budgets={**working.ceremony_budgets,
        ceremony_id: st.session_state[EDITOR_PREFIX + "budget_" + ceremony_id]}))


def _capture_contingency():
    _set_working(replace(st.session_state[WORKING_KEY], contingency_inr=st.session_state[EDITOR_PREFIX + "contingency"]))


def _add_row(ceremony_id):
    _set_working(add_expense(st.session_state[WORKING_KEY], ceremony_id))


def _remove_row(key):
    _capture_row(key)
    working = st.session_state[WORKING_KEY]
    working = replace(working, expenses=tuple(
        replace(e, name="Removed expense") if e.key == key and not e.display_name.strip() else e
        for e in working.expenses
    ))
    _set_working(remove_expense(working, key))


def _undo_row(key):
    _set_working(restore_expense(st.session_state[WORKING_KEY], key))


def _scope_signature(plan, ceremony_id):
    return (
        tuple(e for e in plan.expenses if e.ceremony_id == ceremony_id),
        tuple(e for e in plan.removed_expenses if e.ceremony_id == ceremony_id),
        plan.ceremony_budgets.get(ceremony_id) if ceremony_id is not None else plan.contingency_inr,
    )


def _pending_scopes(plan, working):
    return [scope for scope in [*(n.ceremony_id for n in plan.needs), None]
            if _scope_signature(plan, scope) != _scope_signature(working, scope)]


def _recalculate(ceremony_id):
    scope = ceremony_id or "shared"
    try:
        for row in tuple(st.session_state[WORKING_KEY].expenses):
            if row.ceremony_id == ceremony_id:
                _capture_row(row.key)
        if ceremony_id is None and EDITOR_PREFIX + "contingency" in st.session_state:
            _capture_contingency()
        elif ceremony_id is not None and EDITOR_PREFIX + "budget_" + ceremony_id in st.session_state:
            _capture_budget(ceremony_id)
        updated = recalculate_scope(st.session_state[BUDGET_DRAFT_KEY], st.session_state[WORKING_KEY], ceremony_id)
    except ValueError as error:
        st.session_state[EDITOR_PREFIX + "error_" + scope] = str(error)
    else:
        st.session_state[BUDGET_DRAFT_KEY] = updated
        st.session_state.pop(EDITOR_PREFIX + "error_" + scope, None)


def _review_needs(plan, profile, ceremonies):
    names = {c.ceremony_id: c.name for c in ceremonies}
    with st.expander("Review catering, services and assumptions", expanded=not all(n.reviewed for n in plan.needs)):
        st.caption("Review unconfirmed suggestions here. Applying choices refreshes ranges while preserving edited rows, custom expenses, removed items and ceremony allocations.")
        with st.form("wedding_budget_needs_form"):
            needs = []
            for n in plan.needs:
                st.markdown(f"**{names[n.ceremony_id]}**")
                st.caption(f"Originally suggested by {n.source}. {n.explanation}")
                prefix = f"{EDITOR_PREFIX}{n.ceremony_id}_"
                guests = st.number_input("Guests for this estimate", value=n.guest_count, min_value=0,
                                         max_value=MAX_INR, step=1, key=prefix + "guests")
                meal = st.selectbox("Catering", MEALS, index=MEALS.index(n.catering), key=prefix + "meal")
                venues = [v.value for v in VenuePreference]
                venue = st.selectbox("Venue for this estimate", venues, index=venues.index(n.venue),
                                     format_func=_label, key=prefix + "venue")
                services = st.multiselect("Services to include", SERVICES, default=list(n.services),
                                         format_func=_label, key=prefix + "services")
                needs.append(replace(n, guest_count=guests, catering=meal, venue=venue, services=tuple(services)))
            st.caption("Review venue package inclusions. Shared photography is one package across included ceremonies.")
            percent = st.number_input("Contingency (% of total wedding budget)", min_value=0, max_value=100,
                                      value=plan.assumptions.contingency_percent, key=EDITOR_PREFIX + "percent")
            reviewed = st.checkbox("I have reviewed these catering and service choices", value=all(n.reviewed for n in plan.needs), key=EDITOR_PREFIX + "reviewed")
            submitted = st.form_submit_button("Apply reviewed needs and recalculate", key="wedding_budget_apply_needs")
        if submitted:
            try:
                updated = revise_budget_needs(st.session_state[WORKING_KEY], profile, ceremonies,
                    [replace(n, reviewed=reviewed) for n in needs],
                    replace(plan.assumptions, contingency_percent=percent))
            except ValueError:
                st.error("Check expense names and amounts before applying these choices.")
            else:
                _new_draft(updated, reset_review=False)
                st.rerun()


def _render_summary(plan, profile, ceremonies):
    assessment = assess_budget(plan, profile, ceremonies)
    with st.container(horizontal=True):
        st.metric("Total wedding budget", format_inr(profile.budget_inr))
        st.metric("Allocated budget", format_inr(sum(assessment.ceremony_allocations.values())))
        st.metric("Shared expenses", format_inr(assessment.shared_inr))
        st.metric("Contingency", format_inr(assessment.contingency_inr))
        st.metric("Remaining" if assessment.remaining_inr >= 0 else "Shortfall", format_inr(abs(assessment.remaining_inr)))
    st.caption(f"Total committed: {format_inr(assessment.total_inr)}. Remaining uses the higher of each ceremony's allocation or calculated expenses, plus shared costs and contingency. Only recalculated data appears here.")
    st.caption(f"Illustrative total range: {format_inr(assessment.minimum_inr)} to {format_inr(assessment.maximum_inr)}.")
    if assessment.maximum_inr > profile.budget_inr:
        st.warning("The upper estimate exceeds your ceiling. Review service scope and costs before accepting.")
    for issue in assessment.issues:
        st.warning(issue)
    for warning in assessment.warnings:
        st.warning(warning)
    return assessment


def _expense_rows(ceremony_id):
    working = st.session_state[WORKING_KEY]
    rows = [e for e in working.expenses if e.ceremony_id == ceremony_id]
    if not rows:
        st.info("No expense rows. Add an expense or undo a removal below.")
    with st.container(height=420 if len(rows) > 3 else "content"):
        for row in rows:
            name_col, amount_col, action_col = st.columns([3, 2, 1], vertical_alignment="bottom")
            name_col.text_input("Expense name", value=row.display_name, max_chars=120,
                key=EDITOR_PREFIX + "name_" + row.key, on_change=_capture_row, args=(row.key,))
            amount_col.number_input("Amount (INR)", value=row.allocated_inr, min_value=0, max_value=MAX_INR,
                step=100, key=EDITOR_PREFIX + "allocation_" + row.key, on_change=_capture_row, args=(row.key,))
            action_col.button("Remove", key=EDITOR_PREFIX + "remove_" + row.key,
                              on_click=_remove_row, args=(row.key,))
            st.caption(format_inr(row.allocated_inr) + (f" · Illustrative range {format_inr(row.minimum_inr)}–{format_inr(row.maximum_inr)}"
                if row.category != "custom" else " · User-added amount"))
    scope = ceremony_id or "shared"
    st.button("+ Add Expense", key=EDITOR_PREFIX + "add_" + scope, on_click=_add_row, args=(ceremony_id,))
    for row in working.removed_expenses:
        if row.ceremony_id == ceremony_id:
            st.button("Undo removal: " + row.display_name, key=EDITOR_PREFIX + "undo_" + row.key,
                      on_click=_undo_row, args=(row.key,))


def _ceremony_section(ceremony, plan, assessment):
    cid = ceremony.ceremony_id
    needs = next(n for n in plan.needs if n.ceremony_id == cid)
    with st.expander(ceremony.name, expanded=st.session_state.get("wedding_budget_focus") == cid):
        st.subheader(ceremony.name)
        st.write(f"Guest count: {needs.guest_count if needs.guest_count is not None else 'Not provided'}")
        timing = ceremony.plan.preferred_timing if ceremony.plan else None
        st.write("Preferred timing: " + (_label(timing.value) if timing else "Not provided"))
        st.write("Venue preference: " + _label(needs.venue))
        with st.container(horizontal=True):
            st.metric("Ceremony allocated budget", format_inr(assessment.ceremony_allocations[cid]))
            st.metric("Calculated expense total", format_inr(assessment.ceremony_expenses[cid]))
            remaining = assessment.ceremony_remaining[cid]
            st.metric("Ceremony remaining" if remaining >= 0 else "Ceremony shortfall", format_inr(abs(remaining)))
        constraint = ceremony.plan.estimated_budget_inr if ceremony.plan else None
        if constraint is not None:
            st.caption(f"Your entered ceremony budget {format_inr(constraint)} is preserved as a fixed constraint. Fractional amounts reserve the next whole rupee. Change it in Ceremony Plans if needed.")
        else:
            working = st.session_state[WORKING_KEY]
            st.number_input("Ceremony allocation (INR)", value=working.ceremony_budgets[cid],
                min_value=0, max_value=MAX_INR, step=100, key=EDITOR_PREFIX + "budget_" + cid,
                on_change=_capture_budget, args=(cid,), help="Explicitly change this allocation when needed; editing expenses never changes it automatically.")
        _expense_rows(cid)
        _recalculate_control(cid, plan)


def _recalculate_control(ceremony_id, plan):
    scope = ceremony_id or "shared"
    if _scope_signature(plan, ceremony_id) != _scope_signature(st.session_state[WORKING_KEY], ceremony_id):
        st.info("Unrecalculated edits. Recalculate this section before saving the wedding budget.")
    error = st.session_state.get(EDITOR_PREFIX + "error_" + scope)
    if error:
        st.error("Check expense names and non-negative whole INR amounts. " + error)
    st.button("Recalculate Budget", key="wedding_budget_recalculate_" + scope,
              on_click=_recalculate, args=(ceremony_id,))


def render_smart_wedding_budget():
    st.title("Smart Wedding Budget")
    if not st.session_state.get(ACTIVE_PROJECT_ID_KEY):
        st.info("Open a wedding project to generate its budget.")
        return
    if not render_wedding_at_a_glance():
        return
    summary = st.container()
    st.caption("Illustrative INR estimates, not vendor quotations. Edit rows, recalculate each changed section, then explicitly save the wedding budget.")
    if st.session_state.get(PENDING_SAVE_KEY) or st.session_state.get(PENDING_PLAN_SAVES_KEY):
        st.warning("Retry pending wedding details or ceremony plan saves before generating a budget.")
        return
    profile = st.session_state.get(PROFILE_SAVED_KEY)
    try:
        ceremonies = saved_project_ceremonies(st.session_state)
    except ValueError:
        st.error("Saved ceremonies are unavailable. Reopen this project.")
        return
    if profile is None or profile.budget_inr is None or not ceremonies:
        st.info("Save a total wedding budget and confirm at least one ceremony first. Ceremony budgets are optional.")
        return
    if st.session_state.get(BUDGET_ERROR_KEY):
        st.error(st.session_state[BUDGET_ERROR_KEY])
    draft = st.session_state.get(BUDGET_DRAFT_KEY)
    use_ai = st.checkbox("Use Groq to interpret preferences and service trade-offs", value=True, key="wedding_budget_use_ai")
    replace_draft = not draft or st.checkbox("Replace the current budget draft with new suggestions", key="wedding_budget_replace_draft")
    if draft:
        st.caption("Deliberate regeneration replaces draft row edits, additions and removals. The saved budget stays intact until you save again.")
    generate = st.button("Generate Suggested Budget", key="wedding_budget_generate", type="primary", disabled=not replace_draft)
    retry = st.button("Retry AI interpretation", key="wedding_budget_retry_ai", disabled=not use_ai or not replace_draft)
    if generate or retry:
        message = None
        with st.spinner("Interpreting ceremony needs..."):
            if use_ai:
                needs, message = cached_budget_needs(st.session_state, profile, ceremonies, retry=retry)
            else:
                needs = infer_ceremony_needs(profile, ceremonies)
            draft = generate_budget(profile, ceremonies, needs)
        _new_draft(draft)
        st.session_state["wedding_budget_ai_notice"] = message
    if st.session_state.get("wedding_budget_ai_notice"):
        st.info(st.session_state["wedding_budget_ai_notice"])
    if draft is None:
        with summary:
            st.metric("Total wedding budget", format_inr(profile.budget_inr))
            st.caption("Generate or restore a budget to see allocations and remaining funds.")
        return
    if draft.context_fingerprint != context_fingerprint(profile, ceremonies):
        st.warning("Wedding details or confirmed ceremonies changed. Your previous budget is retained; generate a new draft for the current context.")
        return
    st.session_state.setdefault(WORKING_KEY, draft)
    _review_needs(draft, profile, ceremonies)
    with summary:
        assessment = _render_summary(draft, profile, ceremonies)
    st.subheader("Ceremony expenses")
    for ceremony in ceremonies:
        _ceremony_section(ceremony, draft, assessment)
    with st.container(border=True):
        st.subheader("Shared expenses and contingency")
        st.caption("Shared costs appear once here and are excluded from ceremony expense totals.")
        _expense_rows(None)
        working = st.session_state[WORKING_KEY]
        st.number_input("Contingency allocation (INR)", value=working.contingency_inr,
            min_value=0, max_value=MAX_INR, step=100, key=EDITOR_PREFIX + "contingency",
            on_change=_capture_contingency)
        st.caption(f"Configured contingency: {draft.assumptions.contingency_percent}% of the wedding ceiling. The recalculated amount is counted once: {format_inr(draft.contingency_inr)}.")
        _recalculate_control(None, draft)
    with st.expander("How this budget was calculated"):
        st.write("Illustrative guest-based meal/staff/seating rates, one venue rental and fixed services per ceremony. Shared photography covers all included ceremonies once. Destination logistics assume the largest guest count for one guest-night. Review coverage, package inclusions, travel nights and other costs.")
        st.write("Ceremony allocations remain fixed during row edits. Unused allocation remains reserved. A shortfall never silently changes a constraint or reduces costs. Custom rows use your entered amounts; removing catering or rental may leave an incomplete estimate.")
        st.json(asdict(draft.assumptions))
    pending = _pending_scopes(draft, st.session_state[WORKING_KEY])
    saved = st.session_state.get(BUDGET_SAVED_KEY)
    st.caption("Budget status: " + ("Saved and verified" if draft == saved and not pending else "Unsaved draft"))
    if pending:
        st.info("Recalculate each changed ceremony or shared section before saving.")
    accepted = st.checkbox("I accept these reviewed allocations and assumptions", key=EDITOR_PREFIX + "accept")
    if st.button("Save Wedding Budget", key="wedding_budget_save",
                 disabled=not accepted or bool(pending) or not all(n.reviewed for n in draft.needs) or not assessment.feasible):
        try:
            persist_budget_plan(st.session_state, replace(draft, accepted=True))
        except Exception:
            st.error("The budget could not be saved or verified. Your edits remain in this session; retry saving. If wedding details changed elsewhere, reopen the project and regenerate.")
        else:
            st.session_state[WORKING_KEY] = st.session_state[BUDGET_DRAFT_KEY]
            st.success("Your accepted budget was saved and read back from this wedding project.")
