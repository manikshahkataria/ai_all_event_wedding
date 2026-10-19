"""Editable plans for the active project's verified confirmed ceremonies."""

from dataclasses import replace
from datetime import date
from decimal import Decimal

import streamlit as st

from ai_event_planning_platform.ceremony_plan import (
    CeremonyDateCertainty, CeremonyPlan, PreferredTiming, VenuePreference,
    ceremony_plan_suggestions, ceremony_plan_warnings, missing_plan_details,
    validate_ceremony_plan,
)
from ai_event_planning_platform.wedding_ceremonies import Ceremony
from ai_event_planning_platform.wedding_form import DRAFT_STATE_KEY, _format_indian_currency
from ai_event_planning_platform.wedding_profile import migrate_wedding_profile
from ai_event_planning_platform.wedding_project_state import (
    ACTIVE_PROJECT_ID_KEY, PENDING_SAVE_KEY, navigate_project_to_page,
    persist_ceremony_plan, saved_project_ceremonies,
)


CEREMONY_PLANNING_PAGE = "ceremony_planning"
DATE_LABELS = {item.value.title(): item for item in CeremonyDateCertainty}
TIMING_LABELS = {"Not specified": None, **{item.value.title(): item for item in PreferredTiming}}
VENUE_LABELS = {item.value.replace("_", " ").title(): item for item in VenuePreference}


def _navigate(page: str) -> None:
    navigate_project_to_page(st.session_state, page)


def _label(options: dict, value: object) -> str:
    return next(label for label, candidate in options.items() if candidate == value)


def _budget_label(amount: int | float) -> str:
    whole = int(amount)
    if amount == whole:
        return _format_indian_currency(whole)
    fraction = format(Decimal(str(amount)), "f").partition(".")[2].rstrip("0")
    return f"{_format_indian_currency(whole)}.{fraction}"


def _plan_status(plan: CeremonyPlan | None) -> str:
    if plan is None:
        return "Not started · Incomplete"
    return "Saved · Incomplete" if missing_plan_details(plan) else "Saved · Details provided"


def render_ceremony_plan_overview() -> None:
    st.subheader("Your Ceremony Plans")
    try:
        ceremonies = saved_project_ceremonies(st.session_state)
    except ValueError:
        st.error("Saved ceremony plans could not be loaded. Reopen this wedding project.")
        ceremonies = None
    if ceremonies == []:
        st.write("Confirm wedding ceremonies to start planning each one.")
    for ceremony in ceremonies or []:
        with st.container(border=True):
            st.markdown(f"**{ceremony.name}**")
            plan = ceremony.plan
            if plan is not None:
                if plan.guest_count is not None:
                    st.write(f"Guests: {plan.guest_count}")
                if plan.ceremony_date is not None:
                    st.write(f"{plan.date_certainty.value.title()} date: {plan.ceremony_date.strftime('%d %B %Y')}")
                else:
                    st.write(f"Date: {plan.date_certainty.value.title()} · Not provided")
                st.write(f"Preferred timing: {_label(TIMING_LABELS, plan.preferred_timing)}")
                st.write(f"Venue preference: {_label(VENUE_LABELS, plan.venue_preference)}")
                if plan.venue_notes:
                    st.caption(plan.venue_notes)
                if plan.estimated_budget_inr is not None:
                    st.write(f"Estimated budget: {_budget_label(plan.estimated_budget_inr)}")
            st.caption(f"Planning status: {_plan_status(plan)}")
    st.button(
        "Edit Ceremony Plans", key="edit_ceremony_plans",
        on_click=_navigate, args=(CEREMONY_PLANNING_PAGE,),
        disabled=not st.session_state.get(ACTIVE_PROJECT_ID_KEY) or not ceremonies,
    )


def _apply_suggestions(draft_key: str, prefix: str) -> None:
    draft = st.session_state[draft_key]
    profile = migrate_wedding_profile(st.session_state.get(DRAFT_STATE_KEY))
    changes = ceremony_plan_suggestions(draft, profile)
    updated = replace(draft, **changes)
    st.session_state[draft_key] = updated
    for field, value in changes.items():
        st.session_state[f"{prefix}{field}"] = (
            _label(DATE_LABELS, value) if field == "date_certainty" else value
        )


