from dataclasses import dataclass, replace
from datetime import date

import streamlit as st

from ai_event_planning_platform.wedding_form import DRAFT_SAVED_STATE_KEY, DRAFT_STATE_KEY
from ai_event_planning_platform.wedding_ceremonies import render_ceremony_entry_button
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


FOLLOWUP_ANSWERED_KEY = "wedding_followup_answered"
FOLLOWUP_SKIPPED_KEY = "wedding_followup_skipped"
FOLLOWUP_EDIT_KEY = "wedding_followup_edit_question"
FOLLOWUP_RETURN_PAGE_KEY = "wedding_followup_return_page"


@dataclass(frozen=True)
class FollowUpQuestion:
    field_id: str
    label: str
    prompt: str


FOLLOW_UP_QUESTIONS = (
    FollowUpQuestion("wedding_date", "Wedding date", "Do you have a wedding date in mind?"),
    FollowUpQuestion("city", "City", "Which city are you considering?"),
    FollowUpQuestion("state", "State", "Which state is the venue in?"),
    FollowUpQuestion(
        "location_preference",
        "Local or destination preference",
        "Would you prefer a local or destination wedding?",
    ),
    FollowUpQuestion("guest_count", "Guest count", "About how many guests do you expect?"),
    FollowUpQuestion("budget_inr", "Total budget", "What overall wedding budget in INR feels right?"),
    FollowUpQuestion("style", "Wedding style", "Which wedding style feels closest to your vision?"),
    FollowUpQuestion(
        "partner_1_background",
        "Partner 1 cultural background",
        "How would you describe Partner 1's cultural background, if you would like to share?",
    ),
    FollowUpQuestion(
        "partner_1_region",
        "Partner 1 region",
        "Is there a region or community important to Partner 1's wedding preferences?",
    ),
    FollowUpQuestion(
        "partner_2_background",
        "Partner 2 cultural background",
        "How would you describe Partner 2's cultural background, if you would like to share?",
    ),
    FollowUpQuestion(
        "partner_2_region",
        "Partner 2 region",
        "Is there a region or community important to Partner 2's wedding preferences?",
    ),
    FollowUpQuestion(
        "traditions",
        "Tradition preferences",
        "What religious, secular, or custom traditions, if any, would you like to include? Cultural background does not imply a particular ceremony.",
    ),
)
QUESTION_BY_ID = {question.field_id: question for question in FOLLOW_UP_QUESTIONS}
QUESTION_COUNT = len(FOLLOW_UP_QUESTIONS)
DATE_STATUS_LABELS = {
    "Fixed Date": WeddingDateStatus.FIXED_DATE,
    "Approximate Date": WeddingDateStatus.APPROXIMATE_DATE,
    "Not Decided Yet": WeddingDateStatus.NOT_DECIDED_YET,
}
STYLE_OPTIONS = {
    "Simple": WeddingStyle.SIMPLE,
    "Traditional": WeddingStyle.TRADITIONAL,
    "Luxury": WeddingStyle.LUXURY,
    "Modern": WeddingStyle.MODERN,
    "Mixed": WeddingStyle.MIXED,
}
TRADITION_OPTIONS = {
    "Religious": TraditionPreference.RELIGIOUS,
    "Secular": TraditionPreference.SECULAR,
    "Custom": TraditionPreference.CUSTOM,
}


def _culture_missing(culture: CulturalBackground | None, part: str) -> bool:
    return culture is None or not getattr(culture, part)


def missing_followup_ids(profile: WeddingProfile) -> tuple[str, ...]:
    missing: list[str] = []
    if profile.wedding_date_status is WeddingDateStatus.NOT_DECIDED_YET or profile.wedding_date is None:
        missing.append("wedding_date")
    if not profile.city:
        missing.append("city")
    if not profile.state:
        missing.append("state")
    if profile.location_preference is None:
        missing.append("location_preference")
    if profile.guest_count is None:
        missing.append("guest_count")
    if profile.budget_inr is None:
        missing.append("budget_inr")
    if profile.style is None:
        missing.append("style")
    if _culture_missing(profile.partner_1_culture, "background"):
        missing.append("partner_1_background")
    if _culture_missing(profile.partner_1_culture, "region"):
        missing.append("partner_1_region")
    if _culture_missing(profile.partner_2_culture, "background"):
        missing.append("partner_2_background")
    if _culture_missing(profile.partner_2_culture, "region"):
        missing.append("partner_2_region")
    if not profile.traditions:
        missing.append("traditions")
    return tuple(missing)


