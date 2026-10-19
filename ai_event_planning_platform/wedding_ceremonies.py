from dataclasses import dataclass, replace
import re
from uuid import uuid4

import streamlit as st

from ai_event_planning_platform.ceremony_plan import CeremonyPlan
from ai_event_planning_platform.wedding_form import DRAFT_STATE_KEY
from ai_event_planning_platform.wedding_profile import (
    CulturalBackground,
    TraditionPreference,
    WeddingProfile,
    migrate_wedding_profile,
)


WEDDING_CEREMONIES_PAGE = "wedding_ceremonies"
CEREMONIES_STATE_KEY = "wedding_ceremonies_selected"
CEREMONIES_CONFIRMED_KEY = "wedding_ceremonies_confirmed"
CUSTOM_CEREMONIES_STATE_KEY = "wedding_custom_ceremonies"
CEREMONY_CHECKBOX_PREFIX = "wedding_ceremony_selected_"
CEREMONIES_SAVED_KEY = "wedding_ceremonies_saved_snapshot"
CEREMONIES_DRAFT_IDS_KEY = "wedding_ceremonies_draft_ids"


@dataclass(frozen=True)
class Ceremony:
    ceremony_id: str
    name: str
    description: str
    cultural_association: str | None = None
    is_custom: bool = False
    selected: bool = False
    plan: CeremonyPlan | None = None


@dataclass(frozen=True)
class CeremonySuggestion:
    ceremony: Ceremony
    reason: str


CEREMONY_CATALOG = (
    Ceremony(
        "haldi",
        "Haldi / Halad",
        "A pre-wedding gathering in which turmeric paste is applied to the couple in some families.",
        "Optional South Asian ceremony; names and practices vary by family.",
    ),
    Ceremony(
        "mehendi",
        "Mehendi",
        "A gathering centered on applying henna, often held before the wedding day.",
        "Optional ceremony observed in some South Asian wedding traditions.",
    ),
    Ceremony(
        "sangeet",
        "Sangeet",
        "A pre-wedding celebration centered on music, singing, and sometimes dance.",
        "Optional celebration included by some families; not a universal requirement.",
    ),
    Ceremony(
        "wedding_ceremony",
        "Wedding ceremony",
        "The ceremony or service that marks the couple's marriage, shaped by their choices and traditions.",
    ),
    Ceremony(
        "reception",
        "Reception",
        "A gathering for the couple and guests to celebrate the wedding.",
    ),
    Ceremony(
        "engagement",
        "Engagement",
        "An optional event celebrating the couple's engagement before the wedding.",
    ),
    Ceremony(
        "punjabi_hindu_jaimala",
        "Jaimala / Varmala",
        "A mutual exchange of flower garlands that appears in some Hindu wedding ceremonies.",
        "Optional example associated with some Punjabi Hindu weddings; practices vary.",
    ),
    Ceremony(
        "punjabi_sikh_anand_karaj",
        "Anand Karaj",
        "The Sikh marriage ceremony, observed according to the couple's and families' Sikh practice.",
        "Optional Sikh ceremony; Punjabi Sikh families may observe it in different ways.",
    ),
    Ceremony(
        "tamil_hindu_kashi_yatra",
        "Kashi Yatra",
        "A symbolic departure-and-return ritual included in some Tamil Hindu wedding ceremonies.",
        "Optional example from some Tamil Hindu weddings; practices vary.",
    ),
    Ceremony(
        "maharashtrian_sakharpuda",
        "Sakharpuda",
        "A Maharashtrian engagement custom associated with an exchange of sugar, with family practices varying.",
        "Optional example from Maharashtrian traditions.",
    ),
    Ceremony(
        "bengali_hindu_shubho_drishti",
        "Shubho Drishti",
        "An auspicious first look between the couple included in some Bengali Hindu weddings.",
        "Optional example from some Bengali Hindu weddings; practices vary.",
    ),
    Ceremony(
        "christian_wedding_service",
        "Christian wedding service",
        "A Christian marriage service; liturgy, vows, and observances vary by denomination and couple.",
        "Optional Christian ceremony; the couple chooses the service and observances.",
    ),
    Ceremony(
        "muslim_nikah",
        "Nikah",
        "An Islamic marriage contract and ceremony; details and accompanying celebrations vary.",
        "Optional Muslim ceremony; practices vary by family and community.",
    ),
)
CEREMONY_BY_ID = {ceremony.ceremony_id: ceremony for ceremony in CEREMONY_CATALOG}

