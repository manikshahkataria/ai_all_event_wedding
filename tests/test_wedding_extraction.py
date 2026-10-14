import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
import pytest
from groq import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError
from groq import Groq

from ai_event_planning_platform.groq_client import (
    GROQ_BASE_URL,
    GROQ_MAX_COMPLETION_TOKENS,
    GROQ_MODEL,
    GroqConfigurationError,
    create_groq_client,
    load_groq_api_key,
)
from ai_event_planning_platform.wedding_extraction import (
    GroqErrorDiagnostics,
    WeddingExtractionError,
    extract_wedding_profile,
    parse_wedding_profile_json,
)
from ai_event_planning_platform.wedding_profile import (
    CulturalBackground,
    TraditionPreference,
    WeddingDateStatus,
    WeddingLocationPreference,
    WeddingProfile,
    WeddingStyle,
)


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "wedding_date_status": "not_decided_yet",
        "wedding_date": None,
        "city": None,
        "state": None,
        "location_preference": None,
        "guest_count": None,
        "budget_inr": None,
        "style": None,
        "partner_1_culture": None,
        "partner_2_culture": None,
        "traditions": [],
        "additional_preferences": None,
    }
    payload.update(overrides)
    return payload


def _mock_client(content: str) -> Mock:
    client = Mock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )
    return client


def test_groq_api_key_loads_from_secrets_or_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "environment-test-key")
    with patch("ai_event_planning_platform.groq_client.st", SimpleNamespace(secrets={})):
        assert load_groq_api_key() == "environment-test-key"

    with patch(
        "ai_event_planning_platform.groq_client.st",
        SimpleNamespace(secrets={"GROQ_API_KEY": "streamlit-test-key"}),
    ):
        assert load_groq_api_key() == "streamlit-test-key"


def test_missing_groq_key_has_safe_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with (
        patch("ai_event_planning_platform.groq_client.st", SimpleNamespace(secrets={})),
        pytest.raises(GroqConfigurationError, match="GROQ_API_KEY"),
    ):
        load_groq_api_key()


def test_client_uses_groq_endpoint_timeout_and_no_retries() -> None:
    with (
        patch(
            "ai_event_planning_platform.groq_client.load_groq_api_key",
            return_value="test-key",
        ),
        patch("ai_event_planning_platform.groq_client.Groq") as groq_constructor,
    ):
        create_groq_client()

    options = groq_constructor.call_args.kwargs
    assert options["api_key"] == "test-key"
    assert options["base_url"] == GROQ_BASE_URL
    assert options["max_retries"] == 0
    assert isinstance(options["timeout"], httpx.Timeout)
    assert options["timeout"].connect == 5.0
    assert options["timeout"].read == 25.0


def test_groq_sdk_constructs_the_expected_completion_url() -> None:
    captured_urls: list[str] = []

    def capture_request(request: httpx.Request) -> httpx.Response:
        captured_urls.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "id": "mock-completion",
                "object": "chat.completion",
                "created": 0,
                "model": GROQ_MODEL,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "{}"},
                        "finish_reason": "stop",
                    }
                ],
            },
            request=request,
        )

    http_client = httpx.Client(transport=httpx.MockTransport(capture_request))
    client = Groq(
        api_key="placeholder-test-key",
        base_url=GROQ_BASE_URL,
        http_client=http_client,
        max_retries=0,
    )
    try:
        client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": "test"}],
            response_format={"type": "json_object"},
            max_completion_tokens=8,
        )
    finally:
        client.close()

    assert captured_urls == [
        "https://api.groq.com/openai/v1/chat/completions"
    ]


def test_successful_extraction_uses_structured_json_and_requested_model() -> None:
    client = _mock_client(json.dumps(_payload(city="Jaipur")))
    with patch(
        "ai_event_planning_platform.wedding_extraction.create_groq_client",
        return_value=client,
    ):
        profile = extract_wedding_profile("We plan to marry in Jaipur.")

    assert profile.city == "Jaipur"
    call = client.chat.completions.create.call_args.kwargs
    assert call["model"] == "openai/gpt-oss-120b" == GROQ_MODEL
    assert call["response_format"] == {"type": "json_object"}
    assert call["max_completion_tokens"] == GROQ_MAX_COMPLETION_TOKENS
    assert client.chat.completions.create.call_count == 1


