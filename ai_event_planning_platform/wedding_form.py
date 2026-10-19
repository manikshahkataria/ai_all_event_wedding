from datetime import date, datetime

import streamlit as st

from ai_event_planning_platform.wedding_profile import (
    CulturalBackground,
    TraditionPreference,
    WeddingDateStatus,
    WeddingLocationPreference,
    WeddingProfile,
    WeddingStyle,
    migrate_wedding_profile,
    validate_wedding_profile,
)


DRAFT_STATE_KEY = "wedding_profile_draft"
DRAFT_SAVED_STATE_KEY = "wedding_profile_saved"
DATE_STATUS_LABELS = {
    "Fixed Date": WeddingDateStatus.FIXED_DATE,
    "Approximate Date": WeddingDateStatus.APPROXIMATE_DATE,
    "Not Decided Yet": WeddingDateStatus.NOT_DECIDED_YET,
}
STYLE_VALUES = {
    "Simple": WeddingStyle.SIMPLE,
    "Traditional": WeddingStyle.TRADITIONAL,
    "Luxury": WeddingStyle.LUXURY,
    "Modern": WeddingStyle.MODERN,
    "Mixed": WeddingStyle.MIXED,
}
LOCATION_VALUES = {
    "Local wedding": WeddingLocationPreference.LOCAL,
    "Destination wedding": WeddingLocationPreference.DESTINATION,
}
TRADITION_VALUES = {
    "Religious": TraditionPreference.RELIGIOUS,
    "Secular": TraditionPreference.SECULAR,
    "Custom": TraditionPreference.CUSTOM,
}


def _date_input_value(value: date | str | None) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            if len(value) == 7:
                return date.fromisoformat(f"{value}-01")
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _initialize_form_state(profile: WeddingProfile, key_prefix: str) -> None:
    state_key = lambda name: f"{key_prefix}{name}"
    legacy_date_status = st.session_state.get(state_key("date_certainty"))
    legacy_date_labels = {
        "Fixed date": "Fixed Date",
        "Approximate month": "Approximate Date",
        "Undecided": "Not Decided Yet",
    }
    date_status_key = state_key("date_status")
    if key_prefix == "wedding_" and legacy_date_status in legacy_date_labels:
        st.session_state[date_status_key] = legacy_date_labels[legacy_date_status]
    elif date_status_key not in st.session_state:
        initial_status = next(
            (
                label
                for label, status in DATE_STATUS_LABELS.items()
                if status is profile.wedding_date_status
            ),
            "Not Decided Yet",
        )
        st.session_state[date_status_key] = initial_status
    if key_prefix == "wedding_":
        st.session_state.pop(state_key("date_certainty"), None)
        for old_key in tuple(st.session_state):
            if old_key.startswith("wedding_") and old_key.endswith("_certainty"):
                st.session_state.pop(old_key, None)

    date_value = _date_input_value(profile.wedding_date)
    st.session_state.setdefault(state_key("date_value"), date_value)
    st.session_state.setdefault(state_key("city_value"), profile.city or "")
    st.session_state.setdefault(state_key("bride_name_value"), profile.bride_name or "")
    st.session_state.setdefault(state_key("groom_name_value"), profile.groom_name or "")
    st.session_state.setdefault(state_key("state_value"), profile.state or "")
    st.session_state.setdefault(
        state_key("location_preference"),
        profile.location_preference.value
        if profile.location_preference is not None
        else "Not selected",
    )
    st.session_state.setdefault(state_key("guest_count_value"), profile.guest_count)
    st.session_state.setdefault(state_key("budget_inr_value"), profile.budget_inr)
    st.session_state.setdefault(
        state_key("style_value"), profile.style.value.title() if profile.style else "Not selected"
    )
    for partner_number, culture in (
        (1, profile.partner_1_culture),
        (2, profile.partner_2_culture),
    ):
        st.session_state.setdefault(
            state_key(f"partner_{partner_number}_background_value"),
            culture.background if culture else "",
        )
        st.session_state.setdefault(
            state_key(f"partner_{partner_number}_region_value"),
            culture.region if culture else "",
        )
    st.session_state.setdefault(
        state_key("traditions_value"),
        [item.value.title() for item in profile.traditions],
    )
    st.session_state.setdefault(
        state_key("additional_preferences_value"), profile.additional_preferences or ""
    )


