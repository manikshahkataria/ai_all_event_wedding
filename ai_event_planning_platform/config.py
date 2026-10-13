from collections.abc import Mapping
from dataclasses import dataclass

import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError


class SupabaseConfigurationError(RuntimeError):
    """Raised when the required Supabase settings are unavailable."""


@dataclass(frozen=True)
class SupabaseSettings:
    url: str
    publishable_key: str


def _read_streamlit_secrets() -> Mapping[str, str]:
    return st.secrets


def load_supabase_settings() -> SupabaseSettings:
    try:
        secrets = _read_streamlit_secrets()
    except StreamlitSecretNotFoundError as error:
        raise SupabaseConfigurationError(
            "Supabase settings are unavailable. Create .streamlit/secrets.toml "
            "from .streamlit/secrets.toml.example and add SUPABASE_URL and "
            "SUPABASE_PUBLISHABLE_KEY."
        ) from error

    required_settings = ("SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY")
    missing_settings = [
        setting
        for setting in required_settings
        if not isinstance(secrets.get(setting), str)
        or not secrets.get(setting, "").strip()
    ]
    if missing_settings:
        missing = ", ".join(missing_settings)
        raise SupabaseConfigurationError(
            f"Missing required Supabase setting(s): {missing}. Add them to "
            ".streamlit/secrets.toml using .streamlit/secrets.toml.example."
        )

    return SupabaseSettings(
        url=secrets["SUPABASE_URL"].strip(),
        publishable_key=secrets["SUPABASE_PUBLISHABLE_KEY"].strip(),
    )