import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ai_event_planning_platform.invitation_wording import PHRASES, cached_wording, suggest_wording


def client_for(value):
    client = Mock()
    client.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(value)))])
    return client


def test_structured_wording_is_contextual_but_cannot_add_facts():
    client = client_for({"lines": [PHRASES[0], PHRASES[3]]})
    context = {"type": "bride_family", "tone": "Warm", "hosting_family": {"hosts": "Host entered by user"}, "ceremonies": [{"name": "Haldi"}]}
    message = suggest_wording(context, client=client)
    assert message == PHRASES[0] + "\n\n" + PHRASES[3]
    assert json.loads(client.chat.completions.create.call_args.kwargs["messages"][1]["content"]) == context
    assert "photo" not in client.chat.completions.create.call_args.kwargs["messages"][1]["content"]


@pytest.mark.parametrize("value", [{"lines": ["Invented family invites you at Grand Palace tomorrow."]},
    {"lines": []}, {"message": "Unexpected schema"}, {"lines": [PHRASES[0], PHRASES[0]]},
    {"lines": [PHRASES[0]], "date": "2027-11-01"}])
def test_malformed_or_invented_wording_is_rejected(value):
    with pytest.raises(ValueError, match="unavailable"):
        suggest_wording({}, client=client_for(value))


@pytest.mark.parametrize("failure", [False, True])
def test_successes_and_failures_are_cached_until_explicit_retry(monkeypatch, failure):
    client = client_for({"lines": [PHRASES[1]]})
    if failure:
        client.chat.completions.create.side_effect = TimeoutError("PRIVATE PROVIDER BODY")
    monkeypatch.setattr("ai_event_planning_platform.invitation_wording.create_groq_client", lambda: client)
    state = {}
    first = cached_wording(state, {"tone": "Elegant"})
    assert cached_wording(state, {"tone": "Elegant"}) == first
    assert client.chat.completions.create.call_count == 1
    assert "PRIVATE" not in str(first)
    cached_wording(state, {"tone": "Elegant"}, retry=True)
    assert client.chat.completions.create.call_count == 2
