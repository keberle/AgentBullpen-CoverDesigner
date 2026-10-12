# Problem details on both transports, the problem catalogue, the standalone classifier on recorded responses,
# the retry rules for paid calls and the paint-failed path. Offline: a fake proxy (gateway, from test_gateway) or a fake
# provider (standalone, the same fake behind the standalone mode's transports). Run: `uv run pytest` in api/.
import json
from pathlib import Path

import httpx2 as httpx
import pytest
from conftest import problem_counts
from test_gateway import FakeProxy, client, gql, proxy, start, token  # noqa: F401  (proxy is a fixture)

import designer as designer_module
import images
import main
import modes
import problems
import provider_failures

BASE = problems.BASE
FIELDS = "id messages { role text usage { inputTokens outputTokens costUsd } paintProblem fallbackUsed { requested used } } versions { n } budget { level dailyUsd remainingUsd unlimited resetsAtUtc }"
CHAT = f"mutation($id: ID!, $m: String!, $model: String, $license: String) {{ chat(sessionId: $id, message: $m, model: $model, license: $license) {{ {FIELDS} }} }}"


def chat(session_id: str, message: str, tok: str | None = None, **variables) -> dict:
    r = gql(CHAT, {"id": session_id, "m": message, **variables}, tok=tok)
    assert r.status_code == 200, r.text
    return r.json()


def error(body: dict, i: int = 0) -> dict:
    return body["errors"][i]["extensions"]


def slug(ext: dict) -> str:
    return ext["problem"]["type"].removeprefix(BASE)


def tool_output(fake: FakeProxy) -> str:
    """What paint_cover told the designer (the tool message sent with the follow-up chat call)."""
    for r in fake.of("chat"):
        for item in json.loads(r.content).get("messages") or []:
            if isinstance(item, dict) and item.get("role") == "tool":
                return json.dumps(item)
    return ""


@pytest.fixture
def fast_retries(monkeypatch):
    monkeypatch.setattr(modes, "RATE_LIMIT_WAIT_CAP", 0.0)


@pytest.fixture
def standalone(monkeypatch):
    """Standalone mode on OpenRouter (chat) and OpenAI (images), against a fake provider."""
    settings = designer_module.Settings(type="OpenAI", model="gpt-4o-mini", endpoint="https://openrouter.ai/api/v1/", api_key="sk-or-test-key", timeout_seconds=30)
    image_settings = images.ImageSettings(type="OpenAI", model="gpt-image-2.5-flare", endpoint="", api_key="sk-image-test-key",
                                          size="1024x1536", quality="medium", timeout_seconds=30)
    off = modes.GatewaySettings(signing_key="", issuer="", audience="", llm_base_url="", chat_model="", concept_model="", image_model="")
    mode = modes.choose(settings, image_settings, gateway=off)
    fake = FakeProxy()
    mode.calls.transport = httpx.MockTransport(fake.answer)
    mode.calls.sync_transport = httpx.MockTransport(fake.answer)
    monkeypatch.setattr(main, "mode", mode)
    monkeypatch.setattr(main.designer, "mode", mode)
    monkeypatch.setattr(main.designer, "settings", settings)
    main.designer._agents.clear()
    main.designer._clients.clear()
    main.designer._concept_client = None
    yield fake
    main.designer._agents.clear()
    main.designer._clients.clear()
    main.designer._concept_client = None


def start_standalone() -> dict:
    return start(None)


# --- 1. REST: application/problem+json ------------------------------------------------------------------------------

def assert_problem(r, status: int, kind: str) -> dict:
    assert r.status_code == status, r.text
    assert r.headers["content-type"] == "application/problem+json"
    body = r.json()
    assert body["type"] == BASE + kind and body["status"] == status and body["title"]
    assert len(body["traceId"]) == 32 and int(body["traceId"], 16)
    return body