_SOUTH_ASIAN_MARKERS = (
    "south asian",
    "indian",
    "india",
    "punjabi",
    "punjab",
    "tamil",
    "tamil nadu",
    "maharashtrian",
    "maharashtra",
    "bengali",
    "west bengal",
)
_RELIGION_MARKERS = ("hindu", "sikh", "christian", "muslim")
_CULTURAL_MARKERS = (
    "punjabi",
    "punjab",
    "tamil",
    "tamil nadu",
    "maharashtrian",
    "maharashtra",
    "bengali",
    "west bengal",
    "south asian",
    "indian",
    "india",
)


def _contains(text: str, phrase: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(phrase.casefold())}(?!\w)", text.casefold()) is not None


def _profile_contexts(profile: WeddingProfile) -> tuple[str, ...]:
    contexts = []
    for culture in (profile.partner_1_culture, profile.partner_2_culture):
        if isinstance(culture, CulturalBackground):
            contexts.append(" ".join(filter(None, (culture.background, culture.region))))
    if profile.additional_preferences:
        contexts.append(profile.additional_preferences)
    return tuple(context for context in contexts if context)


def _has_any(contexts: tuple[str, ...], markers: tuple[str, ...]) -> bool:
    return any(_contains(context, marker) for context in contexts for marker in markers)


def _suggestion(ceremony_id: str, reason: str) -> CeremonySuggestion:
    return CeremonySuggestion(CEREMONY_BY_ID[ceremony_id], reason)


def recommend_ceremonies(
    profile: WeddingProfile | None,
) -> tuple[tuple[CeremonySuggestion, ...], str | None]:
    """Match explicit profile preferences to optional catalog entries."""
    contexts = _profile_contexts(profile) if profile else ()
    suggestions = [
        _suggestion(
            "wedding_ceremony",
            "A central part of most wedding plans; its format is yours to define.",
        ),
        _suggestion(
            "reception",
            "A common, optional way to celebrate with guests around the wedding.",
        ),
        _suggestion(
            "engagement",
            "An optional pre-wedding event if you would like to celebrate your engagement.",
        ),
    ]
    suggested_ids = {item.ceremony.ceremony_id for item in suggestions}

    def add(ceremony_id: str, reason: str) -> None:
        if ceremony_id not in suggested_ids:
            suggestions.append(_suggestion(ceremony_id, reason))
            suggested_ids.add(ceremony_id)

    if _has_any(contexts, _SOUTH_ASIAN_MARKERS):
        for ceremony_id in ("haldi", "mehendi", "sangeet"):
            add(
                ceremony_id,
                "You shared a South Asian background or region. This is an optional idea seen in some families, not an assumption about your practices.",
            )

    secular = profile is not None and TraditionPreference.SECULAR in profile.traditions
    if not secular:
        has_sikh = _has_any(contexts, ("sikh",))
        has_christian = _has_any(contexts, ("christian",))
        has_muslim = _has_any(contexts, ("muslim",))
        if has_sikh:
            add(
                "punjabi_sikh_anand_karaj",
                "You explicitly mentioned Sikh tradition. This optional catalog entry is a Sikh marriage ceremony.",
            )
        if has_christian:
            add(
                "christian_wedding_service",
                "You explicitly mentioned Christian tradition. Service details vary, so this is only a starting point.",
            )
        if has_muslim:
            add(
                "muslim_nikah",
                "You explicitly mentioned Muslim tradition. This optional catalog entry can be considered or skipped.",
            )

        for context in contexts:
            if _contains(context, "punjabi") and _contains(context, "hindu"):
                add(
                    "punjabi_hindu_jaimala",
                    "You explicitly mentioned Punjabi Hindu tradition. This optional example may or may not fit your families.",
                )
            if _contains(context, "tamil") and _contains(context, "hindu"):
                add(
                    "tamil_hindu_kashi_yatra",
                    "You explicitly mentioned Tamil Hindu tradition. This optional example may or may not fit your families.",
                )
            if _contains(context, "bengali") and _contains(context, "hindu"):
                add(
                    "bengali_hindu_shubho_drishti",
                    "You explicitly mentioned Bengali Hindu tradition. This optional example may or may not fit your families.",
                )
            if _contains(context, "maharashtrian"):
                add(
                    "maharashtrian_sakharpuda",
                    "You explicitly mentioned a Maharashtrian background. This engagement custom is optional and family practices vary.",
                )

    explicit_religion = _has_any(contexts, _RELIGION_MARKERS)
    cultural_context = _has_any(contexts, _CULTURAL_MARKERS)
    clarification = None
    if (
        profile is not None
        and TraditionPreference.SECULAR not in profile.traditions
        and TraditionPreference.RELIGIOUS in profile.traditions
        and not explicit_religion
    ):
        clarification = (
            "You selected religious traditions but did not name a specific faith. "
            "Which tradition, if any, should suggestions reflect, or should they stay general?"
        )
    elif (
        cultural_context
        and not explicit_religion
        and not (profile and profile.traditions)
    ):
        clarification = (
            "You shared cultural background or region details. Is there a specific "
            "religious or family tradition you want reflected, or should suggestions stay general?"
        )

    return tuple(suggestions), clarification


