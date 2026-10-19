"""Compact display of the active project's verified saved wedding context."""

from datetime import date

import streamlit as st

from ai_event_planning_platform.budget_engine import format_inr
from ai_event_planning_platform.dashboard_data import wedding_display_title
from ai_event_planning_platform.wedding_profile import WeddingDateStatus, WeddingProfile
from ai_event_planning_platform.wedding_project_state import (
    ACTIVE_PROJECT_ID_KEY, ACTIVE_PROJECT_NAME_KEY, LOADED_PROJECT_ID_KEY, PROFILE_SAVED_KEY,
    saved_project_ceremonies,
)


def wedding_glance_fields(name, profile, ceremonies):
    date_value = profile.wedding_date
    if isinstance(date_value, str):
        try:
            date_value = date.fromisoformat(date_value + "-01" if len(date_value) == 7 else date_value)
        except ValueError:
            date_value = None
    status = profile.wedding_date_status
    if not isinstance(date_value, date) or status is WeddingDateStatus.NOT_DECIDED_YET:
        date_label = "Not provided (not decided yet)"
    elif status is WeddingDateStatus.APPROXIMATE_DATE:
        date_label = date_value.strftime("%B %Y") + " (approximate date)"
    else:
        date_label = date_value.strftime("%d %B %Y") + " (fixed date)"
    fields = [
        ("Project", wedding_display_title(profile, name or "Not provided")),
        ("City and state", ", ".join(v for v in (profile.city, profile.state) if v) or "Not provided"),
        ("Wedding date", date_label),
        ("Wedding location", profile.location_preference.value.capitalize() if profile.location_preference else "Not provided"),
        ("Total wedding guests", str(profile.guest_count) if profile.guest_count is not None else "Not provided"),
        ("Total wedding budget", format_inr(profile.budget_inr) if profile.budget_inr is not None else "Not provided"),
        ("Wedding style", profile.style.value.capitalize() if profile.style else "Not provided"),
    ]
    for number, culture in enumerate((profile.partner_1_culture, profile.partner_2_culture), 1):
        if culture and (culture.background or culture.region):
            fields.append((f"Partner {number} cultural background", ", ".join(v for v in (culture.background, culture.region) if v)))
    fields.append(("Confirmed ceremonies", ", ".join(c.name for c in ceremonies) or "Not provided"))
    if profile.additional_preferences:
        fields.append(("Additional preferences", profile.additional_preferences))
    return fields


def render_wedding_at_a_glance() -> bool:
    project_id = st.session_state.get(ACTIVE_PROJECT_ID_KEY)
    profile = st.session_state.get(PROFILE_SAVED_KEY)
    try:
        ceremonies = saved_project_ceremonies(st.session_state)
        if (not project_id or st.session_state.get(LOADED_PROJECT_ID_KEY) != project_id
                or not isinstance(profile, WeddingProfile)):
            raise ValueError
    except ValueError:
        st.error("Saved wedding details are unavailable. Reopen this wedding project.")
        return False
    fields = wedding_glance_fields(st.session_state.get(ACTIVE_PROJECT_NAME_KEY), profile, ceremonies)
    with st.container(border=True, key="wedding_glance_panel"):
        st.subheader("Wedding at a Glance")
        left, right = st.columns(2)
        for index, (label, value) in enumerate(fields):
            (left if index % 2 == 0 else right).text(f"{label}: {value}")
    return True