def test_download_of_an_unknown_session_or_version_is_a_problem(proxy):
    tok = token()
    auth = {"Authorization": f"Bearer {tok}"}
    assert_problem(client.get("/download/nope/1", headers=auth), 404, "unknown-session")
    s = start(tok)
    body = assert_problem(client.get(f"/download/{s['id']}/7", headers=auth), 404, "unknown-version")
    assert body["detail"] == "This session has no version 7."


def test_other_rest_errors_are_problems(proxy):
    assert_problem(client.get("/download/x/1"), 401, "unauthorized")  # gateway: no token
    auth = {"Authorization": f"Bearer {token()}"}
    assert_problem(client.get("/nothing-here", headers=auth), 404, "not-found")
    assert_problem(client.get("/download/x/not-a-number", headers=auth), 400, "bad-request")


def test_a_forced_500_is_internal_error_with_only_the_trace_id(proxy, monkeypatch):
    tok = token()
    s = start(tok)
    assert "errors" not in chat(s["id"], "Paint a cover.", tok)

    def boom(*args):
        raise RuntimeError("SECRET-STACK-TEXT")

    monkeypatch.setattr(main.typography, "render", boom)
    from fastapi.testclient import TestClient

    quiet = TestClient(main.app, raise_server_exceptions=False)
    r = quiet.get(f"/download/{s['id']}/1", headers={"Authorization": f"Bearer {tok}"})
    body = assert_problem(r, 500, "internal-error")
    assert "SECRET" not in r.text
    assert body["detail"] == f"Something went wrong (ref {body['traceId']})."


# --- 2. GraphQL: extensions.code (kept) + extensions.problem ----------------------------------------------------------

# The problem answers from the site's proxy → this demo's code, and the extensions passed on.
SITE = [
    ("budget-exhausted", 403, {"dailyAllowanceUsd": 1.0, "usedTodayUsd": 1.0042, "resetsAtUtc": "2026-10-10T00:00:00Z"}, "BUDGET_EXHAUSTED",
     {"dailyAllowanceUsd": 1.0, "usedTodayUsd": 1.0042, "resetsAtUtc": "2026-10-10T00:00:00Z"}),
    ("chat-off", 403, {"level": "off"}, "CHAT_OFF", {"level": "off"}),
    ("rate-limited", 429, {"retryAfterSeconds": 12}, "RATE_LIMITED", {"retryAfterSeconds": 12}),
    ("model-not-allowed", 403, {"model": "gpt-4o-mini"}, "UNKNOWN_MODEL", {"model": "gpt-4o-mini"}),
    ("provider-unavailable", 503, {"provider": "openrouter", "reason": "credits", "fallbackModel": None}, "PROVIDER_UNAVAILABLE", {"reason": "credits"}),
    ("upstream-error", 502, {"provider": "openrouter"}, "MODEL_ERROR", {}),
    ("unauthorized", 401, {}, "UNAUTHORIZED", {}),
]


@pytest.mark.parametrize("kind,status,ext,code,passed", SITE)
def test_the_sites_problems_become_code_and_problem_without_provider_or_detail(proxy, kind, status, ext, code, passed):
    tok = token()
    s = start(tok)
    proxy.refuse["chat"] = (status, {"type": BASE + kind, "title": "x", "status": status, "detail": "SECRET-PROXY-DETAIL", **ext},
                            None, {"Retry-After": "12"} if status == 429 else {})
    before = problem_counts()
    n = len(proxy.of("chat"))
    body = chat(s["id"], "Hello.", tok)
    e = error(body)
    assert e["code"] == code and slug(e) == kind
    problem = e["problem"]
    assert {k: problem[k] for k in passed} == passed
    assert "provider" not in problem and "fallbackModel" not in problem
    assert "SECRET" not in json.dumps(body) and "openrouter" not in json.dumps(body)
    assert len(problem["traceId"]) == 32
    assert len(proxy.of("chat")) == n + 1  # never retried here (the chat call's 429 is the web's to retry)
    key = (kind, passed.get("reason", ""))
    assert problem_counts().get(key, 0) == before.get(key, 0) + 1