def _custom_ceremonies() -> list[Ceremony]:
    values = st.session_state.get(CUSTOM_CEREMONIES_STATE_KEY, [])
    return [item for item in values if isinstance(item, Ceremony) and item.is_custom]


def _selected_ids() -> set[str]:
    values = st.session_state.get(CEREMONIES_STATE_KEY, ())
    return {
        item.ceremony_id
        for item in values
        if isinstance(item, Ceremony) and item.selected
    }


def _render_ceremony_option(ceremony: Ceremony, reason: str | None = None) -> None:
    key = f"{CEREMONY_CHECKBOX_PREFIX}{ceremony.ceremony_id}"
    draft_ids = st.session_state.setdefault(CEREMONIES_DRAFT_IDS_KEY, list(_selected_ids()))
    st.session_state.setdefault(key, ceremony.ceremony_id in draft_ids)
    st.checkbox(
        ceremony.name, key=key, on_change=_record_ceremony_choice,
        args=(ceremony.ceremony_id,),
    )
    st.caption(ceremony.description)
    if ceremony.cultural_association:
        st.caption(ceremony.cultural_association)
    if reason:
        st.write(reason)


def _record_ceremony_choice(ceremony_id: str) -> None:
    draft_ids = set(st.session_state.get(CEREMONIES_DRAFT_IDS_KEY, ()))
    if st.session_state.get(f"{CEREMONY_CHECKBOX_PREFIX}{ceremony_id}", False):
        draft_ids.add(ceremony_id)
    else:
        draft_ids.discard(ceremony_id)
    st.session_state[CEREMONIES_DRAFT_IDS_KEY] = list(draft_ids)


def _draft_selection(ceremonies: list[Ceremony]) -> list[Ceremony]:
    draft_ids = set(st.session_state.get(CEREMONIES_DRAFT_IDS_KEY, ()))
    saved_by_id = {
        item.ceremony_id: item
        for item in st.session_state.get(CEREMONIES_SAVED_KEY, ())
    }
    selected = []
    for item in ceremonies:
        if item.ceremony_id not in draft_ids:
            continue
        saved = saved_by_id.get(item.ceremony_id)
        plan = item.plan or (saved.plan if saved else None)
        if plan is not None:
            plan = replace(plan, ceremony_name=item.name)
        selected.append(replace(item, selected=True, plan=plan))
    return selected


def _same_selection(first: list[Ceremony], second: list[Ceremony]) -> bool:
    return {item.ceremony_id: item for item in first} == {
        item.ceremony_id: item for item in second
    }


def _save_custom_name(ceremony_id: str, name: str) -> None:
    customs = _custom_ceremonies()
    updated = [
        replace(item, name=name.strip())
        if item.ceremony_id == ceremony_id
        else item
        for item in customs
    ]
    st.session_state[CUSTOM_CEREMONIES_STATE_KEY] = updated
    st.session_state[CEREMONIES_STATE_KEY] = [
        replace(item, name=name.strip()) if item.ceremony_id == ceremony_id else item
        for item in st.session_state.get(CEREMONIES_STATE_KEY, ())
    ]


def _delete_custom_ceremony(ceremony_id: str) -> None:
    st.session_state[CEREMONIES_DRAFT_IDS_KEY] = [
        item for item in st.session_state.get(CEREMONIES_DRAFT_IDS_KEY, ())
        if item != ceremony_id
    ]
    st.session_state[CUSTOM_CEREMONIES_STATE_KEY] = [
        item for item in _custom_ceremonies() if item.ceremony_id != ceremony_id
    ]
    st.session_state[CEREMONIES_STATE_KEY] = [
        item
        for item in st.session_state.get(CEREMONIES_STATE_KEY, ())
        if item.ceremony_id != ceremony_id
    ]