def _render_plan_editor(ceremony: Ceremony, project_id: str) -> None:
    prefix = f"wedding_plan_{project_id}_{ceremony.ceremony_id}_"
    draft_key = f"{prefix}draft"
    saved = ceremony.plan
    initial = saved or CeremonyPlan(ceremony.ceremony_id, ceremony.name)
    draft = st.session_state.setdefault(draft_key, initial)
    # Names are read-only and follow explicit edits to the parent custom ceremony.
    draft = replace(draft, ceremony_name=ceremony.name)
    defaults = {
        "guest_count": draft.guest_count,
        "ceremony_date": draft.ceremony_date,
        "date_certainty": _label(DATE_LABELS, draft.date_certainty),
        "preferred_timing": _label(TIMING_LABELS, draft.preferred_timing),
        "venue_preference": _label(VENUE_LABELS, draft.venue_preference),
        "venue_notes": draft.venue_notes or "",
        "estimated_budget_inr": float(draft.estimated_budget_inr) if draft.estimated_budget_inr is not None else None,
        "additional_preferences": draft.additional_preferences or "",
    }
    for field, value in defaults.items():
        st.session_state.setdefault(f"{prefix}{field}", value)
    with st.expander(ceremony.name, expanded=st.session_state.get("wedding_ceremony_focus") in (None, ceremony.ceremony_id)):
        st.caption("All planning details are optional. Save this ceremony independently.")
        guests = st.number_input(
            "Guest count", key=f"{prefix}guest_count", min_value=0, max_value=(1 << 53) - 1,
            value=None, step=1, placeholder="Not provided",
        )
        certainty = DATE_LABELS[st.selectbox("Date certainty", list(DATE_LABELS), key=f"{prefix}date_certainty")]
        ceremony_date = st.date_input(
            "Ceremony date (optional)", key=f"{prefix}ceremony_date",
            value=None, min_value=date.min, max_value=date.max, format="DD/MM/YYYY",
        )
        timing = TIMING_LABELS[st.selectbox("Preferred timing", list(TIMING_LABELS), key=f"{prefix}preferred_timing")]
        venue = VENUE_LABELS[st.selectbox("Venue preference", list(VENUE_LABELS), key=f"{prefix}venue_preference")]
        notes = st.text_input("Venue name or notes", key=f"{prefix}venue_notes")
        budget = st.number_input(
            "Estimated ceremony budget (INR)", key=f"{prefix}estimated_budget_inr",
            min_value=0.0, max_value=float((1 << 53) - 1), step=1000.0,
            value=None, placeholder="Not provided",
        )
        preferences = st.text_area("Additional preferences", key=f"{prefix}additional_preferences")
        draft = CeremonyPlan(
            ceremony_id=ceremony.ceremony_id, ceremony_name=ceremony.name,
            guest_count=guests, ceremony_date=ceremony_date, date_certainty=certainty,
            preferred_timing=timing, venue_preference=venue, venue_notes=notes or None,
            estimated_budget_inr=budget, additional_preferences=preferences or None,
        )
        st.session_state[draft_key] = draft
        profile = migrate_wedding_profile(st.session_state.get(DRAFT_STATE_KEY))
        for warning in ceremony_plan_warnings(draft, profile):
            st.warning(warning)
        if st.button("Save Ceremony Plan", key=f"{prefix}save", type="primary"):
            errors = validate_ceremony_plan(draft)
            if errors:
                for error in errors:
                    st.error(error)
            else:
                try:
                    persist_ceremony_plan(st.session_state, draft)
                except Exception:
                    st.error(
                        "This ceremony plan could not be saved or verified. Your edits remain "
                        "in this session. Retry Save Ceremony Plan."
                    )
                else:
                    saved = draft
                    st.success("This ceremony plan was saved to your wedding project.")
        if saved is None and draft == initial:
            status = "Not started"
        else:
            status = "Saved" if draft == saved else "Unsaved changes"
        missing = missing_plan_details(draft)
        st.caption(f"Planning status: {status} · {'Incomplete' if missing else 'Details provided'}")
        if missing:
            st.caption("Optional details not provided: " + ", ".join(missing))
        suggestions = ceremony_plan_suggestions(draft, profile)
        dismissed_key = f"{prefix}suggestions_dismissed"
        if suggestions and not st.session_state.get(dismissed_key, False):
            st.markdown("**Optional suggestions — not saved or confirmed**")
            if "guest_count" in suggestions:
                st.caption(f"Consider {suggestions['guest_count']} guests if the full wedding guest list attends. Adjust for this ceremony.")
            if "ceremony_date" in suggestions:
                st.caption(f"Consider {suggestions['ceremony_date'].strftime('%d %B %Y')} as a tentative date, based on the fixed wedding date.")
            st.button(
                "Use suggestions as draft", key=f"{prefix}apply_suggestions",
                on_click=_apply_suggestions, args=(draft_key, prefix),
            )
            st.button(
                "Dismiss suggestions", key=f"{prefix}dismiss_suggestions",
                on_click=lambda: st.session_state.__setitem__(dismissed_key, True),
            )


def render_ceremony_planning() -> None:
    st.title("Plan Your Wedding Ceremonies")
    project_id = st.session_state.get(ACTIVE_PROJECT_ID_KEY)
    if not isinstance(project_id, str):
        st.info("Open a wedding project to plan its confirmed ceremonies.")
        return
    if "selected_ceremonies" in st.session_state.get(PENDING_SAVE_KEY, {}):
        st.warning("Save your pending ceremony selections before editing ceremony plans.")
        st.button("Return to ceremony selections", key="plans_pending_selections", on_click=_navigate, args=("wedding_ceremonies",))
        return
    try:
        ceremonies = saved_project_ceremonies(st.session_state)
    except ValueError:
        st.error("Saved ceremonies could not be loaded. Reopen this wedding project.")
        return
    if not ceremonies:
        st.info("No ceremonies selected yet. Confirm ceremonies before planning them.")
        st.button("Choose ceremonies", key="plans_choose_ceremonies", on_click=_navigate, args=("wedding_ceremonies",))
    for ceremony in ceremonies:
        _render_plan_editor(ceremony, project_id)