def test_local_problem_types(proxy, monkeypatch):
    tok = token()
    s = start(tok)
    e = error(chat("nope", "Hello.", tok))
    assert (e["code"], slug(e)) == ("UNKNOWN_SESSION", "unknown-session")
    e = error(chat(s["id"], "Hello.", tok, license="reckless"))
    assert (e["code"], slug(e)) == ("BAD_LICENSE", "bad-request")
    e = error(gql("{ noSuchField }", tok=tok).json())
    assert (e["code"], slug(e)) == ("BAD_REQUEST", "bad-request")
    e = error(chat(s["id"], "Hello.", tok, model="openai:gpt-9"))
    assert (e["code"], slug(e), e["problem"]["model"]) == ("UNKNOWN_MODEL", "model-not-allowed", "openai:gpt-9")

    proxy.raise_for["chat"] = httpx.ReadTimeout("slow")
    e = error(chat(s["id"], "Hello.", tok))
    assert (e["code"], slug(e)) == ("TIMEOUT", "timeout")
    del proxy.raise_for["chat"]

    def boom(*args):
        raise RuntimeError("SECRET-STACK-TEXT")

    monkeypatch.setattr(main.designer, "get", boom)
    body = chat(s["id"], "Hello.", tok)
    e = error(body)
    assert (e["code"], slug(e)) == ("INTERNAL_ERROR", "internal-error")
    assert body["errors"][0]["message"] == f"Something went wrong (ref {e['problem']['traceId']})."
    assert "SECRET" not in json.dumps(body)


def test_not_configured_types(standalone, monkeypatch):
    settings = designer_module.Settings(type="", model="", endpoint="", api_key="", timeout_seconds=30)
    empty_images = images.ImageSettings(type="", model="", endpoint="", api_key="", size="1024x1536", quality="medium", timeout_seconds=30)
    off = modes.GatewaySettings(signing_key="", issuer="", audience="", llm_base_url="", chat_model="", concept_model="", image_model="")
    mode = modes.choose(settings, empty_images, gateway=off)
    monkeypatch.setattr(main, "mode", mode)
    monkeypatch.setattr(main.designer, "mode", mode)
    a = gql("{ availability { chat images chatProblem imagesProblem } }").json()["data"]["availability"]
    assert a["chatProblem"]["type"] == BASE + "model-not-configured" and a["chatProblem"]["status"] == 503
    assert a["imagesProblem"]["type"] == BASE + "images-not-configured"
    assert "LLM_API_KEY" in a["chatProblem"]["detail"]
    s = start_standalone()
    e = error(chat(s["id"], "Hello."))
    assert (e["code"], slug(e)) == ("NO_MODEL", "model-not-configured")


# --- 3. Paints that fail: the answer stays, no version, the note's problem ---------------------------------------------

@pytest.mark.parametrize("refusal,expected", [
    ((503, {"type": BASE + "provider-unavailable", "status": 503, "reason": "credits", "provider": "openai"}), "provider-unavailable"),
    ((502, {"type": BASE + "upstream-error", "status": 502, "provider": "openai"}), "upstream-error"),
    ((400, {"type": BASE + "upstream-rejected", "status": 400, "provider": "openai"}), "content-policy"),
])
def test_a_failed_image_keeps_the_answer_adds_no_version_and_says_why(proxy, refusal, expected):
    tok = token()
    s = start(tok)
    proxy.refuse["image"] = refusal
    body = chat(s["id"], "Paint a cover.", tok)
    session = body["data"]["chat"]
    assert session["versions"] == []
    e = error(body)
    assert e["code"] == "IMAGE_ERROR" and slug(e) == expected
    assert session["messages"][-1]["paintProblem"]["type"] == BASE + expected
    assert len(proxy.of("image")) == 1  # never retried
    told = tool_output(proxy)
    assert "nothing was painted" in told and "do not describe a painting" in told