def _render_custom_ceremonies() -> None:
    st.subheader("Custom family ceremony")
    st.write("Add a ceremony by any name that reflects your family's plans.")
    with st.form("add_custom_ceremony_form", clear_on_submit=True):
        name = st.text_input("Ceremony name", max_chars=80, key="custom_ceremony_new_name")
        submitted = st.form_submit_button(
            "Add custom ceremony", key="add_custom_ceremony_submit"
        )
    if submitted:
        cleaned_name = name.strip()
        if not cleaned_name:
            st.error("Enter a name for your custom ceremony.")
        else:
            ceremony = Ceremony(
                uuid4().hex,
                cleaned_name,
                "A family-defined ceremony; description and traditions are set by you.",
                is_custom=True,
            )
            st.session_state[CUSTOM_CEREMONIES_STATE_KEY] = [
                *_custom_ceremonies(),
                ceremony,
            ]
            st.rerun()

    for ceremony in _custom_ceremonies():
        st.markdown(f"**{ceremony.name}**")
        name_key = f"custom_ceremony_name_{ceremony.ceremony_id}"
        st.session_state.setdefault(name_key, ceremony.name)
        st.text_input("Rename ceremony", key=name_key, max_chars=80)
        save_column, delete_column = st.columns(2)
        with save_column:
            if st.button("Save name", key=f"save_custom_ceremony_{ceremony.ceremony_id}"):
                new_name = str(st.session_state.get(name_key, "")).strip()
                if new_name:
                    _save_custom_name(ceremony.ceremony_id, new_name)
                    st.rerun()
                else:
                    st.error("Enter a name for your custom ceremony.")
        with delete_column:
            if st.button("Delete", key=f"delete_custom_ceremony_{ceremony.ceremony_id}"):
                _delete_custom_ceremony(ceremony.ceremony_id)
                st.rerun()


def _confirmed_selection() -> list[Ceremony]:
    values = st.session_state.get(CEREMONIES_STATE_KEY, ())
    return [item for item in values if isinstance(item, Ceremony) and item.selected]


def _navigate_to_page(page: str) -> None:
    from ai_event_planning_platform.wedding_project_state import (
        navigate_project_to_page,
    )

    navigate_project_to_page(st.session_state, page)


def _render_confirmed_summary() -> None:
    st.subheader("Your confirmed ceremonies")
    selection = _confirmed_selection()
    if not selection:
        st.write("No ceremonies confirmed yet.")
        return
    for ceremony in selection:
        st.markdown(f"- **{ceremony.name}**: {ceremony.description}")


def render_saved_ceremony_summary() -> None:
    """Render the verified saved snapshot, independently of ceremony edits."""
    from ai_event_planning_platform.wedding_project_state import (
        saved_project_ceremonies,
    )

    st.subheader("Your Confirmed Wedding Ceremonies")
    try:
        saved = saved_project_ceremonies(st.session_state)
    except ValueError:
        st.error(
            "Your saved ceremonies could not be loaded. Reopen this wedding "
            "project and try again."
        )
        return
    if not saved:
        st.write("No ceremonies selected yet.")
        return
    for ceremony in saved:
        with st.container(border=True):
            st.markdown(f"**{ceremony.name}**")
            st.caption(ceremony.description)


def _confirm_ceremonies(ceremonies: tuple[Ceremony, ...]) -> None:
    selected = _draft_selection(list(ceremonies))
    st.session_state[CEREMONIES_STATE_KEY] = selected
    st.session_state[CUSTOM_CEREMONIES_STATE_KEY] = [
        replace(
            ceremony,
            selected=any(item.ceremony_id == ceremony.ceremony_id for item in selected),
        )
        for ceremony in _custom_ceremonies()
    ]
    st.session_state[CEREMONIES_CONFIRMED_KEY] = True