def provided_detail_count(profile: WeddingProfile) -> int:
    return QUESTION_COUNT - len(missing_followup_ids(profile))


def _current_question_id(profile: WeddingProfile) -> str | None:
    edit_question = st.session_state.get(FOLLOWUP_EDIT_KEY)
    if edit_question in QUESTION_BY_ID:
        return edit_question
    answered = set(st.session_state.get(FOLLOWUP_ANSWERED_KEY, []))
    skipped = set(st.session_state.get(FOLLOWUP_SKIPPED_KEY, []))
    return next(
        (
            field_id
            for field_id in missing_followup_ids(profile)
            if field_id not in answered and field_id not in skipped
        ),
        None,
    )


def _update_profile(profile: WeddingProfile, field_id: str, values: dict[str, object]) -> WeddingProfile:
    if field_id == "wedding_date":
        status = values["date_status"]
        return replace(
            profile,
            wedding_date_status=status,
            wedding_date=values.get("date"),
        )
    if field_id in {"city", "state"}:
        return replace(profile, **{field_id: str(values[field_id]).strip()})
    if field_id == "location_preference":
        return replace(profile, location_preference=values["location_preference"])
    if field_id == "guest_count":
        return replace(profile, guest_count=values["guest_count"])
    if field_id == "budget_inr":
        return replace(profile, budget_inr=values["budget_inr"])
    if field_id == "style":
        return replace(profile, style=values["style"])
    if field_id in {"partner_1_background", "partner_1_region"}:
        current = profile.partner_1_culture or CulturalBackground()
        field_name = "background" if field_id.endswith("background") else "region"
        culture = replace(current, **{field_name: str(values[field_id]).strip()})
        return replace(profile, partner_1_culture=culture)
    if field_id in {"partner_2_background", "partner_2_region"}:
        current = profile.partner_2_culture or CulturalBackground()
        field_name = "background" if field_id.endswith("background") else "region"
        culture = replace(current, **{field_name: str(values[field_id]).strip()})
        return replace(profile, partner_2_culture=culture)
    if field_id == "traditions":
        traditions = tuple(values["traditions"])
        notes = str(values.get("tradition_notes", "")).strip()
        if notes and TraditionPreference.CUSTOM not in traditions:
            traditions += (TraditionPreference.CUSTOM,)
        preferences = profile.additional_preferences
        if notes:
            note = f"Tradition preferences: {notes}"
            preferences = f"{preferences}\n{note}" if preferences else note
        return replace(profile, traditions=traditions, additional_preferences=preferences)
    raise ValueError("Unknown wedding detail question.")


def _validate_followup_answer(field_id: str, values: dict[str, object]) -> str | None:
    if field_id == "wedding_date":
        status = values.get("date_status")
        if status is not WeddingDateStatus.NOT_DECIDED_YET and values.get("date") is None:
            return "Enter a date or choose Not Decided Yet."
    elif field_id in {"city", "state", "partner_1_background", "partner_1_region", "partner_2_background", "partner_2_region"}:
        if not str(values.get(field_id, "")).strip():
            return "Please enter an answer or skip this question for now."
    elif field_id in {"guest_count", "budget_inr"}:
        if values.get(field_id) is None:
            return "Please enter an answer or skip this question for now."
    elif field_id == "traditions":
        if not values.get("traditions") and not str(values.get("tradition_notes", "")).strip():
            return "Choose a preference, describe a custom, or skip for now."
    return None