def test_an_image_timeout_is_not_retried(proxy):
    tok = token()
    s = start(tok)
    proxy.raise_for["image"] = httpx.ReadTimeout("slow")
    body = chat(s["id"], "Paint a cover.", tok)
    assert slug(error(body)) == "timeout" and body["data"]["chat"]["versions"] == []
    assert len(proxy.requests) and len([r for r in proxy.requests if r.url.path.endswith("/images/generations")]) == 1


def test_invalid_concepts_twice_is_concept_failed(proxy):
    tok = token()
    s = start(tok)
    proxy.refuse["concept"] = (200, {"not": "a response"})
    body = chat(s["id"], "Paint a cover.", tok)
    e = error(body)
    assert (e["code"], slug(e)) == ("CONCEPT_ERROR", "concept-failed")
    assert len(proxy.of("concept")) == 2 and proxy.of("image") == []  # the concept call's one retry; no image call


# --- 4. Retry rules for paid calls ------------------------------------------------------------------------------------

def test_an_image_429_is_retried_once_then_painted(proxy, fast_retries):
    tok = token()
    s = start(tok)
    proxy.refuse["image"] = (429, {"type": BASE + "rate-limited", "status": 429, "retryAfterSeconds": 1}, 1, {"Retry-After": "1"})
    body = chat(s["id"], "Paint a cover.", tok)
    assert "errors" not in body, body
    assert len(proxy.of("image")) == 2 and [v["n"] for v in body["data"]["chat"]["versions"]] == [1]


def test_an_image_429_twice_fails_the_paint_after_one_retry(proxy, fast_retries):
    tok = token()
    s = start(tok)
    proxy.refuse["image"] = (429, {"type": BASE + "rate-limited", "status": 429, "retryAfterSeconds": 1})
    body = chat(s["id"], "Paint a cover.", tok)
    assert len(proxy.of("image")) == 2
    assert slug(error(body)) == "rate-limited" and body["data"]["chat"]["versions"] == []


def test_the_retry_wait_is_capped_at_30_seconds():
    policy = modes.CallPolicy("image", gateway=True)
    assert policy.wait_before_retry(problems.make("rate-limited", retryAfterSeconds=600), 429) == 30.0
    assert policy.wait_before_retry(problems.make("rate-limited", retryAfterSeconds=2), 429) == 2.0
    assert policy.wait_before_retry(problems.make("upstream-error"), 502) is None  # an image call: never on 5xx
    assert modes.CallPolicy("chat", gateway=True).wait_before_retry(problems.make("rate-limited"), 429) is None  # the web's
    assert modes.CallPolicy("chat", gateway=False).wait_before_retry(problems.make("upstream-error"), 500) == 0.0
    assert modes.CallPolicy("chat", gateway=True).wait_before_retry(problems.make("upstream-error"), 502) is None  # proxy retried


# --- 5. Gateway: fallback, availability, budget reset ------------------------------------------------------------------

def test_x_model_fallback_becomes_fallback_used_by_display_name(proxy):
    tok = token()
    s = start(tok)
    proxy.models = {"providers": [
        {"name": "openai", "protocol": "openai", "models": [{"id": "gpt-4o-mini", "kind": "chat", "displayName": "GPT-4o mini"}]},
        {"name": "openrouter", "protocol": "openai", "models": [{"id": "anthropic/claude-haiku-5.5", "kind": "chat", "displayName": "Claude Haiku 5.5"}]},
    ]}
    proxy.headers["chat"] = {modes.FALLBACK_HEADER: "anthropic/claude-haiku-5.5 -> gpt-4o-mini"}
    body = chat(s["id"], "Hello.", tok)
    assert body["data"]["chat"]["messages"][-1]["fallbackUsed"] == {"requested": "Claude Haiku 5.5", "used": "GPT-4o mini"}
    del proxy.headers["chat"]
    assert chat(s["id"], "Hello again.", tok)["data"]["chat"]["messages"][-1]["fallbackUsed"] is None


