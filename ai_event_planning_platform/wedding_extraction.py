from datetime import date
import json
import re
from dataclasses import dataclass
from typing import Any

from groq import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError

from ai_event_planning_platform.groq_client import (
    GROQ_MAX_COMPLETION_TOKENS,
    GROQ_MODEL,
    GroqConfigurationError,
    create_groq_client,
)
from ai_event_planning_platform.wedding_profile import (
    CulturalBackground,
    TraditionPreference,
    WeddingDateStatus,
    WeddingLocationPreference,
    WeddingProfile,
    WeddingStyle,
    validate_wedding_profile,
)


class WeddingExtractionError(RuntimeError):
    """A safe user-facing error raised when profile extraction cannot complete."""

    def __init__(
        self,
        message: str,
        *,
        diagnostics: "GroqErrorDiagnostics | None" = None,
    ) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics


@dataclass(frozen=True)
class GroqErrorDiagnostics:
    status_code: int | None = None
    error_type: str | None = None
    error_code: str | None = None
    parameter: str | None = None


_SAFE_DIAGNOSTIC_VALUE = re.compile(r"[A-Za-z0-9_.-]{1,64}")


def _safe_diagnostic_value(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not _SAFE_DIAGNOSTIC_VALUE.fullmatch(candidate):
        return None
    return candidate


def _groq_error_diagnostics(error: APIStatusError) -> GroqErrorDiagnostics:
    body = error.body
    error_fields = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error_fields, dict):
        error_fields = {}
    return GroqErrorDiagnostics(
        status_code=error.status_code,
        error_type=_safe_diagnostic_value(error_fields.get("type")),
        error_code=_safe_diagnostic_value(error_fields.get("code")),
        parameter=_safe_diagnostic_value(error_fields.get("param")),
    )


SYSTEM_PROMPT = """You extract wedding requirements from a user's description.
Return only a JSON object matching the requested fields. Extract only details stated
or unambiguously specified by the user. Never invent, assume, or fill missing values;
use null or an empty list for unknowns. Treat ceremonies as preferences, never as
confirmed bookings or scheduled events. Convert explicitly stated Indian lakh/crore
budgets to an integer INR amount (1 lakh = 100000 INR; 1 crore = 10000000 INR).

wedding_date_status must be fixed_date, approximate_date, or not_decided_yet.
wedding_date must be YYYY-MM-DD for a stated exact date, YYYY-MM for an approximate
month and year, or null. For a year/season without a month, use not_decided_yet and
null; preserve the stated detail in additional_preferences if useful.
location_preference must be local, destination, or null.
guest_count and budget_inr must be JSON integers or null.
style must be simple, traditional, luxury, modern, mixed, or null.
Each partner culture must be null or an object with background and region string/null.
traditions must be a list containing only religious, secular, and/or custom.
additional_preferences must be a concise string of other explicitly stated preferences
or null. Use null when a field is not mentioned.

Return exactly these keys:
{
  "wedding_date_status": "not_decided_yet",
  "wedding_date": null,
  "city": null,
  "state": null,
  "location_preference": null,
  "guest_count": null,
  "budget_inr": null,
  "style": null,
  "partner_1_culture": null,
  "partner_2_culture": null,
  "traditions": [],
  "additional_preferences": null
}"""


def _optional_text(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise WeddingExtractionError(
            f"Groq returned an invalid value for {field_name}. Please edit the description and try again."
        )
    cleaned = value.strip()
    if cleaned.casefold() in {"", "unknown", "not specified", "not provided", "null"}:
        return None
    return cleaned


def _enum_value(enum_type: type, value: Any, field_name: str) -> Any:
    if value is None:
        return None
    if not isinstance(value, str):
        raise WeddingExtractionError(
            f"Groq returned an invalid value for {field_name}. Please edit the description and try again."
        )
    normalized = value.strip().casefold().replace("-", "_").replace(" ", "_")
    try:
        return enum_type(normalized)
    except ValueError as error:
        raise WeddingExtractionError(
            f"Groq returned an unsupported value for {field_name}. Please review the description and try again."
        ) from error


def _parse_date(status: WeddingDateStatus, value: Any) -> date | None:
    if status is WeddingDateStatus.NOT_DECIDED_YET:
        if value not in (None, ""):
            raise WeddingExtractionError(
                "Groq returned a date without a matching date status. Please try again."
            )
        return None
    if not isinstance(value, str):
        raise WeddingExtractionError(
            "Groq did not return a valid wedding date. Please review the description and try again."
        )
    if status is WeddingDateStatus.APPROXIMATE_DATE:
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value):
            raise WeddingExtractionError(
                "Groq returned an invalid approximate month. Please review the description and try again."
            )
        try:
            return date.fromisoformat(f"{value}-01")
        except ValueError as error:
            raise WeddingExtractionError(
                "Groq returned an invalid approximate month. Please review the description and try again."
            ) from error
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise WeddingExtractionError(
            "Groq returned an invalid fixed date. Please review the description and try again."
        )
    try:
        parsed_date = date.fromisoformat(value)
    except ValueError as error:
        raise WeddingExtractionError(
            "Groq returned an invalid fixed date. Please review the description and try again."
        ) from error
    if parsed_date.isoformat() != value:
        raise WeddingExtractionError(
            "Groq returned an invalid fixed date. Please review the description and try again."
        )
    return parsed_date


