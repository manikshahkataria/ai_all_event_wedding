from dataclasses import replace
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ai_event_planning_platform.budget_engine import infer_ceremony_needs
from ai_event_planning_platform.budget_intelligence import (
    BudgetIntelligenceError, cached_budget_needs, interpret_budget_needs,
)
from test_budget_engine import ceremony, profile


def response(payload):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))])


def payload():
    return {"ceremonies": [{"ceremony_id": "haldi", "catering": "breakfast",
        "services": ["catering", "seating"], "explanation": "Consider breakfast at home; reduce optional decoration to preserve the budget."}]}


def test_groq_receives_full_context_but_only_interprets_needs():
    p, c = profile(city="Pune", additional_preferences="Vegetarian breakfast"), ceremony(budget=50000)
    client = Mock()
    client.chat.completions.create.return_value = response(payload())
    needs = interpret_budget_needs(p, [c], client=client)
    request = client.chat.completions.create.call_args.kwargs
    context = json.loads(request["messages"][1]["content"])
    assert context["profile"]["city"] == "Pune"
    assert context["profile"]["additional_preferences"] == "Vegetarian breakfast"
    assert context["ceremonies"][0]["plan"]["estimated_budget_inr"] == 50000
    assert request["response_format"] == {"type": "json_object"}
    assert "Never invent quotations" in request["messages"][0]["content"]
    assert needs[0].source == "groq" and not needs[0].reviewed
    assert needs[0].guest_count == 50 and needs[0].venue == "home"
    assert c.plan.estimated_budget_inr == 50000
    client.chat.completions.create.assert_called_once()


@pytest.mark.parametrize("kind", ["price", "missing", "duplicate", "unknown_id", "service", "type"])
def test_invalid_ai_json_is_rejected(kind):
    raw = payload()
    item = raw["ceremonies"][0]
    if kind == "price":
        item["vendor_price"] = 100
    elif kind == "missing":
        raw["ceremonies"] = []
    elif kind == "duplicate":
        raw["ceremonies"].append(dict(item))
    elif kind == "unknown_id":
        item["ceremony_id"] = "invented"
    elif kind == "service":
        item["services"] = ["invented"]
    else:
        item["services"] = "catering"
    client = Mock()
    client.chat.completions.create.return_value = response(raw)
    with pytest.raises(BudgetIntelligenceError):
        interpret_budget_needs(profile(), [ceremony()], client=client)


def test_api_failure_is_safe_and_does_not_repeat_until_explicit_retry(monkeypatch):
    create = Mock(side_effect=RuntimeError("secret-provider-body"))
    monkeypatch.setattr("ai_event_planning_platform.budget_intelligence.create_groq_client", create)
    p, ceremonies, state = profile(), [ceremony()], {}
    first = cached_budget_needs(state, p, ceremonies)
    assert first[0] == infer_ceremony_needs(p, ceremonies)
    assert "secret-provider-body" not in first[1]
    assert cached_budget_needs(state, p, ceremonies) == first
    assert create.call_count == 1
    cached_budget_needs(state, p, ceremonies, retry=True)
    assert create.call_count == 2


def test_success_cache_is_context_sensitive_and_does_not_promote_suggestions(monkeypatch):
    client = Mock()
    client.chat.completions.create.return_value = response(payload())
    monkeypatch.setattr("ai_event_planning_platform.budget_intelligence.create_groq_client", lambda: client)
    p, c, state = profile(), ceremony(), {}
    first = cached_budget_needs(state, p, [c])
    second = cached_budget_needs(state, p, [c])
    assert first == second and first[1] is None
    assert not first[0][0].reviewed
    assert client.chat.completions.create.call_count == 1
    cached_budget_needs(state, replace(p, city="Delhi"), [c])
    assert client.chat.completions.create.call_count == 2
