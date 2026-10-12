# Gateway mode, offline: no site, no network. A test signing key turns gateway mode on, tokens are minted here,
# and httpx MockTransports stand in for the site's LLM proxy (chat and concept calls on the async SDK clients, the image
# call on the sync one). Run: `uv run pytest` in api/.
import base64
import io
import json
import os
import time

import httpx2 as httpx  # the SDKs' HTTP stack (modes.py)
import jwt
import pytest
from PIL import Image

KEY = "test-signing-key-for-tests-only-0123456789"
BASE = "https://site.test/llm"
os.environ.update({
    "GATEWAY_SIGNING_KEY": KEY,
    "GATEWAY_LLM_BASE_URL": BASE,
    "GATEWAY_ISSUER": "",  # empty = the default issuer
    "GATEWAY_AUDIENCE": "",  # empty = covers
})
for name in ("LLM_TYPE", "LLM_MODEL", "LLM_API_KEY", "LLM_ENDPOINT", "IMAGE_TYPE", "IMAGE_MODEL", "IMAGE_API_KEY", "IMAGE_ENDPOINT"):
    os.environ.pop(name, None)

import main  # noqa: E402  (gateway mode is chosen at import, from the env above)
import modes  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

ISSUER = "https://api.agentbullpen.ai"


def token(sub="member-1", aud="covers", iss=ISSUER, alg="HS256", key=KEY, exp_in=1800, **extra) -> str:
    now = int(time.time())
    claims = {"sub": sub, "email": f"{sub}@example.test", "name": "Test Member", "aud": aud, "iss": iss, "iat": now, "nbf": now, "exp": now + exp_in, **extra}
    return jwt.encode(claims, key, algorithm=alg)


def png_b64() -> str:
    out = io.BytesIO()
    Image.new("RGB", (16, 24), (40, 80, 120)).save(out, format="PNG")
    return base64.b64encode(out.getvalue()).decode()


PLAN = {
    "concepts": [{"title": f"Idea {i}", "emblem": "a map", "composition": "centred", "medium": "oil", "palette": "sea blue", "reason": "fits"} for i in range(3)],
    "chosen": 0, "chosenReason": "clearest", "style": "Rich painted illustration.", "noirSense": None,
    "prompt": "USE: cover. SCENE: a beach. SUBJECT: a map. DETAILS: warm light.", "openQuestions": [],
}
USAGE = {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}
MODELS = {"providers": [
    {"name": "openai", "protocol": "openai", "models": [
        {"id": "gpt-4o-mini", "kind": "chat", "weight": 1.0},
        {"id": "gpt-6-luna", "kind": "chat", "displayName": "GPT-6 Luna", "weight": 0.8},
        {"id": "gpt-image-2.5-flare", "kind": "image"},
    ]},
    {"name": "anthropic", "protocol": "anthropic", "models": [{"id": "claude-haiku-5-5", "kind": "chat"}]},
]}


def completion(message: dict, model="gpt-4o-mini", usage: dict | None = None) -> dict:
    """A Chat Completions answer; `usage` adds to the token counts (a provider's own `cost`)."""
    return {"id": f"chatcmpl-{time.monotonic_ns()}", "object": "chat.completion", "created": 0, "model": model,
            "choices": [{"index": 0, "finish_reason": "tool_calls" if message.get("tool_calls") else "stop", "message": {"role": "assistant", **message}}],
            "usage": {**USAGE, **(usage or {})}}


def text(t: str) -> dict:
    return {"content": t}


PAINT_CALL = {"content": None, "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "paint_cover", "arguments": "{}"}}]}


def after_last_user(messages: list) -> list:
    """The messages of the turn in progress: everything after the author's latest message."""
    last = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=-1)
    return messages[last + 1:]


def content_of(message: dict) -> str:
    c = message.get("content")
    return c if isinstance(c, str) else json.dumps(c)