def test_partial_description_keeps_unknown_fields_empty() -> None:
    client = _mock_client(json.dumps(_payload(city="Bareilly")))
    with patch(
        "ai_event_planning_platform.wedding_extraction.create_groq_client",
        return_value=client,
    ):
        profile = extract_wedding_profile("We are planning in Bareilly.")

    assert profile.city == "Bareilly"
    assert profile.state is None
    assert profile.guest_count is None
    assert profile.budget_inr is None
    assert profile.style is None
    assert profile.partner_1_culture is None
    assert profile.traditions == ()


def test_indian_lakh_budget_is_mapped_as_integer_inr() -> None:
    client = _mock_client(json.dumps(_payload(budget_inr=1300000)))
    with patch(
        "ai_event_planning_platform.wedding_extraction.create_groq_client",
        return_value=client,
    ):
        profile = extract_wedding_profile("Our budget is around 13 lakh rupees.")

    assert profile.budget_inr == 1300000


@pytest.mark.parametrize(
    ("status", "date_value", "expected_status", "expected_date"),
    [
        ("fixed_date", "2027-11-20", WeddingDateStatus.FIXED_DATE, "2027-11-20"),
        (
            "approximate_date",
            "2027-11",
            WeddingDateStatus.APPROXIMATE_DATE,
            "2027-11-01",
        ),
    ],
)
def test_exact_and_approximate_dates_map_to_existing_status(
    status: str,
    date_value: str,
    expected_status: WeddingDateStatus,
    expected_date: str,
) -> None:
    profile = parse_wedding_profile_json(
        json.dumps(_payload(wedding_date_status=status, wedding_date=date_value))
    )

    assert profile.wedding_date_status is expected_status
    assert profile.wedding_date.isoformat() == expected_date


def test_mixed_cultural_description_maps_both_partners_and_traditions() -> None:
    client = _mock_client(
        json.dumps(
            _payload(
                partner_1_culture={"background": "Punjabi", "region": "Punjab"},
                partner_2_culture={"background": "Tamil", "region": "Tamil Nadu"},
                traditions=["religious", "custom"],
                additional_preferences="Blend both families' traditions.",
            )
        )
    )
    with patch(
        "ai_event_planning_platform.wedding_extraction.create_groq_client",
        return_value=client,
    ):
        profile = extract_wedding_profile(
            "We are Punjabi and Tamil and want religious ceremonies blended with customs."
        )

    assert profile.partner_1_culture == CulturalBackground("Punjabi", "Punjab")
    assert profile.partner_2_culture == CulturalBackground("Tamil", "Tamil Nadu")
    assert profile.traditions == (
        TraditionPreference.RELIGIOUS,
        TraditionPreference.CUSTOM,
    )


def test_malformed_json_is_rejected() -> None:
    with pytest.raises(WeddingExtractionError, match="unreadable data"):
        parse_wedding_profile_json("not-json")


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [("budget_inr", -1), ("guest_count", 0), ("style", "extravagant")],
)
def test_invalid_structured_values_are_rejected(field: str, invalid_value: object) -> None:
    with pytest.raises(WeddingExtractionError):
        parse_wedding_profile_json(json.dumps(_payload(**{field: invalid_value})))


def test_timeout_is_safe_and_does_not_retry() -> None:
    request = httpx.Request("POST", GROQ_BASE_URL)
    client = Mock()
    client.chat.completions.create.side_effect = APITimeoutError(request)
    with (
        patch(
            "ai_event_planning_platform.wedding_extraction.create_groq_client",
            return_value=client,
        ),
        pytest.raises(WeddingExtractionError, match="too long to respond"),
    ):
        extract_wedding_profile("A sample wedding description")

    client.chat.completions.create.assert_called_once()


