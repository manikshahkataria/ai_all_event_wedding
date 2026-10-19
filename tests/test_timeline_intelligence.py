from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ai_event_planning_platform.timeline_intelligence import (
    TimelineIntelligenceError, cached_timeline, interpret_timeline,
)
from ai_event_planning_platform.wedding_profile import WeddingDateStatus
from test_budget_engine import profile, reviewed_plan
from test_wedding_timeline import DAY, dated_ceremony


def payload():
    return {"ceremonies": [{"ceremony_id": "haldi", "activities": [{"title": "Review setup", "description": "Optional setup suggestion",
            "duration_minutes": 30, "kind": "setup"}]}], "tasks": [{"key": "venue", "title": "Review venue", "description": "Check availability",
            "ceremony_id": None, "phase": "Early planning", "days_before": 60, "relative_window": "2–3 months before", "priority": "high", "dependencies": []}]}


def client_for(value):
    client = Mock()
    client.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(value)))])
    return client


def test_full_saved_context_and_only_python_calculated_dates():
    p, cs = profile(wedding_date_status=WeddingDateStatus.FIXED_DATE, wedding_date=DAY, city="Pune", state="Maharashtra", additional_preferences="Small gathering"), [dated_ceremony()]
    budget = replace(reviewed_plan(p, cs), accepted=True)
    client = client_for(payload())
    plan = interpret_timeline(p, cs, budget, client=client)
    context = json.loads(client.chat.completions.create.call_args.kwargs["messages"][1]["content"])
    assert context["profile"]["city"] == "Pune" and context["approved_budget"]["accepted"]
    assert context["ceremonies"][0]["plan"]["ceremony_date"] == DAY.isoformat()
    assert plan.tasks[0].due_source == "calculated"
    assert plan.schedules[0].activities[0].source == "ai_suggestion"
    assert plan.schedules[0].start_time is None and not plan.accepted
    client.chat.completions.create.assert_called_once()


@pytest.mark.parametrize("mutate", [
    lambda v: v.update(confirmed_date="2027-11-01"),
    lambda v: v["ceremonies"][0].update(start_time="09:00"),
    lambda v: v["ceremonies"][0].update(ceremony_id="unselected"),
    lambda v: v["ceremonies"].clear(),
    lambda v: v["tasks"][0].update(dependencies=["missing"]),
    lambda v: v["tasks"][0].update(dependencies=["venue"]),
    lambda v: v["tasks"][0].update(days_before=True),
    lambda v: v["ceremonies"][0]["activities"][0].update(duration_minutes=-1),
    lambda v: v["tasks"][0].update(phase="Invented phase"),
])
def test_invalid_ai_json_never_becomes_a_schedule(mutate):
    value = deepcopy(payload())
    mutate(value)
    with pytest.raises(TimelineIntelligenceError):
        interpret_timeline(profile(), [dated_ceremony()], client=client_for(value))


@pytest.mark.parametrize("failure", ["timeout", "malformed", "none"])
def test_provider_failures_are_safe_and_fallback_is_cached(monkeypatch, failure):
    client = client_for(payload())
    if failure == "timeout":
        client.chat.completions.create.side_effect = TimeoutError("PRIVATE PROVIDER BODY")
    else:
        client.chat.completions.create.return_value.choices[0].message.content = "not JSON" if failure == "malformed" else None
    monkeypatch.setattr("ai_event_planning_platform.timeline_intelligence.create_groq_client", lambda: client)
    state, p, cs = {}, profile(), [dated_ceremony()]
    first, notice = cached_timeline(state, p, cs)
    assert notice and "PRIVATE" not in notice
    assert all(a.source == "fallback_suggestion" for a in first.schedules[0].activities)
    assert cached_timeline(state, p, cs)[0] == first
    assert client.chat.completions.create.call_count == 1
    cached_timeline(state, p, cs, retry=True)
    assert client.chat.completions.create.call_count == 2


def test_success_is_cached_per_context_until_explicit_retry(monkeypatch):
    client = client_for(payload())
    monkeypatch.setattr("ai_event_planning_platform.timeline_intelligence.create_groq_client", lambda: client)
    state, cs = {}, [dated_ceremony()]
    first, notice = cached_timeline(state, profile(), cs)
    assert notice is None and cached_timeline(state, profile(), cs)[0] == first
    assert client.chat.completions.create.call_count == 1
    cached_timeline(state, profile(city="Pune"), cs)
    assert client.chat.completions.create.call_count == 2
