"""Central wedding workspace and read-only analytics over accepted saved plans."""

import streamlit as st

from ai_event_planning_platform.budget_engine import format_inr
from ai_event_planning_platform.dashboard_data import budget_dashboard_data, planning_alerts, wedding_display_title, recommended_next_step
from ai_event_planning_platform.wedding_calendar import saved_calendar_data
from ai_event_planning_platform.wedding_profile import WeddingProfile
from ai_event_planning_platform.wedding_project_state import (
    ACTIVE_PROJECT_ID_KEY, ACTIVE_PROJECT_NAME_KEY, PROFILE_SAVED_KEY, BUDGET_SAVED_KEY,
    BUDGET_ERROR_KEY, TIMELINE_ERROR_KEY, TIMELINE_SAVED_KEY, PENDING_NAMES_KEY,
    persist_couple_names, reload_couple_names, saved_project_ceremonies,
)
from ai_event_planning_platform.wedding_summary import wedding_glance_fields

DASHBOARD_PAGE = "wedding_dashboard"
BUDGET_DASHBOARD_PAGE = "budget_dashboard"
INVITATIONS_PAGE = "wedding_invitations"


def open_editor(route, ceremony_id=None, view=None):
    if ceremony_id:
        if route == "smart_wedding_budget":
            st.session_state["wedding_budget_focus"] = ceremony_id
        elif route == "ceremony_planning":
            st.session_state["wedding_ceremony_focus"] = ceremony_id
        elif route == "wedding_timeline":
            st.session_state["wedding_timeline_view"] = "Ceremony execution"
            st.session_state["wedding_timeline_focus"] = ceremony_id
    elif route == "wedding_timeline" and view:
        st.session_state["wedding_timeline_view"] = view
    st.session_state["page"] = route


def _context():
    profile = st.session_state.get(PROFILE_SAVED_KEY)
    if not st.session_state.get(ACTIVE_PROJECT_ID_KEY) or not isinstance(profile, WeddingProfile):
        st.info("Open a wedding project to see its dashboard.")
        return None
    try:
        ceremonies = saved_project_ceremonies(st.session_state)
    except ValueError:
        st.error("Saved ceremony details are unavailable. Reopen the project.")
        return None
    for key in (BUDGET_ERROR_KEY, TIMELINE_ERROR_KEY):
        if st.session_state.get(key):
            st.warning(st.session_state[key])
    return profile, ceremonies, st.session_state.get(BUDGET_SAVED_KEY), st.session_state.get(TIMELINE_SAVED_KEY)


def _edit_names():
    st.session_state["wedding_names_editing"] = True
    st.session_state.pop("wedding_names_success", None)


def _names_editor(profile):
    missing = not (profile.bride_name and profile.groom_name)
    show = st.session_state.get("wedding_names_editing", missing and not st.session_state.get("wedding_names_skipped", False))
    if st.session_state.get("wedding_names_success"):
        st.success("Couple names saved and read back from this wedding project.")
    if not show:
        st.button("Edit couple names" if not missing else "Personalize Your Wedding", key="wedding_names_edit",
                  on_click=_edit_names)
        return
    with st.container(border=True):
        st.subheader("Personalize Your Wedding")
        st.caption("Both names are optional. Your stored project name stays separate.")
        initial = st.session_state.get(PENDING_NAMES_KEY, st.session_state.get("wedding_names_draft", (profile.bride_name or "", profile.groom_name or "")))
        st.session_state.setdefault("wedding_names_bride", initial[0] or "")
        st.session_state.setdefault("wedding_names_groom", initial[1] or "")
        with st.form("wedding_names_form"):
            bride = st.text_input("Bride's name", max_chars=120, key="wedding_names_bride")
            groom = st.text_input("Groom's name", max_chars=120, key="wedding_names_groom")
            submitted = st.form_submit_button("Save couple names", key="wedding_names_save")
        if submitted:
            st.session_state.pop("wedding_names_success", None)
            st.session_state["wedding_names_draft"] = (bride, groom)
            try:
                persist_couple_names(st.session_state, bride, groom)
            except Exception:
                st.error("Names could not be saved and verified. Your entries are retained. Save other pending changes first, or reopen and review current names if another session changed them.")
            else:
                st.session_state["wedding_names_editing"] = False
                st.session_state["wedding_names_success"] = True
                st.rerun()
        if st.session_state.get(PENDING_NAMES_KEY):
            if st.button("Load latest names for comparison", key="wedding_names_reload"):
                try:
                    reload_couple_names(st.session_state)
                except Exception:
                    st.error("The latest saved names could not be loaded. Your entries remain available.")
                else:
                    st.session_state["wedding_names_compare"] = True
                    st.rerun()
            if st.session_state.get("wedding_names_compare"):
                st.info(f"Current saved names: {profile.bride_name or 'Not provided'} / {profile.groom_name or 'Not provided'}. Your entered names are retained above. Review them before saving a replacement.")
        st.button("Skip for now" if missing else "Close name editor", key="wedding_names_skip",
                  disabled=bool(st.session_state.get(PENDING_NAMES_KEY)),
                  on_click=lambda: st.session_state.update({"wedding_names_editing": False, "wedding_names_skipped": True}))