def test_unavailable_models_are_marked_and_never_the_default(proxy):
    tok = token()
    proxy.models = {"providers": [
        {"name": "openai", "protocol": "openai", "available": False, "unavailableUntilUtc": "2026-10-09T18:15:00Z",
         "models": [{"id": "gpt-4o-mini", "kind": "chat", "available": False, "unavailableUntilUtc": "2026-10-09T18:15:00Z"},
                    {"id": "gpt-6-luna", "kind": "chat", "available": False, "unavailableUntilUtc": "2026-10-09T18:15:00Z"}]},
        {"name": "anthropic", "protocol": "anthropic", "available": True, "models": [{"id": "claude-haiku-5-5", "kind": "chat", "available": True}]},
    ]}
    models = gql("{ chatModels { id isDefault available unavailableUntilUtc } }", tok=tok).json()["data"]["chatModels"]
    assert models == [
        {"id": "openai:gpt-4o-mini", "isDefault": False, "available": False, "unavailableUntilUtc": "2026-10-09T18:15:00Z"},
        {"id": "openai:gpt-6-luna", "isDefault": False, "available": False, "unavailableUntilUtc": "2026-10-09T18:15:00Z"},
        {"id": "anthropic:claude-haiku-5-5", "isDefault": True, "available": True, "unavailableUntilUtc": None},
    ]
    s = start(tok)
    e = error(chat(s["id"], "Hello.", tok, model="openai:gpt-6-luna"))  # listed, but unavailable
    assert (e["code"], slug(e)) == ("UNKNOWN_MODEL", "model-not-allowed")


def test_budget_carries_its_reset_time(proxy):
    tok = token()
    headers = {modes.BUDGET_LEVEL_HEADER: "standard", modes.BUDGET_DAILY_HEADER: "1.00", modes.BUDGET_REMAINING_HEADER: "0.0100"}
    b = gql("{ budget { remainingUsd resetsAtUtc } }", tok=tok, headers=headers).json()["data"]["budget"]
    assert b["remainingUsd"] == 0.01 and b["resetsAtUtc"].endswith("T00:00:00Z")
    s = start(tok)
    proxy.refuse["chat"] = (403, {"type": BASE + "budget-exhausted", "status": 403, "resetsAtUtc": "2026-10-10T00:00:00Z"})
    e = error(chat(s["id"], "Hello.", tok))
    assert e["problem"]["resetsAtUtc"] == "2026-10-10T00:00:00Z"
    # An older proxy (no type): the next UTC midnight.
    proxy.refuse["chat"] = (403, {"title": "Daily allowance used up"})
    e = error(chat(s["id"], "Hello.", tok))
    assert e["code"] == "BUDGET_EXHAUSTED" and e["problem"]["resetsAtUtc"].endswith("T00:00:00Z")


# --- 6. Standalone: the same failures, classified here --------------------------------------------------------------

FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "provider-failures.json").read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", FIXTURES, ids=[c["name"] for c in FIXTURES])
def test_the_classifier_on_recorded_responses(case):
    f = provider_failures.classify(case["provider"], case["status"], httpx.Headers(case["headers"]), json.dumps(case["body"]).encode(), image=case.get("image", False))
    assert (f.kind, f.reason) == (case["kind"], case["reason"]), case["source"]


def test_rate_limit_waits_come_from_the_header_or_the_message():
    short = next(c for c in FIXTURES if c["name"] == "azure-429-short-wait")
    f = provider_failures.classify("azure", 429, httpx.Headers({}), json.dumps(short["body"]).encode())
    assert (f.kind, f.retry_after) == ("rate-limited", 6)  # "Please retry after 6 seconds." in the message