class FakeProxy:
    """The site's /llm proxy: records every request (URL and headers) and answers like the provider would, or refuses."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        # kind ("chat", "concept", "image", "models") → (status, body[, times[, headers]]); times = how many requests
        # get it (then they answer normally); absent = every one. A 429 without headers says Retry-After: 60.
        self.refuse: dict[str, tuple] = {}
        self.raise_for: dict[str, Exception] = {}  # kind → raised by the transport (a timeout, a dropped connection)
        self.headers: dict[str, dict] = {}  # kind → extra headers on its normal answers (X-Model-Fallback, X-Call-Cost-Usd)
        self.usage: dict[str, dict] = {}  # kind → added to its answers' usage (a provider's own `cost`)
        self.models = MODELS
        # The intent step's label for the next author messages: a fixed one, or None = by what the test messages say
        # (this stand-in model's own reading; the app has no such rule).
        self.intent: str | None = None
        self.intent_answer: str | None = None  # the raw text of the intent answer, to play an invalid one
        self.eager = False  # true: the designer tries to paint on every message, whatever it says
        self.font_args: dict = {"font": "playfair"}  # what the designer passes to set_typography
        self.font_calls = 1  # how many times in a turn it calls set_typography before it answers
        self.plate: bytes | None = None  # the PNG the image call paints; None = a tiny flat one

    def intent_for(self, request: httpx.Request) -> str:
        if self.intent:
            return self.intent
        said = json.dumps(json.loads(request.content)).split("their exact words")[-1]
        painted, typed = "Paint" in said, "font" in said
        return "both" if painted and typed else "picture" if painted else "type" if typed else "talk"

    def kind(self, request: httpx.Request) -> str:
        path = request.url.path
        if path.endswith("/models"):
            return "models"
        if path.endswith("/images/generations"):
            return "image"
        body = json.loads(request.content or b"{}")
        said = json.dumps(body.get("messages"))
        if path.endswith("/v1/messages"):
            return "anthropic-intent" if "their exact words" in said else "anthropic"
        fmt = body.get("response_format") or {}
        if fmt.get("type") == "json_schema":
            return "intent" if (fmt.get("json_schema") or {}).get("name") == "Intent" else "concept"
        return "chat"

    def answer(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        kind = self.kind(request)
        if kind in self.raise_for:
            raise self.raise_for[kind]
        if kind in self.refuse:
            status, body, *rest = self.refuse[kind]
            times = rest[0] if rest else None
            headers = rest[1] if len(rest) > 1 else ({"Retry-After": "60"} if status == 429 else {})
            if times is None or times > 0:
                if times is not None:
                    self.refuse[kind] = (status, body, times - 1, headers)
                return httpx.Response(status, json=body, headers=headers)
        extra = self.headers.get(kind, {})
        if kind == "models":
            return httpx.Response(200, json=self.models)
        if kind == "image":
            return httpx.Response(200, json={"created": 0, "data": [{"b64_json": base64.b64encode(self.plate).decode() if self.plate else png_b64()}], "usage": {"input_tokens": 30, "output_tokens": 40, "total_tokens": 70}}, headers=extra)
        if kind in ("anthropic", "anthropic-intent"):
            said = "Hello from Claude." if kind == "anthropic" else json.dumps({"intent": self.intent_for(request), "reason": "as asked"})
            return httpx.Response(200, json={"id": "msg_a", "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
                                             "content": [{"type": "text", "text": said}], "stop_reason": "end_turn",
                                             "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 5}})
        usage = self.usage.get(kind)
        if kind == "intent":
            answer = self.intent_answer or json.dumps({"intent": self.intent_for(request), "reason": "as asked"})
            return httpx.Response(200, json=completion(text(answer), usage=usage), headers=extra)
        if kind == "concept":
            return httpx.Response(200, json=completion(text(json.dumps(PLAN)), model="gpt-6-luna", usage=usage), headers=extra)
        body = json.loads(request.content)
        messages = body.get("messages") or []
        declared = {t["function"]["name"] for t in body.get("tools") or []}
        last_user = next((content_of(m) for m in reversed(messages) if m.get("role") == "user"), "")
        results = [m for m in after_last_user(messages) if m.get("role") == "tool"]
        if results and "font" in last_user and "set_typography" in declared and len(results) < self.font_calls:
            return httpx.Response(200, json=completion(self.font_call(), usage=usage), headers=extra)
        if results:
            return httpx.Response(200, json=completion(text("I painted it. What would you like to change?"), usage=usage), headers=extra)
        # It wants to paint whenever "Paint" is said, and to set the font whenever "font" is: what it may
        # do is up to the tools it was given.
        if ("Paint" in last_user or self.eager) and "paint_cover" in declared:
            return httpx.Response(200, json=completion(PAINT_CALL, usage=usage), headers=extra)
        if "font" in last_user and "set_typography" in declared:
            return httpx.Response(200, json=completion(self.font_call(), usage=usage), headers=extra)
        return httpx.Response(200, json=completion(text("Hello. What mood do you want?"), usage=usage), headers=extra)

    def font_call(self) -> dict:
        call = {"id": f"call_{time.monotonic_ns()}", "type": "function", "function": {"name": "set_typography", "arguments": json.dumps(self.font_args)}}
        return {"content": None, "tool_calls": [call]}

    def of(self, kind: str) -> list[httpx.Request]:
        return [r for r in self.requests if self.kind(r) == kind]


@pytest.fixture
def proxy():
    fake = FakeProxy()
    calls = main.mode.calls
    calls.transport = httpx.MockTransport(fake.answer)
    calls.sync_transport = httpx.MockTransport(fake.answer)
    main.mode.catalog.transport = httpx.MockTransport(fake.answer)
    main.mode.catalog._cache = None
    main.designer._agents.clear()
    main.designer._clients.clear()
    main.designer._concept_client = None
    return fake


client = TestClient(main.app)
FIELDS = "id model messages { role text usage { inputTokens outputTokens costUsd } } versions { n kind from } budget { level dailyUsd remainingUsd unlimited resetsAtUtc }"


def gql(query: str, variables: dict | None = None, tok: str | None = None, headers: dict | None = None) -> httpx.Response:
    h = {"Authorization": f"Bearer {tok}"} if tok else {}
    return client.post("/graphql", json={"query": query, "variables": variables or {}}, headers={**h, **(headers or {})})


def start(tok: str, headers: dict | None = None) -> dict:
    r = gql(f'mutation {{ startSession(book: {{title: "Treasure Island", author: "Robert Louis Stevenson"}}) {{ {FIELDS} }} }}', tok=tok, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["data"]["startSession"]


def chat(session_id: str, message: str, tok: str, model: str | None = None, headers: dict | None = None) -> dict:
    return gql(f"mutation($id: ID!, $m: String!, $model: String) {{ chat(sessionId: $id, message: $m, model: $model) {{ {FIELDS} }} }}",
               {"id": session_id, "m": message, "model": model}, tok=tok, headers=headers).json()


def code(body: dict) -> str | None:
    return (body.get("errors") or [{}])[0].get("extensions", {}).get("code")


# --- 1. The current user -------------------------------------------------------------------------------------------

def test_gateway_mode_is_on():
    assert main.mode.gateway and isinstance(main.mode.users, modes.GatewayUsers)


def test_only_a_valid_gateway_token_gets_in(proxy):
    q = "{ budget { unlimited } }"
    assert gql(q, tok=token()).status_code == 200
    assert gql(q).status_code == 401  # no token
    assert gql(q).headers["content-type"] == "application/problem+json"
    assert gql(q, tok=token(aud="temporal")).status_code == 401
    assert gql(q, tok=token(iss="https://evil.test")).status_code == 401
    assert gql(q, tok=token(alg="HS384")).status_code == 401
    assert gql(q, tok=jwt.encode({"sub": "x", "aud": "covers", "iss": ISSUER, "exp": int(time.time()) + 600}, key=None, algorithm="none")).status_code == 401
    assert gql(q, tok=token(exp_in=-31)).status_code == 401  # expired beyond the 30 s leeway
    assert gql(q, tok=token(exp_in=-10)).status_code == 200  # within the leeway
    assert gql(q, tok=token(key="another-key-another-key-another-key")).status_code == 401
    assert client.get("/download/x/1").status_code == 401
    assert proxy.requests == []  # nothing reached the proxy


def test_health_is_anonymous():
    r = client.get("/health")
    assert r.status_code == 200 and r.text == "Healthy"


# --- 2. Model calls through the proxy with the request's JWT ------------------------------------------------------

def test_chat_concept_and_image_go_to_the_proxy_with_the_requests_jwt(proxy):
    tok = token()
    s = start(tok)
    body = chat(s["id"], "Paint a cover.", tok)
    assert "errors" not in body, body
    assert [v["n"] for v in body["data"]["chat"]["versions"]] == [1]
    chats, concept, image = proxy.of("chat"), proxy.of("concept"), proxy.of("image")
    assert len(chats) == 3 and len(concept) == 1 and len(image) == 1  # opening, paint call, answer; one concept; one image
    for r in proxy.requests:
        if proxy.kind(r) == "models":
            continue
        assert str(r.url).startswith(f"{BASE}/openai/v1/"), r.url
        assert r.headers["authorization"] == f"Bearer {tok}"
        assert r.headers[modes.CONVERSATION_HEADER] == s["id"]
        assert modes.PLACEHOLDER_KEY not in r.headers["authorization"]
    assert {str(r.url) for r in chats + concept} == {f"{BASE}/openai/v1/chat/completions"}
    assert json.loads(concept[0].content)["model"] == "gpt-6-luna"
    assert str(image[0].url) == f"{BASE}/openai/v1/images/generations" and json.loads(image[0].content)["model"] == "gpt-image-2.5-flare"
    assert json.loads(chats[0].content)["model"] == "gpt-4o-mini"


def test_an_anthropic_chat_model_gets_the_jwt_as_x_api_key(proxy):
    tok = token()
    s = start(tok)
    body = chat(s["id"], "Hello there.", tok, model="anthropic:claude-haiku-5-5")
    assert "errors" not in body, body
    assert body["data"]["chat"]["model"] == "anthropic:claude-haiku-5-5"
    (r,) = proxy.of("anthropic")
    # MAF's Anthropic client uses the beta Messages API (?beta=true); the path is the one the proxy forwards.
    assert f"{r.url.scheme}://{r.url.host}{r.url.path}" == f"{BASE}/anthropic/v1/messages"
    assert r.headers["x-api-key"] == tok and "authorization" not in r.headers
    assert r.headers[modes.CONVERSATION_HEADER] == s["id"]
    assert proxy.of("models")[0].headers["authorization"] == f"Bearer {tok}"  # the catalog asks with the member's token


def test_two_requests_with_two_tokens_on_one_session_use_their_own(proxy):
    first, second = token(jti="one"), token(jti="two")
    assert first != second
    s = start(first)
    n = len(proxy.requests)
    chat(s["id"], "Hello again.", second)
    assert {r.headers["authorization"] for r in proxy.requests[:n]} == {f"Bearer {first}"}
    assert {r.headers["authorization"] for r in proxy.requests[n:]} == {f"Bearer {second}"}


def test_no_token_fails_before_anything_is_sent():
    sent = []
    http = modes.async_http_client(modes.CallPolicy("chat", gateway=True), transport=httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(200)))
    import asyncio

    async def call():
        async with http:
            await http.post(f"{BASE}/openai/v1/chat/completions", json={})

    state = modes.RequestState(token=None)
    ref = modes.begin(state)
    try:
        with pytest.raises(modes.GatewayTokenMissing):
            asyncio.run(call())
    finally:
        modes.end(ref)
    assert sent == [] and state.refusal.slug == "unauthorized"


# --- 3. The catalog -----------------------------------------------------------------------------------------------

def test_the_catalog_keeps_chat_models_with_provider_ids_and_labels(proxy):
    body = gql("{ chatModels { id displayName provider isDefault } }", tok=token()).json()["data"]["chatModels"]
    assert body == [
        {"id": "openai:gpt-4o-mini", "displayName": "openai:gpt-4o-mini", "provider": "openai", "isDefault": True},
        {"id": "openai:gpt-6-luna", "displayName": "GPT-6 Luna", "provider": "openai", "isDefault": False},
        {"id": "anthropic:claude-haiku-5-5", "displayName": "anthropic:claude-haiku-5-5", "provider": "anthropic", "isDefault": False},
    ]
    gql("{ chatModels { id } }", tok=token())
    assert len(proxy.of("models")) == 1  # cached for 60 s


def test_a_model_not_in_the_catalog_is_unknown(proxy):
    tok = token()
    s = start(tok)
    assert code(chat(s["id"], "Hello.", tok, model="openai:gpt-9")) == "UNKNOWN_MODEL"


# --- 4. Refusals --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("status,title,expected", [
    (403, "Daily allowance used up", "BUDGET_EXHAUSTED"),
    (403, "Model not allowed", "UNKNOWN_MODEL"),
    (403, "Forbidden", "BUDGET_EXHAUSTED"),
    (401, "Unauthorized", "UNAUTHORIZED"),
    (429, "Too Many Requests", "RATE_LIMITED"),
])
def test_the_proxys_refusals_become_codes_without_provider_text(proxy, status, title, expected):
    tok = token()
    s = start(tok)
    proxy.refuse["chat"] = (status, {"title": title, "detail": "SECRET-PROVIDER-TEXT"})
    n = len(proxy.of("chat"))
    body = chat(s["id"], "Hello.", tok)
    assert code(body) == expected
    assert "SECRET-PROVIDER-TEXT" not in json.dumps(body)
    assert len(proxy.of("chat")) == n + 1  # sent once: never retried


@pytest.mark.parametrize("kind", ["concept", "image"])
def test_a_refusal_inside_paint_cover_ends_the_turn_with_its_code(proxy, kind):
    tok = token()
    s = start(tok)
    proxy.refuse[kind] = (403, {"title": "Daily allowance used up"})
    body = chat(s["id"], "Paint a cover.", tok)
    assert code(body) == "BUDGET_EXHAUSTED"
    assert len(proxy.of(kind)) == 1  # not retried
    if kind == "concept":
        assert proxy.of("image") == []  # nothing painted
    session = gql("query($id: ID!) { session(id: $id) { versions { n } } }", {"id": s["id"]}, tok=tok).json()["data"]["session"]
    assert session["versions"] == []


# --- 5. The allowance, in dollars -----------------------------------------------------------------------------------

BUDGET = "{ budget { level dailyUsd remainingUsd unlimited resetsAtUtc } }"
RESETS = "2026-10-12T00:00:00Z"


def allowance(level="standard", daily="1.00", remaining="0.9812", resets=RESETS) -> dict:
    headers = {modes.BUDGET_LEVEL_HEADER: level}
    if daily is not None:
        headers[modes.BUDGET_DAILY_HEADER] = daily
    if remaining is not None:
        headers[modes.BUDGET_REMAINING_HEADER] = remaining
    if resets is not None:
        headers[modes.BUDGET_RESETS_HEADER] = resets
    return headers


def budget_of(headers: dict | None) -> dict | None:
    return gql(BUDGET, tok=token(), headers=headers).json()["data"]["budget"]


def test_the_allowance_headers_become_the_budget(proxy):
    assert budget_of(allowance()) == {"level": "standard", "dailyUsd": 1.0, "remainingUsd": 0.9812, "unlimited": False, "resetsAtUtc": RESETS}
    assert budget_of(None) is None  # no headers
    assert budget_of(allowance("custom", "12.50", "3.0001")) == {"level": "custom", "dailyUsd": 12.5, "remainingUsd": 3.0001, "unlimited": False, "resetsAtUtc": RESETS}
    # Off: nothing left and nothing resets it. No limit: unlimited, with no amounts.
    assert budget_of(allowance("off", None, "0")) == {"level": "off", "dailyUsd": None, "remainingUsd": 0.0, "unlimited": False, "resetsAtUtc": None}
    assert budget_of(allowance("none", None, None)) == {"level": "none", "dailyUsd": None, "remainingUsd": None, "unlimited": True, "resetsAtUtc": None}
    # Without a reset time: the next UTC midnight.
    assert budget_of(allowance(resets=None))["resetsAtUtc"].endswith("T00:00:00Z")
    # The old token headers mean nothing any more.
    assert budget_of({"X-Budget-Daily-Tokens": "50000", "X-Budget-Remaining-Tokens": "41280"}) is None
    assert proxy.requests == []


def test_a_turns_cost_is_the_sites_cost_of_every_call_and_comes_off_the_balance(proxy):
    # One paint turn: two chat calls (the tool call, then the answer), the concept call and the image.
    proxy.headers = {"intent": {modes.CALL_COST_HEADER: "0.000050"}, "chat": {modes.CALL_COST_HEADER: "0.000400"},
                     "concept": {modes.CALL_COST_HEADER: "0.001200"}, "image": {modes.CALL_COST_HEADER: "0.039600"}}
    tok = token()
    s = start(tok, allowance())
    assert s["messages"][-1]["usage"] == {"inputTokens": 100, "outputTokens": 20, "costUsd": 0.0004}  # the opening: one chat call, no intent call
    assert proxy.of("intent") == []
    assert s["budget"]["remainingUsd"] == pytest.approx(0.9812 - 0.0004)
    body = chat(s["id"], "Paint a cover.", tok, headers=allowance(remaining="0.9808"))["data"]["chat"]
    usage = body["messages"][-1]["usage"]
    # The intent call, two chat calls (the tool call, then the answer), the concept call and the image.
    assert usage["costUsd"] == pytest.approx(0.00005 + 0.0004 + 0.0004 + 0.0012 + 0.0396)
    assert (usage["inputTokens"], usage["outputTokens"]) == (400, 80)  # the intent call, two chat calls and the concept call
    assert body["budget"] == {"level": "standard", "dailyUsd": 1.0, "remainingUsd": pytest.approx(0.9808 - 0.04165), "unlimited": False, "resetsAtUtc": RESETS}
    # The next turn's cost is its own: the intent call and one chat call.
    body = chat(s["id"], "Hello again.", tok, headers=allowance(remaining="0.9392"))["data"]["chat"]
    assert body["messages"][-1]["usage"]["costUsd"] == pytest.approx(0.00045)
    assert body["budget"]["remainingUsd"] == pytest.approx(0.93875)


def test_a_turn_with_a_call_that_has_no_cost_has_no_cost_and_leaves_the_balance_alone(proxy):
    proxy.headers = {"intent": {modes.CALL_COST_HEADER: "0.000050"}, "chat": {modes.CALL_COST_HEADER: "0.000400"},
                     "concept": {modes.CALL_COST_HEADER: "0.001200"}}  # the image: none
    tok = token()
    s = start(tok, allowance())
    body = chat(s["id"], "Paint a cover.", tok, headers=allowance())["data"]["chat"]
    assert body["messages"][-1]["usage"]["costUsd"] is None
    assert body["budget"]["remainingUsd"] == 0.9812
    # A balance never goes below nothing.
    proxy.headers["image"] = {modes.CALL_COST_HEADER: "0.5"}
    body = chat(s["id"], "Paint a cover.", tok, headers=allowance(remaining="0.01"))["data"]["chat"]
    assert body["budget"]["remainingUsd"] == 0.0


def test_chat_off_ends_the_turn_as_chat_off_with_its_level(proxy):
    tok = token()
    s = start(tok)
    proxy.refuse["chat"] = (403, {"type": "https://agentbullpen.ai/problems/chat-off", "title": "Chat is off", "status": 403, "level": "off", "detail": "SECRET"},
                            None, {modes.BUDGET_LEVEL_HEADER: "off"})
    n = len(proxy.of("chat"))
    body = chat(s["id"], "Hello.", tok)
    ext = body["errors"][0]["extensions"]
    assert ext["code"] == "CHAT_OFF"
    assert ext["problem"]["type"] == "https://agentbullpen.ai/problems/chat-off" and ext["problem"]["level"] == "off"
    assert "SECRET" not in json.dumps(body)
    assert len(proxy.of("chat")) == n + 1  # sent once: never retried
    # Inside a paint it ends the turn too.
    proxy.refuse = {"concept": proxy.refuse["chat"]}
    assert code(chat(s["id"], "Paint a cover.", tok)) == "CHAT_OFF"
    assert proxy.of("image") == []


# --- 5b. One protocol, and the history kept here ----------------------------------------------------------------------

def test_the_second_turn_sends_the_whole_history_and_no_response_id(proxy):
    tok = token()
    s = start(tok)
    chat(s["id"], "A stormy mood, please.", tok)
    first, second = (json.loads(r.content) for r in proxy.of("chat"))
    roles = [m["role"] for m in second["messages"]]
    assert roles == ["system", "user", "assistant", "user"]
    assert first["messages"] == second["messages"][:2]  # the instructions and the opening message, again
    assert content_of(second["messages"][2]) == "Hello. What mood do you want?"
    assert "A stormy mood, please." in content_of(second["messages"][3])
    for body in (first, second):
        assert "previous_response_id" not in body and "conversation" not in body and "store" not in body
        assert "input" not in body  # the Responses API's field


def test_a_paint_turns_tool_call_and_result_stay_in_the_history(proxy):
    tok = token()
    s = start(tok)
    chat(s["id"], "Paint a cover.", tok)
    chat(s["id"], "Thanks.", tok)
    last = json.loads(proxy.of("chat")[-1].content)["messages"]
    assert [m["role"] for m in last] == ["system", "user", "assistant", "user", "assistant", "tool", "assistant", "user"]
    assert last[4]["tool_calls"][0]["function"]["name"] == "paint_cover"
    assert last[5]["tool_call_id"] == "call_1" and "Painted version 1" in content_of(last[5])


def test_a_second_paint_in_the_same_session_sends_the_first_paints_tool_round_again(proxy):
    tok = token()
    s = start(tok)
    chat(s["id"], "Paint a cover.", tok)
    body = chat(s["id"], "Paint it again, at night.", tok)
    assert "errors" not in body, body
    assert [v["n"] for v in body["data"]["chat"]["versions"]] == [1, 2]
    last = json.loads(proxy.of("chat")[-1].content)["messages"]
    assert [m["role"] for m in last] == ["system", "user", "assistant", "user", "assistant", "tool", "assistant", "user", "assistant", "tool"]
    assert "Painted version 1" in content_of(last[5]) and "Painted version 2" in content_of(last[9])
    assert len(proxy.of("concept")) == 2 and len(proxy.of("image")) == 2


def test_changing_the_model_between_turns_keeps_the_history_and_the_versions(proxy):
    tok = token()
    s = start(tok)
    chat(s["id"], "Paint a cover.", tok)
    body = chat(s["id"], "Now in a colder palette.", tok, model="openai:gpt-6-luna")["data"]["chat"]
    assert body["model"] == "openai:gpt-6-luna" and [v["n"] for v in body["versions"]] == [1]
    last = json.loads(proxy.of("chat")[-1].content)
    assert last["model"] == "gpt-6-luna"
    assert [m["role"] for m in last["messages"]] == ["system", "user", "assistant", "user", "assistant", "tool", "assistant", "user"]
    # And on to another provider's own protocol: Anthropic's Messages gets the same conversation.
    chat(s["id"], "And warmer again.", tok, model="anthropic:claude-haiku-5-5")
    (r,) = proxy.of("anthropic")
    sent = json.loads(r.content)["messages"]
    assert len(sent) >= 7 and "And warmer again." in json.dumps(sent[-1])
    assert "Paint a cover." in json.dumps(sent)


@pytest.mark.parametrize("provider,model,path", [
    ("openai", "gpt-4o-mini", "/llm/openai/v1/chat/completions"),
    ("openrouter", "openai/gpt-4o-mini", "/llm/openrouter/v1/chat/completions"),
    ("azure", "my-deployment", "/llm/azure/openai/v1/chat/completions"),
])
def test_every_openai_compatible_gateway_route_is_chat_completions(provider, model, path):
    import asyncio
    from agent_framework import Message

    seen = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=completion(text("Hello."), model=model))

    calls = modes.GatewayModelCalls(main.mode.calls.s, 30, "1024x1536", "medium", 30, transport=httpx.MockTransport(answer))
    ref = modes.begin(modes.RequestState(token=token()))
    try:
        asyncio.run(calls.chat_client(f"{provider}:{model}").get_response([Message(role="user", contents=["Hello."])]))
    finally:
        modes.end(ref)
    (r,) = seen
    assert r.url.path == path and json.loads(r.content)["model"] == model


# --- 6. Sessions belong to their member ----------------------------------------------------------------------------

def test_another_members_session_counts_as_unknown(proxy):
    mine, theirs = token(sub="member-1"), token(sub="member-2")
    s = start(mine)
    assert "errors" not in chat(s["id"], "Paint a cover.", mine)
    assert gql("query($id: ID!) { session(id: $id) { id } }", {"id": s["id"]}, tok=theirs).json()["data"]["session"] is None
    assert code(chat(s["id"], "Hello.", theirs)) == "UNKNOWN_SESSION"
    assert client.get(f"/download/{s['id']}/1", headers={"Authorization": f"Bearer {theirs}"}).status_code == 404
    r = client.get(f"/download/{s['id']}/1", headers={"Authorization": f"Bearer {mine}"})
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert r.headers["content-disposition"] == 'attachment; filename="treasure-island-v1.png"'