def render_planning_status(profile, ceremonies, budget, timeline, *, prefix="wedding_status", calendar_only=False, expanded=True):
    alerts = planning_alerts(profile, ceremonies, budget, timeline, include_budget=not calendar_only)
    if calendar_only:
        alerts = tuple(a for a in alerts if a.route != "step_by_step")
    with st.expander("Planning Status" if calendar_only else "Planning alerts", expanded=expanded):
        st.caption("Based on saved details. Missing fields are planning information to review; suggestions remain unconfirmed.")
        for index, a in enumerate(alerts):
            st.write(f"{a.kind}: {a.message}")
            label = "Set Ceremony Date" if "date is not decided" in a.message else "Edit Ceremony Details" if a.route == "ceremony_planning" else "Update Timeline" if a.route == "wedding_timeline" else "Review details"
            st.button(label, key=f"{prefix}_{index}", on_click=open_editor,
                      args=(a.route, a.ceremony_id, "Preparation timeline" if a.route == "wedding_timeline" and not a.ceremony_id else None))
        if not alerts:
            st.caption("No missing details or scheduling issues found in the available saved information.")


def _open_next_step(step):
    if step.route == DASHBOARD_PAGE:
        st.session_state["wedding_dashboard_summary_open"] = True
    open_editor(step.route, step.ceremony_id, step.view)


def render_wedding_dashboard():
    context = _context()
    if context is None:
        return
    profile, ceremonies, budget, timeline = context
    names = [name.strip() for name in (profile.bride_name, profile.groom_name) if name and name.strip()]
    st.title(names[0] + "'s Wedding" if len(names) == 1 else wedding_display_title(profile, st.session_state.get(ACTIVE_PROJECT_NAME_KEY)))
    fields = dict(wedding_glance_fields(st.session_state.get(ACTIVE_PROJECT_NAME_KEY), profile, ceremonies))
    st.caption(fields["Wedding date"] + " · " + fields["City and state"])
    with st.container(horizontal=True):
        for label, route, key in (("Wedding Details", "step_by_step", "details"),
                ("Budget Dashboard", BUDGET_DASHBOARD_PAGE, "budget"),
                ("Calendar & Timeline", "wedding_timeline", "calendar"),
                ("Invitations", INVITATIONS_PAGE, "invitations")):
            st.button(label, key="wedding_dashboard_" + key,
                      width="stretch", on_click=open_editor, args=(route,))
    step = recommended_next_step(profile, ceremonies, budget, timeline)
    with st.container(border=True):
        st.subheader("Your Next Step")
        st.button(step.label, key="wedding_dashboard_next_step", type="primary", on_click=_open_next_step, args=(step,))

    with st.expander("Wedding summary", expanded=bool(st.session_state.get("wedding_dashboard_summary_open"))):
        schedules, tasks = saved_calendar_data(profile, ceremonies, timeline, budget)
        with st.container(horizontal=True):
            for label, value in (("Guest count", fields["Total wedding guests"]),
                    ("Overall wedding budget", fields["Total wedding budget"]), ("Selected ceremonies", len(ceremonies))):
                st.metric(label, value, border=True)
        st.caption(f"{sum(s.ceremony_date is not None for s in schedules)} of {len(schedules)} ceremonies dated · "
                   + (f"{sum(t.status != 'completed' for t in tasks)} preparation tasks pending" if timeline else "Timeline not saved"))
        data = budget_dashboard_data(budget, profile, ceremonies)
        if data:
            a = data["assessment"]
            with st.container(horizontal=True):
                for label, amount in (("Allocated budget", sum(a.ceremony_allocations.values())), ("Shared expenses", a.shared_inr),
                        ("Contingency", a.contingency_inr), ("Remaining budget" if profile.budget_inr is None or a.remaining_inr >= 0 else "Budget shortfall", abs(a.remaining_inr) if profile.budget_inr is not None else None)):
                    st.metric(label, format_inr(amount) if amount is not None else "Not provided", border=True)
            if data["stale"]:
                st.warning("Planning details changed since budget acceptance. Review the saved plan in Budget Dashboard.")
        _names_editor(profile)
        st.button("Describe your dream wedding", key="wedding_dashboard_describe", on_click=open_editor, args=("describe_wedding",))
    render_planning_status(profile, ceremonies, budget, timeline, expanded=False)