def test_standalone_openrouter_402_is_provider_unavailable_with_the_fix_it_text(standalone):
    s = start_standalone()
    case = next(c for c in FIXTURES if c["name"] == "openrouter-402-credits")
    standalone.refuse["chat"] = (402, case["body"])
    n = len(standalone.of("chat"))
    before = problem_counts()
    body = chat(s["id"], "Hello.")
    e = error(body)
    assert (e["code"], slug(e), e["problem"]["reason"], e["problem"]["provider"]) == ("PROVIDER_UNAVAILABLE", "provider-unavailable", "credits", "OpenRouter")
    assert e["problem"]["detail"] == ("Your OpenRouter account can't serve requests right now (credits). Top it up, or switch to"
                                      " another provider in docs › Configuration.")
    assert "Insufficient credits" not in json.dumps(body) and "sk-or-test-key" not in json.dumps(body)
    assert len(standalone.of("chat")) == n + 1  # an account failure is never retried
    assert problem_counts().get(("provider-unavailable", "credits"), 0) == before.get(("provider-unavailable", "credits"), 0) + 1


def test_standalone_rejected_key_names_the_secret_to_set(standalone):
    s = start_standalone()
    standalone.refuse["image"] = (401, {"error": {"message": "Incorrect API key provided: sk-proj-****abcd.", "type": "invalid_request_error", "code": "invalid_api_key"}})
    body = chat(s["id"], "Paint a cover.")
    e = error(body)
    assert e["code"] == "IMAGE_ERROR" and e["problem"]["reason"] == "auth" and e["problem"]["provider"] == "OpenAI"
    assert e["problem"]["detail"] == "Your OpenAI key was rejected. Set it again with `aspire secret set Parameters:image-api-key <key>`."
    assert "sk-proj" not in json.dumps(body) and body["data"]["chat"]["versions"] == []


def test_standalone_moderation_refusal_is_content_policy(standalone):
    s = start_standalone()
    case = next(c for c in FIXTURES if c["name"] == "openai-image-400-moderation-blocked")
    standalone.refuse["image"] = (400, case["body"])
    body = chat(s["id"], "Paint a cover.")
    e = error(body)
    assert (e["code"], slug(e)) == ("IMAGE_ERROR", "content-policy")
    assert "safety system" not in json.dumps(body)
    assert "declined this picture" in tool_output(standalone)


def test_standalone_chat_5xx_is_retried_once_at_once(standalone):
    s = start_standalone()
    standalone.refuse["chat"] = (500, {"error": {"message": "The server had an error", "type": "server_error"}}, 1)
    n = len(standalone.of("chat"))
    body = chat(s["id"], "Hello.")
    assert "errors" not in body, body
    assert len(standalone.of("chat")) == n + 2
    standalone.refuse["chat"] = (500, {"error": {"message": "The server had an error", "type": "server_error"}})
    e = error(chat(s["id"], "Hello."))
    assert (e["code"], slug(e)) == ("MODEL_ERROR", "upstream-error")


def test_standalone_image_5xx_is_never_retried(standalone):
    s = start_standalone()
    standalone.refuse["image"] = (500, {"error": {"message": "The server had an error", "type": "server_error"}})
    body = chat(s["id"], "Paint a cover.")
    assert len(standalone.of("image")) == 1 and body["data"]["chat"]["versions"] == []
    assert slug(error(body)) == "upstream-error"


def test_standalone_chat_429_goes_to_the_web_with_its_wait(standalone):
    s = start_standalone()
    standalone.refuse["chat"] = (429, {"error": {"code": 429, "message": "Rate limit exceeded: 20 requests per minute"}}, None, {"Retry-After": "7"})
    n = len(standalone.of("chat"))
    e = error(chat(s["id"], "Hello."))
    assert (e["code"], slug(e), e["problem"]["retryAfterSeconds"]) == ("RATE_LIMITED", "rate-limited", 7)
    assert len(standalone.of("chat")) == n + 1
