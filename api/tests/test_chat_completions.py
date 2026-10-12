# Standalone, offline: every OpenAI-compatible provider is called over Chat Completions with the whole conversation, the
# concept call's structured output, and a turn's cost only when the provider sends one. A fake provider stands behind the
# standalone mode's transports. Run: `uv run pytest` in api/.
import asyncio
import json

import httpx2 as httpx
import pytest
from test_gateway import PLAN, FakeProxy, completion, content_of, proxy, text  # noqa: F401  (gateway mode is chosen at import there)
from test_problems import chat, standalone, start_standalone  # noqa: F401  (standalone is a fixture)

import concepts
import designer as designer_module
import images
import modes

NO_IMAGES = images.ImageSettings(type="", model="", endpoint="", api_key="", size="1024x1536", quality="medium", timeout_seconds=30)
LOCAL = {"level": None, "dailyUsd": None, "remainingUsd": None, "unlimited": True, "resetsAtUtc": None}


def local_calls(endpoint: str, fake: FakeProxy, effort: str = "") -> modes.LocalModelCalls:
    settings = designer_module.Settings(type="OpenAI", model="chat-model", endpoint=endpoint, api_key="sk-test-key", timeout_seconds=30,
                                        concept_model="concept-model", concept_reasoning_effort=effort)
    calls = modes.LocalModelCalls(settings, NO_IMAGES)
    calls.transport = httpx.MockTransport(fake.answer)
    return calls


@pytest.mark.parametrize("endpoint,url", [
    ("", "https://api.openai.com/v1/chat/completions"),
    ("https://my-resource.openai.azure.com/openai/v1/", "https://my-resource.openai.azure.com/openai/v1/chat/completions"),
    ("https://openrouter.ai/api/v1", "https://openrouter.ai/api/v1/chat/completions"),
])
def test_every_openai_compatible_provider_is_called_over_chat_completions(endpoint, url):
    from agent_framework import Message

    fake = FakeProxy()
    calls = local_calls(endpoint, fake)
    asyncio.run(calls.chat_client("chat-model").get_response([Message(role="user", contents=["Hello."])]))
    (r,) = fake.requests
    assert str(r.url) == url
    body = json.loads(r.content)
    assert body["model"] == "chat-model" and body["messages"][-1]["role"] == "user"
    assert "previous_response_id" not in body and "input" not in body
    assert r.headers["authorization"] == "Bearer sk-test-key"


def test_the_concept_call_asks_for_the_plan_as_a_json_schema_and_parses_it():
    fake = FakeProxy()
    calls = local_calls("https://openrouter.ai/api/v1", fake, effort="low")
    plan, tokens_in, tokens_out = asyncio.run(
        concepts.generate(calls.concept_client(), calls.concept_model, "interpretive", "the brief", 30, calls.concept_reasoning_effort))
    assert isinstance(plan, concepts.ConceptPlan)
    assert [c.title for c in plan.concepts] == ["Idea 0", "Idea 1", "Idea 2"] and plan.chosen == 0 and plan.prompt == PLAN["prompt"]
    assert (tokens_in, tokens_out) == (100, 20)
    (r,) = fake.requests
    body = json.loads(r.content)
    assert str(r.url) == "https://openrouter.ai/api/v1/chat/completions" and body["model"] == "concept-model"
    assert body["response_format"]["type"] == "json_schema" and body["response_format"]["json_schema"]["name"] == "ConceptPlan"
    assert set(body["response_format"]["json_schema"]["schema"]["properties"]) == set(PLAN)
    assert body["reasoning_effort"] == "low"
    assert "reasoning" not in body and "text" not in body  # the Responses API's fields
    # Without a setting, no effort is sent.
    calls = local_calls("", fake)
    asyncio.run(concepts.generate(calls.concept_client(), calls.concept_model, "interpretive", "the brief", 30, calls.concept_reasoning_effort))
    assert "reasoning_effort" not in json.loads(fake.requests[-1].content)


def test_a_standalone_conversation_sends_the_whole_history_each_turn(standalone):
    s = start_standalone()
    chat(s["id"], "A stormy mood, please.")
    chat(s["id"], "Paint a cover.")
    chat(s["id"], "Change the font.")
    requests = [json.loads(r.content) for r in standalone.of("chat")]
    assert all(str(r.url) == "https://openrouter.ai/api/v1/chat/completions" for r in standalone.of("chat") + standalone.of("intent"))
    assert [m["role"] for m in requests[1]["messages"]] == ["system", "user", "assistant", "user"]
    # The last turn calls a tool: its second request carries the call and its result after everything before.
    assert [m["role"] for m in requests[-1]["messages"]] == [
        "system", "user", "assistant", "user", "assistant", "user", "assistant", "tool", "assistant", "user", "assistant", "tool"]
    assert "A stormy mood, please." in content_of(requests[-1]["messages"][3])
    for body in requests:
        assert "previous_response_id" not in body and "store" not in body
    # The intent calls are apart from the conversation: one message each, never kept in the history.
    assert len(standalone.of("intent")) == 3
    for r in standalone.of("intent"):
        assert [m["role"] for m in json.loads(r.content)["messages"]] == ["user"]
    assert "their exact words" not in json.dumps(requests[-1]["messages"])


def test_standalone_cost_is_the_providers_own_when_it_sends_one(standalone):
    standalone.usage = {"chat": {"cost": 0.00025}, "intent": {"cost": 0.00005}}
    s = start_standalone()
    assert s["messages"][-1]["usage"] == {"inputTokens": 100, "outputTokens": 20, "costUsd": 0.00025}
    assert s["budget"] == LOCAL  # no allowance standalone, whatever a turn cost
    # The intent call and one chat call: summed.
    body = chat(s["id"], "Hello.")["data"]["chat"]
    assert body["messages"][-1]["usage"] == {"inputTokens": 200, "outputTokens": 40, "costUsd": 0.0003}
    assert body["budget"] == LOCAL
    # A paint: the concept call and the image come without a cost here, so the turn has none. Never a partial sum.
    body = chat(s["id"], "Paint a cover.")["data"]["chat"]
    assert [v["n"] for v in body["versions"]] == [1]
    assert body["messages"][-1]["usage"]["costUsd"] is None
    assert body["messages"][-1]["usage"]["inputTokens"] == 400
    # A type change: the intent call and two chat calls (the tool call, then the answer). Its legibility check calls no model.
    body = chat(s["id"], "Change the font.")["data"]["chat"]
    assert body["messages"][-1]["usage"] == {"inputTokens": 300, "outputTokens": 60, "costUsd": pytest.approx(0.00055)}


def test_standalone_without_a_provider_cost_shows_tokens_only(standalone):
    # Not estimated, and a site's cost header means nothing standalone.
    standalone.headers = {"chat": {modes.CALL_COST_HEADER: "0.5"}}
    s = start_standalone()
    assert s["messages"][-1]["usage"] == {"inputTokens": 100, "outputTokens": 20, "costUsd": None}
    body = chat(s["id"], "Hello.")["data"]["chat"]
    assert body["messages"][-1]["usage"]["costUsd"] is None and body["budget"] == LOCAL
    # A cost that isn't an amount is no cost.
    standalone.usage = {"chat": {"cost": "free"}}
    assert chat(s["id"], "Hello.")["data"]["chat"]["messages"][-1]["usage"]["costUsd"] is None