def _parse_integer(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int:
        raise WeddingExtractionError(
            f"Groq returned an invalid {field_name}. Please review the description and try again."
        )
    return value


def _parse_culture(value: Any, field_name: str) -> CulturalBackground | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise WeddingExtractionError(
            f"Groq returned an invalid {field_name}. Please try again."
        )
    unknown_keys = set(value) - {"background", "region"}
    if unknown_keys:
        raise WeddingExtractionError(
            f"Groq returned an invalid {field_name}. Please try again."
        )
    background = _optional_text(value.get("background"), f"{field_name} background")
    region = _optional_text(value.get("region"), f"{field_name} region")
    if background is None and region is None:
        return None
    return CulturalBackground(background=background, region=region)


def parse_wedding_profile_json(content: str) -> WeddingProfile:
    try:
        payload = json.loads(content)
    except (json.JSONDecodeError, TypeError) as error:
        raise WeddingExtractionError(
            "Groq returned unreadable data. Please try the extraction again."
        ) from error
    if not isinstance(payload, dict):
        raise WeddingExtractionError(
            "Groq returned an invalid profile. Please try the extraction again."
        )

    date_status = _enum_value(
        WeddingDateStatus,
        payload.get("wedding_date_status", WeddingDateStatus.NOT_DECIDED_YET.value),
        "wedding date status",
    )
    if date_status is None:
        raise WeddingExtractionError(
            "Groq did not return a wedding date status. Please try again."
        )
    wedding_date = _parse_date(date_status, payload.get("wedding_date"))

    location = _enum_value(
        WeddingLocationPreference,
        payload.get("location_preference"),
        "location preference",
    )
    style = _enum_value(WeddingStyle, payload.get("style"), "wedding style")

    traditions_value = payload.get("traditions")
    if traditions_value is None:
        traditions_value = []
    if not isinstance(traditions_value, list):
        raise WeddingExtractionError(
            "Groq returned invalid tradition preferences. Please try again."
        )
    traditions = tuple(
        _enum_value(TraditionPreference, item, "tradition preference")
        for item in traditions_value
    )

    profile = WeddingProfile(
        wedding_date_status=date_status,
        wedding_date=wedding_date,
        city=_optional_text(payload.get("city"), "city"),
        state=_optional_text(payload.get("state"), "state"),
        location_preference=location,
        guest_count=_parse_integer(payload.get("guest_count"), "guest count"),
        budget_inr=_parse_integer(payload.get("budget_inr"), "INR budget"),
        style=style,
        partner_1_culture=_parse_culture(
            payload.get("partner_1_culture"), "Partner 1 cultural background"
        ),
        partner_2_culture=_parse_culture(
            payload.get("partner_2_culture"), "Partner 2 cultural background"
        ),
        traditions=traditions,
        additional_preferences=_optional_text(
            payload.get("additional_preferences"), "additional preferences"
        ),
    )
    errors = validate_wedding_profile(profile)
    if errors:
        raise WeddingExtractionError(
            "Groq returned details that failed validation. Review the description and try again."
        )
    return profile


def extract_wedding_profile(
    description: str, *, client: Any | None = None
) -> WeddingProfile:
    if not isinstance(description, str) or not description.strip():
        raise WeddingExtractionError("Enter a wedding description before extracting details.")

    try:
        groq_client = client if client is not None else create_groq_client()
        response = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": description.strip()},
            ],
            response_format={"type": "json_object"},
            max_completion_tokens=GROQ_MAX_COMPLETION_TOKENS,
        )
    except GroqConfigurationError as error:
        raise WeddingExtractionError(str(error)) from None
    except APITimeoutError:
        raise WeddingExtractionError(
            "Groq took too long to respond. Your description is preserved; please try again."
        ) from None
    except RateLimitError:
        raise WeddingExtractionError(
            "Groq is temporarily rate-limited. Your description is preserved; please try again later."
        ) from None
    except APIConnectionError:
        raise WeddingExtractionError(
            "Could not connect to Groq. Your description is preserved; check your connection and try again."
        ) from None
    except APIStatusError as error:
        raise WeddingExtractionError(
            "Groq could not process the request. Your description is preserved; please try again.",
            diagnostics=_groq_error_diagnostics(error),
        ) from None
    except Exception:
        raise WeddingExtractionError(
            "Wedding details could not be extracted. Your description is preserved; please try again."
        ) from None

    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError, TypeError):
        raise WeddingExtractionError(
            "Groq returned an incomplete response. Your description is preserved; please try again."
        ) from None
    if not isinstance(content, str) or not content.strip():
        raise WeddingExtractionError(
            "Groq returned an empty response. Your description is preserved; please try again."
        )
    return parse_wedding_profile_json(content)