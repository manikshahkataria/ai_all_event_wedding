from unittest.mock import Mock, call, patch

import pytest
from streamlit.errors import StreamlitSecretNotFoundError
from supabase.lib.client_options import SyncClientOptions

from ai_event_planning_platform.config import (
    SupabaseConfigurationError,
    SupabaseSettings,
    load_supabase_settings,
)
from ai_event_planning_platform import supabase_client


def test_load_supabase_settings_reads_streamlit_secrets() -> None:
    configured_secrets = {
        "SUPABASE_URL": "https://project.example.supabase.co",
        "SUPABASE_PUBLISHABLE_KEY": "placeholder-publishable-key",
    }
    with patch(
        "ai_event_planning_platform.config._read_streamlit_secrets",
        return_value=configured_secrets,
    ):
        settings = load_supabase_settings()

    assert settings == SupabaseSettings(
        url="https://project.example.supabase.co",
        publishable_key="placeholder-publishable-key",
    )


@pytest.mark.parametrize(
    ("secrets", "missing_setting"),
    [
        ({"SUPABASE_PUBLISHABLE_KEY": "placeholder-key"}, "SUPABASE_URL"),
        ({"SUPABASE_URL": "https://project.example.supabase.co"}, "SUPABASE_PUBLISHABLE_KEY"),
        ({}, "SUPABASE_URL"),
    ],
)
def test_missing_supabase_settings_raise_clear_error(
    secrets: dict[str, str], missing_setting: str
) -> None:
    with (
        patch(
            "ai_event_planning_platform.config._read_streamlit_secrets",
            return_value=secrets,
        ),
        pytest.raises(SupabaseConfigurationError, match=missing_setting),
    ):
        load_supabase_settings()


def test_missing_secrets_file_raises_setup_error() -> None:
    with (
        patch(
            "ai_event_planning_platform.config._read_streamlit_secrets",
            side_effect=StreamlitSecretNotFoundError("No local secrets file"),
        ),
        pytest.raises(SupabaseConfigurationError, match="secrets.toml.example"),
    ):
        load_supabase_settings()


def test_client_factory_creates_independent_clients() -> None:
    settings = SupabaseSettings(
        url="https://project.example.supabase.co",
        publishable_key="placeholder-publishable-key",
    )
    first_client = Mock(name="first_client")
    second_client = Mock(name="second_client")

    with (
        patch.object(
            supabase_client, "load_supabase_settings", return_value=settings
        ),
        patch.object(
            supabase_client,
            "create_client",
            side_effect=[first_client, second_client],
        ) as create_client,
    ):
        first_result = supabase_client.create_supabase_client()
        second_result = supabase_client.create_supabase_client()

    assert first_result is first_client
    assert second_result is second_client
    assert first_result is not second_result
    assert create_client.call_count == 2
    for client_call in create_client.call_args_list:
        assert client_call.args == (settings.url, settings.publishable_key)
        options = client_call.kwargs["options"]
        assert isinstance(options, SyncClientOptions)
        assert options.auto_refresh_token is False
        assert options.persist_session is False