def _format_indian_currency(amount: int) -> str:
    digits = str(amount)
    if len(digits) <= 3:
        return f"₹{digits}"
    trailing = digits[-3:]
    leading = digits[:-3]
    groups: list[str] = []
    while leading:
        groups.append(leading[-2:])
        leading = leading[:-2]
    return f"₹{','.join(reversed(groups))},{trailing}"


def _format_date_summary(profile: WeddingProfile) -> str:
    if profile.wedding_date_status is WeddingDateStatus.NOT_DECIDED_YET:
        return "Not Decided Yet"
    parsed_date = _date_input_value(profile.wedding_date)
    if parsed_date is None:
        return "Not provided"
    if profile.wedding_date_status is WeddingDateStatus.APPROXIMATE_DATE:
        return f"Approximate Date: {parsed_date.strftime('%B %Y')}"
    return f"Fixed Date: {parsed_date.strftime('%d %B %Y')}"


def _display(value: object, *, empty: str = "Not provided") -> str:
    return value if isinstance(value, str) and value.strip() else empty


def _render_summary(profile: WeddingProfile) -> None:
    st.subheader("Requirements summary")
    st.caption("The form stays editable above. Save again to update this summary.")
    st.text(f"Wedding date: {_format_date_summary(profile)}")
    st.text(f"City: {_display(profile.city)}")
    st.text(f"State: {_display(profile.state)}")
    location = profile.location_preference
    st.text(
        "Local or destination: "
        + (location.value.replace("_", " ").title() if location else "Not provided")
    )
    st.text(
        "Guest count: "
        + (str(profile.guest_count) if profile.guest_count is not None else "Not provided")
    )
    st.text(
        "Budget: "
        + (
            _format_indian_currency(profile.budget_inr)
            if profile.budget_inr is not None
            else "Not provided"
        )
    )
    st.text(
        "Wedding style: "
        + (profile.style.value.title() if profile.style else "Not provided")
    )
    for label, culture in (
        ("Partner 1", profile.partner_1_culture),
        ("Partner 2", profile.partner_2_culture),
    ):
        details = ", ".join(
            value
            for value in (
                culture.background if culture else None,
                culture.region if culture else None,
            )
            if value
        )
        st.text(f"{label} cultural background and region: {details or 'Not provided'}")
    tradition_names = ", ".join(item.value.title() for item in profile.traditions)
    st.text(f"Preferred traditions: {tradition_names or 'Not provided'}")
    st.text(f"Additional preferences: {_display(profile.additional_preferences)}")


def _back_to_planning_options() -> None:
    from ai_event_planning_platform.wedding_project_state import (
        navigate_project_to_page,
    )

    navigate_project_to_page(st.session_state, "wedding_start")


def _open_wedding_followups() -> None:
    from ai_event_planning_platform.wedding_project_state import (
        navigate_project_to_page,
    )

    st.session_state["wedding_followup_return_page"] = "step_by_step"
    navigate_project_to_page(st.session_state, "wedding_followups")


def _open_wedding_ceremonies() -> None:
    from ai_event_planning_platform.wedding_project_state import (
        navigate_project_to_page,
    )

    navigate_project_to_page(st.session_state, "wedding_ceremonies")


