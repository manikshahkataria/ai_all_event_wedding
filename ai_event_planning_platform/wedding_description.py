import streamlit as st

from ai_event_planning_platform.wedding_extraction import (
    WeddingExtractionError,
    extract_wedding_profile,
)
from ai_event_planning_platform.wedding_form import (
    DRAFT_SAVED_STATE_KEY,
    DRAFT_STATE_KEY,
    render_wedding_requirements,
)
from ai_event_planning_platform.wedding_followups import (
    QUESTION_BY_ID,
    missing_followup_ids,
    render_followup_entry_button,
)
from ai_event_planning_platform.wedding_ceremonies import render_ceremony_entry_button
from ai_event_planning_platform.wedding_profile import (
    WeddingProfile,
    migrate_wedding_profile,
)


DESCRIPTION_STATE_KEY = "wedding_ai_description"
EXTRACTION_PROFILE_STATE_KEY = "wedding_ai_extraction_candidate"
EXTRACTION_SAVED_STATE_KEY = "wedding_ai_extraction_saved"
EXTRACTION_ACCEPTED_STATE_KEY = "wedding_ai_extraction_accepted"
EXTRACTION_IN_PROGRESS_KEY = "wedding_ai_extraction_in_progress"
REPLACE_DRAFT_STATE_KEY = "wedding_ai_replace_existing_draft"
REVIEW_WIDGET_PREFIX = "ai_review_wedding_"

SAMPLE_DESCRIPTION = (
    "We are hoping for a destination wedding in Jaipur around November 2027, "
    "with about 300 guests and a total budget of around 13 lakh INR. I am "
    "Punjabi from Punjab, and my partner is Tamil from Chennai. We would like "
    "to blend both families' religious traditions with a relaxed, modern "
    "celebration and an outdoor venue if possible."
)


def missing_wedding_details(profile: WeddingProfile) -> tuple[str, ...]:
    return tuple(QUESTION_BY_ID[field_id].label for field_id in missing_followup_ids(profile))


def _render_missing_details(profile: WeddingProfile) -> None:
    missing = missing_wedding_details(profile)
    st.subheader("Details to review")
    if missing:
        st.write("Not found in your description:")
        for item in missing:
            st.markdown(f"- {item}")
    else:
        st.success("All listed details were found in the description.")


def _render_accept_extracted_profile(profile: WeddingProfile) -> None:
    if st.session_state.get(EXTRACTION_ACCEPTED_STATE_KEY, False):
        st.success("The reviewed details are your active wedding requirements draft.")
        render_followup_entry_button(
            "describe_wedding", key="complete_wedding_details_after_extraction"
        )
        render_ceremony_entry_button("choose_ceremonies_after_accepted_extraction")
        return

    active_profile = migrate_wedding_profile(st.session_state.get(DRAFT_STATE_KEY))
    has_existing_draft = (
        active_profile is not None and active_profile != WeddingProfile()
    )
    replace_existing = False
    if has_existing_draft:
        st.warning(
            "A step-by-step wedding draft already exists. Accepting these extracted "
            "details will replace it. Save or review that draft before proceeding."
        )
        replace_existing = st.checkbox(
            "I want to replace the existing wedding draft.",
            key=REPLACE_DRAFT_STATE_KEY,
        )

    accepted = st.button(
        "Accept extracted details",
        key="accept_wedding_extraction",
        type="primary",
        disabled=has_existing_draft and not replace_existing,
    )
    if accepted:
        current_profile = migrate_wedding_profile(
            st.session_state.get(EXTRACTION_PROFILE_STATE_KEY)
        )
        if current_profile is None:
            st.error("There is no extracted profile to accept. Run extraction again.")
            return
        st.session_state[DRAFT_STATE_KEY] = current_profile
        st.session_state[DRAFT_SAVED_STATE_KEY] = True
        st.session_state[EXTRACTION_ACCEPTED_STATE_KEY] = True
        st.success(
            "The reviewed details are now your active wedding requirements draft."
        )
        render_followup_entry_button(
            "describe_wedding", key="complete_wedding_details_after_extraction"
        )
        render_ceremony_entry_button("choose_ceremonies_after_accepted_extraction")


def render_wedding_description() -> None:
    st.title("Describe Your Dream Wedding")
    st.write(
        "Describe your plans in your own words. Extracted details will stay separate "
        "from your active wedding draft until you review and accept them."
    )
    st.session_state.setdefault(DESCRIPTION_STATE_KEY, SAMPLE_DESCRIPTION)
    st.session_state.setdefault(EXTRACTION_IN_PROGRESS_KEY, False)

    description_label, voice_control = st.columns(
        [4, 1], vertical_alignment="bottom"
    )
    with description_label:
        st.markdown("**Wedding description**")
    with voice_control:
        st.button(
            "Voice input",
            key="wedding_voice_input",
            icon=":material/mic:",
            help="Soon you'll be able to describe your wedding using your voice.",
            disabled=True,
            type="tertiary",
            width="stretch",
        )

    with st.form("wedding_description_form", clear_on_submit=False):
        description = st.text_area(
            "Your wedding description",
            key=DESCRIPTION_STATE_KEY,
            height=220,
            max_chars=6000,
            help="Include only details you want the planner to use.",
        )
        submitted = st.form_submit_button(
            "Extract Wedding Details",
            key="extract_wedding_details",
            type="primary",
            disabled=st.session_state[EXTRACTION_IN_PROGRESS_KEY],
        )

    if submitted:
        if st.session_state[EXTRACTION_IN_PROGRESS_KEY]:
            st.info("Extraction is already processing.")
        else:
            st.session_state[EXTRACTION_IN_PROGRESS_KEY] = True
            st.session_state.pop(EXTRACTION_PROFILE_STATE_KEY, None)
            st.session_state[EXTRACTION_SAVED_STATE_KEY] = False
            st.session_state[EXTRACTION_ACCEPTED_STATE_KEY] = False
            st.session_state[REPLACE_DRAFT_STATE_KEY] = False
            try:
                with st.spinner("Extracting wedding details..."):
                    candidate = extract_wedding_profile(description)
            except WeddingExtractionError as error:
                st.error(str(error))
            else:
                st.session_state[EXTRACTION_PROFILE_STATE_KEY] = candidate
                st.session_state[EXTRACTION_SAVED_STATE_KEY] = True
                st.success("Details extracted. Review and edit them before accepting.")
            finally:
                st.session_state[EXTRACTION_IN_PROGRESS_KEY] = False

    candidate = migrate_wedding_profile(
        st.session_state.get(EXTRACTION_PROFILE_STATE_KEY)
    )
    if candidate is not None:
        _render_missing_details(candidate)
        render_wedding_requirements(
            profile_state_key=EXTRACTION_PROFILE_STATE_KEY,
            saved_state_key=EXTRACTION_SAVED_STATE_KEY,
            widget_prefix=REVIEW_WIDGET_PREFIX,
            title="Review extracted details",
            submit_label="Save review changes",
            show_temporary_notice=False,
            show_back_button=False,
        )
        reviewed_candidate = migrate_wedding_profile(
            st.session_state.get(EXTRACTION_PROFILE_STATE_KEY)
        )
        if reviewed_candidate is not None:
            _render_accept_extracted_profile(reviewed_candidate)

    st.button(
        "Back to planning options",
        key="back_from_describe_wedding",
        icon=":material/arrow_back:",
        on_click=lambda: st.session_state.__setitem__("page", "wedding_start"),
    )