def _render_answer_field(question_id: str, profile: WeddingProfile) -> dict[str, object]:
    key = f"wedding_followup_{question_id}"
    if question_id == "wedding_date":
        current_status = next(
            (
                label
                for label, status in DATE_STATUS_LABELS.items()
                if status is profile.wedding_date_status
            ),
            "Not Decided Yet",
        )
        st.session_state.setdefault(f"{key}_status", current_status)
        selected_status = st.selectbox(
            "Date status",
            tuple(DATE_STATUS_LABELS),
            key=f"{key}_status",
        )
        status = DATE_STATUS_LABELS[selected_status]
        if status is WeddingDateStatus.NOT_DECIDED_YET:
            return {"date_status": status, "date": None}
        current_date = profile.wedding_date
        if isinstance(current_date, str):
            try:
                current_date = date.fromisoformat(
                    f"{current_date}-01" if len(current_date) == 7 else current_date
                )
            except ValueError:
                current_date = None
        st.session_state.setdefault(f"{key}_value", current_date)
        selected_date = st.date_input(
            "Wedding date or approximate month",
            key=f"{key}_value",
            min_value=date(1900, 1, 1),
            max_value=date(2100, 12, 31),
            format="DD/MM/YYYY",
        )
        if status is WeddingDateStatus.APPROXIMATE_DATE and selected_date is not None:
            selected_date = selected_date.replace(day=1)
        return {"date_status": status, "date": selected_date}

    if question_id in {"city", "state", "partner_1_background", "partner_1_region", "partner_2_background", "partner_2_region"}:
        culture = (
            profile.partner_1_culture
            if question_id.startswith("partner_1")
            else profile.partner_2_culture
        )
        current = (
            getattr(culture, "background" if question_id.endswith("background") else "region")
            if question_id.startswith("partner_") and culture
            else getattr(profile, question_id, None)
        )
        st.session_state.setdefault(f"{key}_value", current or "")
        value = st.text_input("Your answer", key=f"{key}_value")
        return {question_id: value}

    if question_id == "location_preference":
        options = ("Not sure yet", "Local wedding", "Destination wedding")
        current = {
            WeddingLocationPreference.LOCAL: "Local wedding",
            WeddingLocationPreference.DESTINATION: "Destination wedding",
        }.get(profile.location_preference, "Not sure yet")
        st.session_state.setdefault(f"{key}_value", current)
        selected = st.selectbox("Your preference", options, key=f"{key}_value")
        value = {
            "Local wedding": WeddingLocationPreference.LOCAL,
            "Destination wedding": WeddingLocationPreference.DESTINATION,
        }.get(selected)
        return {"location_preference": value}

    if question_id in {"guest_count", "budget_inr"}:
        current = getattr(profile, question_id)
        st.session_state.setdefault(f"{key}_value", current)
        value = st.number_input(
            "Approximate guest count" if question_id == "guest_count" else "Budget in INR",
            min_value=None,
            max_value=(1 << 53) - 1,
            step=1 if question_id == "guest_count" else 10000,
            key=f"{key}_value",
            placeholder="Enter a whole number",
        )
        return {question_id: value}

    if question_id == "style":
        options = ("Not sure yet", "Simple", "Traditional", "Luxury", "Modern", "Mixed")
        current = profile.style.value.title() if profile.style else "Not sure yet"
        st.session_state.setdefault(f"{key}_value", current)
        selected = st.selectbox("Your preferred style", options, key=f"{key}_value")
        style = next((item for item in WeddingStyle if item.value.title() == selected), None)
        return {"style": style}

    if question_id == "traditions":
        st.caption("These are preferences only; no ceremony or schedule is being confirmed.")
        current = [item.value.title() for item in profile.traditions]
        st.session_state.setdefault(f"{key}_values", current)
        selections = st.multiselect(
            "Choose any that fit",
            ("Religious", "Secular", "Custom"),
            key=f"{key}_values",
        )
        st.session_state.setdefault(f"{key}_notes", "")
        notes = st.text_input(
            "Any particular customs or details? (optional)",
            key=f"{key}_notes",
        )
        return {
            "traditions": tuple(
                tradition
                for tradition in TraditionPreference
                if tradition.value.title() in selections
            ),
            "tradition_notes": notes,
        }
    raise ValueError("Unknown wedding detail question.")