def render_wedding_requirements(
    *,
    profile_state_key: str = DRAFT_STATE_KEY,
    saved_state_key: str = DRAFT_SAVED_STATE_KEY,
    widget_prefix: str = "wedding_",
    title: str = "Plan Step by Step",
    submit_label: str = "Save requirements draft",
    show_temporary_notice: bool = True,
    show_back_button: bool = True,
) -> None:
    from ai_event_planning_platform.wedding_project_state import (
        ACTIVE_PROJECT_ID_KEY,
        PROJECT_DIRTY_KEY,
        persist_active_project,
    )

    active_project_id = st.session_state.get(ACTIVE_PROJECT_ID_KEY)
    if isinstance(active_project_id, str):
        widget_prefix = f"{widget_prefix}{active_project_id}_"
    widget_key = lambda name: f"{widget_prefix}{name}"
    submit_widget_key = (
        "save_wedding_profile"
        if widget_prefix == "wedding_"
        else widget_key("save_profile")
    )
    back_widget_key = (
        "back_from_step_by_step"
        if widget_prefix == "wedding_"
        else widget_key("back_from_step_by_step")
    )
    st.title(title)
    st.write("Share what you know. All details except date status are optional.")
    if show_temporary_notice and not isinstance(active_project_id, str):
        st.info(
            "This draft is stored temporarily in this Streamlit session only. "
            "It is not saved to an account or database and will be lost when the session ends."
        )

    old_profile = st.session_state.get(profile_state_key)
    profile = migrate_wedding_profile(old_profile) or WeddingProfile()
    if old_profile is not None and profile is not old_profile:
        st.session_state[profile_state_key] = profile
    _initialize_form_state(profile, widget_prefix)

    st.subheader("Wedding date")
    date_status_label = st.selectbox(
        "Date status",
        tuple(DATE_STATUS_LABELS),
        key=widget_key("date_status"),
    )
    date_status = DATE_STATUS_LABELS[date_status_label]

    with st.form("wedding_requirements_form", clear_on_submit=False):
        bride_name = st.text_input("Bride's name (optional)", key=widget_key("bride_name_value"), max_chars=120)
        groom_name = st.text_input("Groom's name (optional)", key=widget_key("groom_name_value"), max_chars=120)
        wedding_date = None
        if date_status is not WeddingDateStatus.NOT_DECIDED_YET:
            date_label = (
                "Fixed wedding date"
                if date_status is WeddingDateStatus.FIXED_DATE
                else "Approximate wedding date"
            )
            wedding_date = st.date_input(
                date_label,
                key=widget_key("date_value"),
                min_value=date(1900, 1, 1),
                max_value=date(2100, 12, 31),
                format="DD/MM/YYYY",
                help="For an approximate date, only the month and year are used.",
            )

        st.subheader("Location")
        city = st.text_input("Wedding city (optional)", key=widget_key("city_value"))
        wedding_state = st.text_input(
            "Wedding state (optional)", key=widget_key("state_value")
        )
        location_options = ("Not selected", "Local wedding", "Destination wedding")
        st.session_state[widget_key("location_preference")] = {
            "local": "Local wedding",
            "destination": "Destination wedding",
            "not selected": "Not selected",
            "undecided": "Not selected",
        }.get(
            str(st.session_state.get(widget_key("location_preference"), "Not selected")).lower(),
            st.session_state.get(widget_key("location_preference"), "Not selected"),
        )
        location_label = st.selectbox(
            "Local or destination wedding (optional)",
            location_options,
            key=widget_key("location_preference"),
        )

        st.subheader("Guests and budget")
        guest_count = st.number_input(
            "Approximate total guest count (optional)",
            key=widget_key("guest_count_value"),
            min_value=None,
            max_value=(1 << 53) - 1,
            step=1,
            placeholder="Leave blank if not known",
        )
        budget = st.number_input(
            "Total wedding budget in INR (optional)",
            key=widget_key("budget_inr_value"),
            min_value=None,
            max_value=(1 << 53) - 1,
            step=10000,
            placeholder="Leave blank if not known",
        )

        st.subheader("Style and traditions")
        style_options = ("Not selected", *STYLE_VALUES)
        st.session_state[widget_key("style_value")] = {
            "simple": "Simple",
            "traditional": "Traditional",
            "luxury": "Luxury",
            "modern": "Modern",
            "mixed": "Mixed",
        }.get(
            str(st.session_state.get(widget_key("style_value"), "Not selected")).lower(),
            st.session_state.get(widget_key("style_value"), "Not selected"),
        )
        style_label = st.selectbox(
            "Wedding style (optional)", style_options, key=widget_key("style_value")
        )
        traditions = st.multiselect(
            "Preferred religious, secular, or custom traditions (optional)",
            tuple(TRADITION_VALUES),
            key=widget_key("traditions_value"),
        )

        st.subheader("Cultural backgrounds")
        partner_1_background = st.text_input(
            "Partner 1 cultural background (optional)",
            key=widget_key("partner_1_background_value"),
        )
        partner_1_region = st.text_input(
            "Partner 1 region (optional)", key=widget_key("partner_1_region_value")
        )
        partner_2_background = st.text_input(
            "Partner 2 cultural background (optional)",
            key=widget_key("partner_2_background_value"),
        )
        partner_2_region = st.text_input(
            "Partner 2 region (optional)", key=widget_key("partner_2_region_value")
        )
        additional_preferences = st.text_area(
            "Additional preferences (optional)",
            key=widget_key("additional_preferences_value"),
            max_chars=3000,
            help="Any extra details you want to include.",
        )
        submitted = st.form_submit_button(
            submit_label,
            key=submit_widget_key,
            type="primary",
        )

    if submitted:
        location = {
            "Local wedding": WeddingLocationPreference.LOCAL,
            "Destination wedding": WeddingLocationPreference.DESTINATION,
        }.get(location_label)
        style = STYLE_VALUES.get(style_label)
        candidate = WeddingProfile(
            wedding_date_status=date_status,
            wedding_date=wedding_date,
            city=city.strip() or None,
            state=wedding_state.strip() or None,
            location_preference=location,
            guest_count=guest_count,
            budget_inr=budget,
            style=style,
            partner_1_culture=(
                CulturalBackground(
                    partner_1_background.strip() or None,
                    partner_1_region.strip() or None,
                )
                if partner_1_background.strip() or partner_1_region.strip()
                else None
            ),
            partner_2_culture=(
                CulturalBackground(
                    partner_2_background.strip() or None,
                    partner_2_region.strip() or None,
                )
                if partner_2_background.strip() or partner_2_region.strip()
                else None
            ),
            traditions=tuple(TRADITION_VALUES[label] for label in traditions),
            additional_preferences=additional_preferences.strip() or None,
            bride_name=bride_name.strip() or None,
            groom_name=groom_name.strip() or None,
        )
        errors = validate_wedding_profile(candidate)
        if errors:
            for error in errors:
                st.error(error)
        else:
            st.session_state[profile_state_key] = candidate
            st.session_state[saved_state_key] = True
            profile = candidate
            if isinstance(active_project_id, str) and profile_state_key == DRAFT_STATE_KEY:
                try:
                    persist_active_project(
                        st.session_state,
                        profile=candidate,
                        planning_step="step_by_step",
                    )
                except Exception:
                    st.session_state[PROJECT_DIRTY_KEY] = True
                    st.error(
                        "Your requirements could not be saved to the database. "
                        "Your edits are still available in this session; retry Save."
                    )
                else:
                    st.success("Your requirements were saved to this wedding project.")
            elif profile_state_key != DRAFT_STATE_KEY:
                st.success("Your review changes are ready. Accept them to update your wedding project.")
            else:
                st.success("Your requirements draft was saved for this session.")

    if st.session_state.get(saved_state_key, False):
        saved_profile = migrate_wedding_profile(st.session_state.get(profile_state_key))
        if saved_profile is not None:
            _render_summary(saved_profile)
            if profile_state_key == DRAFT_STATE_KEY:
                from ai_event_planning_platform.wedding_ceremonies import (
                    render_saved_ceremony_summary,
                )

                render_saved_ceremony_summary()
                st.button(
                    "Edit Ceremonies",
                    key="choose_ceremonies_from_profile",
                    on_click=_open_wedding_ceremonies,
                )
                from ai_event_planning_platform.ceremony_planning import (
                    render_ceremony_plan_overview,
                )

                render_ceremony_plan_overview()
                from ai_event_planning_platform.wedding_project_state import navigate_project_to_page

                st.button(
                    "Smart Wedding Budget", key="wedding_budget_open",
                    on_click=navigate_project_to_page, args=(st.session_state, "smart_wedding_budget"),
                )
                st.button(
                    "Wedding Calendar & Timeline", key="wedding_timeline_open",
                    on_click=navigate_project_to_page, args=(st.session_state, "wedding_timeline"),
                )
                from ai_event_planning_platform.timeline_planning import render_timeline_summary
                from ai_event_planning_platform.wedding_project_state import PROFILE_SAVED_KEY, saved_project_ceremonies
                if st.session_state.get(PROFILE_SAVED_KEY):
                    try:
                        saved_ceremonies = saved_project_ceremonies(st.session_state)
                    except ValueError:
                        st.caption("Scheduling summary unavailable. Reopen the project to restore saved ceremony details.")
                    else:
                        render_timeline_summary(st.session_state, st.session_state[PROFILE_SAVED_KEY], saved_ceremonies)
                st.button(
                    "Complete Wedding Details",
                    key="open_wedding_followups_from_form",
                    on_click=_open_wedding_followups,
                )

    if show_back_button:
        st.button(
            "Back to planning options",
            key=back_widget_key,
            icon=":material/arrow_back:",
            on_click=_back_to_planning_options,
        )