def test_rate_limit_is_safe_and_does_not_retry() -> None:
    request = httpx.Request("POST", GROQ_BASE_URL)
    response = httpx.Response(429, request=request)
    client = Mock()
    client.chat.completions.create.side_effect = RateLimitError(
        "private rate detail", response=response, body=None
    )
    with (
        patch(
            "ai_event_planning_platform.wedding_extraction.create_groq_client",
            return_value=client,
        ),
        pytest.raises(WeddingExtractionError, match="rate-limited") as error,
    ):
        extract_wedding_profile("A sample wedding description")

    assert "private rate detail" not in str(error.value)
    client.chat.completions.create.assert_called_once()


def test_connection_and_api_failures_are_safe() -> None:
    request = httpx.Request("POST", GROQ_BASE_URL)
    client = Mock()
    client.chat.completions.create.side_effect = APIConnectionError(
        message="private network detail", request=request
    )
    with (
        patch(
            "ai_event_planning_platform.wedding_extraction.create_groq_client",
            return_value=client,
        ),
        pytest.raises(WeddingExtractionError, match="Could not connect to Groq") as error,
    ):
        extract_wedding_profile("A sample wedding description")
    assert "private network detail" not in str(error.value)

    client.chat.completions.create.side_effect = RuntimeError("private SDK detail")
    with (
        patch(
            "ai_event_planning_platform.wedding_extraction.create_groq_client",
            return_value=client,
        ),
        pytest.raises(WeddingExtractionError, match="could not be extracted") as error,
    ):
        extract_wedding_profile("A sample wedding description")
    assert "private SDK detail" not in str(error.value)


def test_api_status_failure_is_safe() -> None:
    request = httpx.Request("POST", GROQ_BASE_URL)
    response = httpx.Response(500, request=request)
    client = Mock()
    client.chat.completions.create.side_effect = APIStatusError(
        "private API response", response=response, body=None
    )
    with (
        patch(
            "ai_event_planning_platform.wedding_extraction.create_groq_client",
            return_value=client,
        ),
        pytest.raises(WeddingExtractionError, match="could not process") as error,
    ):
        extract_wedding_profile("A sample wedding description")

    assert "private API response" not in str(error.value)
    assert error.value.diagnostics == GroqErrorDiagnostics(
        status_code=500,
        error_type=None,
        error_code=None,
        parameter=None,
    )
    client.chat.completions.create.assert_called_once()


def test_api_status_error_exposes_only_sanitized_diagnostic_fields() -> None:
    request = httpx.Request("POST", GROQ_BASE_URL)
    response = httpx.Response(400, request=request)
    client = Mock()
    client.chat.completions.create.side_effect = APIStatusError(
        "private response text",
        response=response,
        body={
            "error": {
                "message": "private response body",
                "type": "invalid_request_error",
                "code": "invalid_parameter",
                "param": "max_completion_tokens",
            }
        },
    )

    with (
        patch(
            "ai_event_planning_platform.wedding_extraction.create_groq_client",
            return_value=client,
        ),
        pytest.raises(WeddingExtractionError) as error,
    ):
        extract_wedding_profile("A sample wedding description")

    assert error.value.diagnostics == GroqErrorDiagnostics(
        status_code=400,
        error_type="invalid_request_error",
        error_code="invalid_parameter",
        parameter="max_completion_tokens",
    )
    assert "private response" not in str(error.value)


def test_api_status_diagnostics_drop_untrusted_parameter_text() -> None:
    request = httpx.Request("POST", GROQ_BASE_URL)
    response = httpx.Response(400, request=request)
    client = Mock()
    client.chat.completions.create.side_effect = APIStatusError(
        "private detail",
        response=response,
        body={"error": {"param": "authorization: Bearer sensitive-token"}},
    )

    with (
        patch(
            "ai_event_planning_platform.wedding_extraction.create_groq_client",
            return_value=client,
        ),
        pytest.raises(WeddingExtractionError) as error,
    ):
        extract_wedding_profile("A sample wedding description")

    assert error.value.diagnostics is not None
    assert error.value.diagnostics.parameter is None
    assert "sensitive-token" not in str(error.value)


def test_invalid_description_does_not_call_api() -> None:
    client_factory = Mock()
    with (
        patch(
            "ai_event_planning_platform.wedding_extraction.create_groq_client",
            client_factory,
        ),
        pytest.raises(WeddingExtractionError, match="Enter a wedding description"),
    ):
        extract_wedding_profile(" ")

    client_factory.assert_not_called()