def _provided_detail_count(profile: WeddingProfile) -> int:
    missing = set(missing_followup_ids(profile))
    return sum(question.field_id not in missing for question in FOLLOW_UP_QUESTIONS)


def _go_to_return_page() -> None:
    from ai_event_planning_platform.wedding_project_state import navigate_project_to_page

    navigate_project_to_page(
        st.session_state, st.session_state.get(FOLLOWUP_RETURN_PAGE_KEY, "step_by_step")
    )


def render_followup_entry_button(return_page: str, *, key: str) -> None:
    st.button(
        "Complete Wedding Details",
        key=key,
        on_click=lambda: _open_followups(return_page),
    )


def _open_followups(return_page: str) -> None:
    from ai_event_planning_platform.wedding_project_state import navigate_project_to_page

    st.session_state[FOLLOWUP_RETURN_PAGE_KEY] = return_page
    navigate_project_to_page(st.session_state, "wedding_followups")


def _render_answered_editor() -> None:
    answered = [
        field_id
        for field_id in st.session_state.get(FOLLOWUP_ANSWERED_KEY, [])
        if field_id in QUESTION_BY_ID
    ]
    if not answered:
        return
    labels = {QUESTION_BY_ID[field_id].label: field_id for field_id in answered}
    st.selectbox("Go back and edit an earlier answer", tuple(labels), key="wedding_followup_edit_select")
    if st.button("Edit selected answer", key="wedding_followup_edit_button"):
        st.session_state[FOLLOWUP_EDIT_KEY] = labels[
            st.session_state["wedding_followup_edit_select"]
        ]
        st.rerun()