def render_wedding_ceremonies() -> None:
    from ai_event_planning_platform.wedding_project_state import (
        ACTIVE_PROJECT_ID_KEY,
        PENDING_PLAN_SAVES_KEY,
        PROJECT_DIRTY_KEY,
        persist_active_project,
    )

    st.title("Choose Your Wedding Ceremonies")
    st.write(
        "Review optional ideas, choose only what feels right, and add ceremonies "
        "from any tradition or your own family. Nothing is required."
    )
    profile = migrate_wedding_profile(st.session_state.get(DRAFT_STATE_KEY))
    suggestions, clarification = recommend_ceremonies(profile)
    st.session_state.setdefault(CEREMONIES_STATE_KEY, [])
    st.session_state.setdefault(CUSTOM_CEREMONIES_STATE_KEY, [])
    st.session_state.setdefault(CEREMONIES_CONFIRMED_KEY, False)

    if clarification:
        st.info(clarification)
    if st.session_state[CEREMONIES_CONFIRMED_KEY]:
        st.caption(
            "Changes below remain drafts until you select Confirm My Ceremonies again."
        )

    st.subheader("Personalized suggestions")
    st.caption("Suggestions are optional. Religious ceremonies are never preselected.")
    suggested_ids = set()
    all_ceremonies: list[Ceremony] = []
    for suggestion in suggestions:
        ceremony = suggestion.ceremony
        suggested_ids.add(ceremony.ceremony_id)
        all_ceremonies.append(ceremony)
        _render_ceremony_option(ceremony, suggestion.reason)

    other_ceremonies = [
        ceremony
        for ceremony in CEREMONY_CATALOG
        if ceremony.ceremony_id not in suggested_ids
    ]
    if other_ceremonies:
        st.subheader("Other catalog ceremonies")
        st.caption("Browse any catalog option, including optional examples from other traditions.")
        for ceremony in other_ceremonies:
            all_ceremonies.append(ceremony)
            _render_ceremony_option(ceremony)

    _render_custom_ceremonies()
    all_ceremonies.extend(_custom_ceremonies())
    for ceremony in _custom_ceremonies():
        _render_ceremony_option(ceremony)

    pending_plans = bool(st.session_state.get(PENDING_PLAN_SAVES_KEY))
    if pending_plans:
        st.warning("Retry failed ceremony plan saves before changing confirmed ceremony selections.")
    planned_ids = {
        item.ceremony_id for item in st.session_state.get(CEREMONIES_SAVED_KEY, ())
        if item.plan is not None
    }
    if planned_ids - set(st.session_state.get(CEREMONIES_DRAFT_IDS_KEY, ())):
        st.warning(
            "Confirming this selection will also remove the saved plans for ceremonies "
            "you deselected. Re-select those ceremonies to keep their plans."
        )
    if st.button(
        "Confirm My Ceremonies", key="confirm_wedding_ceremonies", type="primary",
        disabled=pending_plans,
    ):
        _confirm_ceremonies(tuple(all_ceremonies))
        if isinstance(st.session_state.get(ACTIVE_PROJECT_ID_KEY), str):
            try:
                persist_active_project(
                    st.session_state,
                    selected_ceremonies=_confirmed_selection(),
                    planning_step="wedding_ceremonies",
                )
            except Exception:
                st.session_state[PROJECT_DIRTY_KEY] = True
                st.error(
                    "Your ceremony selections could not be saved to the database. "
                    "Your choices are still available in this session; retry Confirm."
                )
        else:
            st.session_state[CEREMONIES_SAVED_KEY] = list(_confirmed_selection())

    saved = st.session_state.get(CEREMONIES_SAVED_KEY)
    draft_changed = not _same_selection(
        _draft_selection(all_ceremonies), saved or []
    )
    if draft_changed:
        st.warning("You have unsaved ceremony changes. Select Confirm My Ceremonies to save them.")
    if st.session_state[CEREMONIES_CONFIRMED_KEY] and not draft_changed:
        if isinstance(st.session_state.get(ACTIVE_PROJECT_ID_KEY), str):
            if saved is None or st.session_state.get(PROJECT_DIRTY_KEY, False):
                st.warning("These ceremony choices have not yet been saved.")
            else:
                st.success("Your ceremony selections are saved to this wedding project.")
        else:
            st.success("Your ceremony selections are confirmed for this session.")
        st.info("Next: Plan each ceremony's guests, dates, venue and budget.")
    _render_confirmed_summary()

    st.button(
        "Plan Your Wedding Ceremonies", key="open_ceremony_planning",
        on_click=_navigate_to_page, args=("ceremony_planning",),
        disabled=not st.session_state.get(ACTIVE_PROJECT_ID_KEY)
        or not st.session_state.get(CEREMONIES_SAVED_KEY),
    )


def render_ceremony_entry_button(key: str) -> None:
    st.button(
        "Choose Your Wedding Ceremonies",
        key=key,
        on_click=_navigate_to_page,
        args=(WEDDING_CEREMONIES_PAGE,),
    )