def render_budget_dashboard():
    st.title("Budget Dashboard")
    context = _context()
    if context is None:
        return
    profile, ceremonies, budget, _timeline = context
    st.button("Edit Budget", key="wedding_dashboard_edit_budget", on_click=open_editor, args=("smart_wedding_budget",))
    data = budget_dashboard_data(budget, profile, ceremonies)
    if data is None:
        st.info("No accepted budget has been saved. Open Edit Budget to generate, review and save estimates.")
        st.metric("Total wedding budget", format_inr(profile.budget_inr) if profile.budget_inr is not None else "Not provided", border=True)
        return
    a = data["assessment"]
    cards = (("Total wedding budget", profile.budget_inr), ("Total ceremony allocations", sum(a.ceremony_allocations.values())),
        ("Calculated expenses", sum(a.ceremony_expenses.values())), ("Shared expenses", a.shared_inr),
        ("Contingency", a.contingency_inr), ("Total committed", a.total_inr),
        ("Remaining" if profile.budget_inr is None or a.remaining_inr >= 0 else "Shortfall", abs(a.remaining_inr) if profile.budget_inr is not None else None))
    with st.container(horizontal=True):
        for label, amount in cards:
            st.metric(label, format_inr(amount) if amount is not None else "Not provided", border=True)
    st.caption("Illustrative estimates, not vendor quotations. Calculated expenses above include ceremony rows only; shared rows appear once separately. Total committed uses the higher of each ceremony's allocation or expenses, plus shared expenses and contingency. Unused allocations remain reserved.")
    if data["stale"]:
        st.warning("Planning details changed since acceptance. These are the saved expense rows and allocations, compared with the current saved wedding ceiling. Review and regenerate in Edit Budget when ready.")
    for message in data["messages"]:
        st.warning(message)
    if profile.budget_inr is not None and a.maximum_inr > profile.budget_inr:
        st.warning("The upper estimate exceeds your overall wedding ceiling.")
    st.subheader("Ceremony-wise budget")
    st.dataframe(list(data["ceremonies"]), hide_index=True, alt="Saved ceremony allocations, expenses and remaining reservations")
    with st.container(horizontal=True):
        for row in data["ceremonies"]:
            st.button("Edit " + row["Ceremony"], key="wedding_dashboard_edit_" + row["Ceremony ID"],
                disabled=row["Ceremony ID"] not in {c.ceremony_id for c in ceremonies},
                on_click=open_editor, args=("smart_wedding_budget", row["Ceremony ID"]))
    st.subheader("Expense category breakdown")
    st.caption("Categories aggregate actual saved rows. Original expense labels and categories remain in the detail records. Combined travel/accommodation is kept together because the saved row does not split it.")
    categories = data["categories"]
    if categories:
        st.bar_chart({"Category": list(categories), "Estimated amount (INR)": list(categories.values())},
            x="Category", y="Estimated amount (INR)", horizontal=True, alt="Accepted expense totals by category including shared rows once")
        st.dataframe([{"Category": key, "Estimated amount (INR)": amount} for key, amount in categories.items()],
            hide_index=True, alt="Category totals reconciled to accepted expense records")
        selected = st.selectbox("Inspect category", tuple(categories), key="wedding_dashboard_category")
        contributions = [{"Ceremony ID": cid, "Contribution": data["names"].get(cid, cid) if cid else "Shared expenses", "Estimated amount (INR)": amount}
            for (category, cid), amount in data["contributions"].items() if category == selected]
        st.dataframe(contributions, hide_index=True, alt="Ceremony and shared contributions to the selected category")
        st.dataframe([r for r in data["records"] if r["Category"] == selected], hide_index=True,
                     alt="Original accepted expense records behind the selected category total")
    else:
        st.info("The accepted plan has no active expense records.")
    row_total = sum(categories.values())
    reserves = sum(a.ceremony_reserves.values())
    st.caption(f"Reconciliation: category expense rows {format_inr(row_total)} + unused ceremony reservations {format_inr(reserves)} + contingency {format_inr(a.contingency_inr)} = committed {format_inr(a.total_inr)}. Category totals exclude reservations and contingency.")