def render_wedding_followups() -> None:
    from ai_event_planning_platform.wedding_project_state import (
        ACTIVE_PROJECT_ID_KEY,
        PROJECT_DIRTY_KEY,
        PROJECT_SUCCESS_KEY,
        persist_active_project,
    )

    st.title("Complete Wedding Details")
    profile = migrate_wedding_profile(st.session_state.get(DRAFT_STATE_KEY))
    if profile is None or not st.session_state.get(DRAFT_SAVED_STATE_KEY, False):
        st.info("Save a wedding requirements draft before answering follow-up questions.")
        render_ceremony_entry_button("choose_ceremonies_from_followups")
        st.button(
            "Continue with available details",
            key="wedding_followup_continue",
            on_click=_go_to_return_page,
        )
        return

    st.session_state.setdefault(FOLLOWUP_ANSWERED_KEY, [])
    st.session_state.setdefault(FOLLOWUP_SKIPPED_KEY, [])
    st.session_state.setdefault(FOLLOWUP_EDIT_KEY, None)

    st.write(f"{_provided_detail_count(profile)} of {QUESTION_COUNT} planning details provided.")
    missing_ids = missing_followup_ids(profile)
    answered = set(st.session_state[FOLLOWUP_ANSWERED_KEY])
    skipped = set(st.session_state[FOLLOWUP_SKIPPED_KEY])
    edit_id = st.session_state.get(FOLLOWUP_EDIT_KEY)
    if edit_id in QUESTION_BY_ID:
        question_id = edit_id
    else:
        question_id = next(
            (item for item in missing_ids if item not in answered and item not in skipped),
            None,
        )

    if question_id is None:
        st.success("No more follow-up questions are needed right now.")
        unanswered = [QUESTION_BY_ID[item].label for item in missing_ids]
        if unanswered:
            st.write("Still unanswered (you can continue without these):")
            for label in unanswered:
                st.markdown(f"- {label}")
        st.button(
            "Continue with available details",
            key="wedding_followup_continue",
            on_click=_go_to_return_page,
        )
        render_ceremony_entry_button("choose_ceremonies_from_followups")
        _render_answered_editor()
        return

    question = QUESTION_BY_ID[question_id]
    st.subheader(question.label)
    st.write(question.prompt)
    if question_id == "traditions" and (
        profile.partner_1_culture or profile.partner_2_culture
    ):
        st.caption("Cultural background does not imply a particular ceremony or tradition.")

    values = _render_answer_field(question_id, profile)
    submitted = st.button("Save answer", key="wedding_followup_save")

    if submitted:
        answer_error = _validate_followup_answer(question_id, values)
        if answer_error:
            st.error(answer_error)
        else:
            try:
                updated = _update_profile(profile, question_id, values)
            except (TypeError, ValueError):
                st.error("That answer is not valid. Please review it and try again.")
            else:
                errors = validate_wedding_profile(updated)
                if errors:
                    for error in errors:
                        st.error(error)
                else:
                    st.session_state[DRAFT_STATE_KEY] = updated
                    st.session_state[DRAFT_SAVED_STATE_KEY] = True
                    if isinstance(
                        st.session_state.get(ACTIVE_PROJECT_ID_KEY), str
                    ):
                        try:
                            persist_active_project(
                                st.session_state,
                                profile=updated,
                                planning_step="wedding_followups",
                            )
                        except Exception:
                            st.session_state[PROJECT_DIRTY_KEY] = True
                            st.session_state[FOLLOWUP_EDIT_KEY] = question_id
                            st.error(
                                "Your answer could not be saved to the database. "
                                "Your edits are still available in this session; "
                                "retry Save answer."
                            )
                        else:
                            st.session_state[PROJECT_SUCCESS_KEY] = (
                                "Your answer was saved to this wedding project."
                            )
                            answered.add(question_id)
                            skipped.discard(question_id)
                            st.session_state[FOLLOWUP_ANSWERED_KEY] = list(answered)
                            st.session_state[FOLLOWUP_SKIPPED_KEY] = list(skipped)
                            st.session_state[FOLLOWUP_EDIT_KEY] = None
                            st.rerun()
                    else:
                        answered.add(question_id)
                        skipped.discard(question_id)
                        st.session_state[FOLLOWUP_ANSWERED_KEY] = list(answered)
                        st.session_state[FOLLOWUP_SKIPPED_KEY] = list(skipped)
                        st.session_state[FOLLOWUP_EDIT_KEY] = None
                        st.rerun()

    skip_label = "Cancel editing" if edit_id in QUESTION_BY_ID else "Skip for now"
    if st.button(skip_label, key="wedding_followup_skip"):
        if edit_id in QUESTION_BY_ID:
            st.session_state[FOLLOWUP_EDIT_KEY] = None
        else:
            skipped.add(question_id)
            st.session_state[FOLLOWUP_SKIPPED_KEY] = list(skipped)
        st.rerun()

    unanswered_labels = [QUESTION_BY_ID[item].label for item in missing_ids]
    if unanswered_labels:
        st.caption("Unanswered: " + ", ".join(unanswered_labels))
    _render_answered_editor()
    render_ceremony_entry_button("choose_ceremonies_from_followups")


def missing_followup_ids(profile: WeddingProfile) -> tuple[str, ...]:
    missing: list[str] = []
    if profile.wedding_date_status is WeddingDateStatus.NOT_DECIDED_YET or profile.wedding_date is None:
        missing.append("wedding_date")
    if not profile.city:
        missing.append("city")
    if not profile.state:
        missing.append("state")
    if profile.location_preference is None:
        missing.append("location_preference")
    if profile.guest_count is None:
        missing.append("guest_count")
    if profile.budget_inr is None:
        missing.append("budget_inr")
    if profile.style is None:
        missing.append("style")
    if _culture_missing(profile.partner_1_culture, "background"):
        missing.append("partner_1_background")
    if _culture_missing(profile.partner_1_culture, "region"):
        missing.append("partner_1_region")
    if _culture_missing(profile.partner_2_culture, "background"):
        missing.append("partner_2_background")
    if _culture_missing(profile.partner_2_culture, "region"):
        missing.append("partner_2_region")
    if not profile.traditions:
        missing.append("traditions")
    return tuple(missing)


def _culture_missing(culture: CulturalBackground | None, part: str) -> bool:
    return culture is None or not getattr(culture, part)
