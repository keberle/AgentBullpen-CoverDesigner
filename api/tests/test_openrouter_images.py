# The image call over OpenRouter's images API (POST …/images), offline: behind the gateway (the site's
# /llm/openrouter route) and standalone (openrouter.ai), against the fake from test_gateway. Run: `uv run pytest` in api/.
import base64
import io
import json
from pathlib import Path

import httpx2 as httpx
import pytest
from PIL import Image
from test_gateway import BASE as LLM, ISSUER, KEY, FakeProxy, gql, token

import designer as designer_module
import images
import main
import modes
import problems

BASE = problems.BASE
MODEL = "openai/gpt-image-2.5-flare"
IMAGE = "image { model size quality inputTokens outputTokens costUsd }"
FIELDS = (f"id messages {{ usage {{ inputTokens outputTokens costUsd }} {IMAGE} paintProblem }} versions {{ n imageDataUrl {IMAGE} }}"
          " budget { remainingUsd }")
CHAT = f"mutation($id: ID!, $m: String!) {{ chat(sessionId: $id, message: $m) {{ {FIELDS} }} }}"
FIXTURES = {c["name"]: c for c in json.loads((Path(__file__).parent / "fixtures" / "provider-failures.json").read_text(encoding="utf-8"))["cases"]}
OFF = modes.GatewaySettings(signing_key="", issuer="", audience="", llm_base_url="", chat_model="", concept_model="", image_model="")


def use(monkeypatch, mode: modes.Mode, settings=None) -> FakeProxy:
    fake = FakeProxy()
    mode.calls.transport = httpx.MockTransport(fake.answer)
    mode.calls.sync_transport = httpx.MockTransport(fake.answer)
    if mode.gateway:
        mode.catalog.transport = httpx.MockTransport(fake.answer)
    monkeypatch.setattr(main, "mode", mode)
    monkeypatch.setattr(main.designer, "mode", mode)
    if settings:
        monkeypatch.setattr(main.designer, "settings", settings)
    return fake


@pytest.fixture(autouse=True)
def fresh_clients():
    def clear():
        main.designer._agents.clear()
        main.designer._clients.clear()
        main.designer._concept_client = None

    clear()
    yield
    clear()


@pytest.fixture
def fast_retries(monkeypatch):
    monkeypatch.setattr(modes, "RATE_LIMIT_WAIT_CAP", 0.0)


@pytest.fixture
def gateway(monkeypatch):
    """Gateway mode with the image model on OpenRouter (the default)."""
    g = modes.GatewaySettings(signing_key=KEY, issuer=ISSUER, audience="covers", llm_base_url=LLM, chat_model="openai:gpt-4o-mini",
                              concept_model="openai:gpt-6-luna", image_model=f"openrouter:{MODEL}")
    return use(monkeypatch, modes.choose(main.settings, main.image_settings, gateway=g))


@pytest.fixture
def standalone(monkeypatch):
    """Standalone on OpenRouter for chat and images, with no image endpoint set."""
    settings = designer_module.Settings(type="OpenAI", model="gpt-4o-mini", endpoint="https://openrouter.ai/api/v1/", api_key="sk-or-test-key", timeout_seconds=30)
    image_settings = images.ImageSettings(type="OpenRouter", model=MODEL, endpoint="", api_key="sk-or-image-test-key",
                                          size="1024x1536", quality="medium", timeout_seconds=30)
    return use(monkeypatch, modes.choose(settings, image_settings, gateway=OFF), settings)


def start(tok: str | None, headers: dict | None = None) -> str:
    r = gql('mutation { startSession(book: {title: "Treasure Island", author: "Robert Louis Stevenson"}) { id } }', tok=tok, headers=headers)
    return r.json()["data"]["startSession"]["id"]


def paint(session_id: str, tok: str | None, headers: dict | None = None) -> dict:
    r = gql(CHAT, {"id": session_id, "m": "Paint a cover."}, tok=tok, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def slug(body: dict) -> str:
    return body["errors"][0]["extensions"]["problem"]["type"].removeprefix(BASE)


def picture(fmt: str, size=(20, 30)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, (40, 80, 120)).save(out, format=fmt)
    return out.getvalue()


def plate_of(version: dict) -> bytes:
    prefix = "data:image/png;base64,"
    assert version["imageDataUrl"].startswith(prefix)
    return base64.b64decode(version["imageDataUrl"][len(prefix):])


# --- Behind the gateway ---------------------------------------------------------------------------------------------

def test_the_default_gateway_image_model_is_on_openrouter(monkeypatch):
    monkeypatch.delenv("GATEWAY_IMAGE_MODEL")
    g = modes.GatewaySettings.from_env()
    assert g.image_model == f"openrouter:{MODEL}"
    s = modes.choose(main.settings, main.image_settings, gateway=g).calls.image_settings
    assert (s.type, s.model, s.endpoint) == ("OpenRouter", MODEL, f"{LLM}/openrouter/v1/")


def test_a_paint_posts_to_the_sites_openrouter_images_route_with_the_jwt(gateway):
    gateway.headers = {"intent": {modes.CALL_COST_HEADER: "0.00005"}, "chat": {modes.CALL_COST_HEADER: "0.0004"},
                       "concept": {modes.CALL_COST_HEADER: "0.0012"}, "image": {modes.CALL_COST_HEADER: "0.0412"}}
    tok = token()
    allowance = {modes.BUDGET_LEVEL_HEADER: "standard", modes.BUDGET_DAILY_HEADER: "1.00", modes.BUDGET_REMAINING_HEADER: "0.5000"}
    session_id = start(tok)
    body = paint(session_id, tok, allowance)
    assert "errors" not in body, body
    (request,) = gateway.of("image")
    assert request.method == "POST" and str(request.url) == f"{LLM}/openrouter/v1/images"
    assert request.headers["authorization"] == f"Bearer {tok}" and request.headers[modes.CONVERSATION_HEADER] == session_id
    assert request.headers["content-type"] == "application/json"
    sent = json.loads(request.content)
    assert sent.pop("prompt").startswith("USE: cover.")
    # Only what the model lists as supported: no size (the shape goes as an aspect ratio) and no output format.
    assert sent == {"model": MODEL, "n": 1, "quality": "medium", "aspect_ratio": "2:3"}

    session = body["data"]["chat"]
    (version,) = session["versions"]
    assert plate_of(version).startswith(images.PNG_SIGNATURE)
    info = {"model": MODEL, "size": "16x24", "quality": "medium", "inputTokens": 12, "outputTokens": 4175, "costUsd": 0.04}
    assert version["image"] == info and session["messages"][-1]["image"] == info  # the size is the painted image's own
    # The turn's cost and the balance come from the site's X-Call-Cost-Usd, the image's too.
    cost = 0.00005 + 0.0004 + 0.0004 + 0.0012 + 0.0412
    assert session["messages"][-1]["usage"]["costUsd"] == pytest.approx(cost)
    assert session["budget"]["remainingUsd"] == pytest.approx(0.5 - cost)


@pytest.mark.parametrize("fmt,media_type", [("JPEG", "image/jpeg"), ("WEBP", "image/webp"), ("JPEG", None)])
def test_a_plate_that_is_not_a_png_becomes_one(gateway, fmt, media_type):
    gateway.plate, gateway.media_type = picture(fmt), media_type
    tok = token()
    body = paint(start(tok), tok)
    (version,) = body["data"]["chat"]["versions"]
    png = plate_of(version)
    assert png.startswith(images.PNG_SIGNATURE)
    with Image.open(io.BytesIO(png)) as plate:
        assert plate.format == "PNG" and plate.size == (20, 30)
    assert version["image"]["size"] == "20x30"


def test_an_answer_without_usage_still_paints(gateway):
    gateway.image_usage = None
    tok = token()
    body = paint(start(tok), tok)
    assert "errors" not in body, body
    (version,) = body["data"]["chat"]["versions"]
    assert version["image"] == {"model": MODEL, "size": "16x24", "quality": "medium", "inputTokens": None, "outputTokens": None, "costUsd": None}


def test_a_429_is_retried_once_then_painted(gateway, fast_retries):
    gateway.refuse["image"] = (429, {"type": BASE + "rate-limited", "status": 429, "retryAfterSeconds": 1}, 1, {"Retry-After": "1"})
    tok = token()
    body = paint(start(tok), tok)
    assert "errors" not in body, body
    assert len(gateway.of("image")) == 2 and [v["n"] for v in body["data"]["chat"]["versions"]] == [1]


def test_a_429_twice_fails_the_paint_after_the_one_retry(gateway, fast_retries):
    gateway.refuse["image"] = (429, {"type": BASE + "rate-limited", "status": 429, "retryAfterSeconds": 1})
    tok = token()
    body = paint(start(tok), tok)
    assert len(gateway.of("image")) == 2 and slug(body) == "rate-limited" and body["data"]["chat"]["versions"] == []


# The same refusals as the OpenAI path's (test_problems), and OpenRouter's own moderation status.
@pytest.mark.parametrize("refusal,expected", [
    ((503, {"type": BASE + "provider-unavailable", "status": 503, "reason": "credits", "provider": "openrouter"}), "provider-unavailable"),
    ((502, {"type": BASE + "upstream-error", "status": 502, "provider": "openrouter"}), "upstream-error"),
    ((400, {"type": BASE + "upstream-rejected", "status": 400, "provider": "openrouter"}), "content-policy"),
    ((403, {"type": BASE + "upstream-rejected", "status": 403, "provider": "openrouter"}), "content-policy"),
])
def test_a_refused_paint_is_sent_once_and_says_why(gateway, refusal, expected):
    gateway.refuse["image"] = refusal
    tok = token()
    body = paint(start(tok), tok)
    assert slug(body) == expected and body["data"]["chat"]["versions"] == []
    assert len(gateway.of("image")) == 1  # a paid call: never sent twice
    assert "openrouter" not in json.dumps(body)


def test_a_spent_allowance_ends_the_turn_after_one_call(gateway):
    gateway.refuse["image"] = (403, {"type": BASE + "budget-exhausted", "status": 403})
    tok = token()
    body = paint(start(tok), tok)
    assert body["errors"][0]["extensions"]["code"] == "BUDGET_EXHAUSTED" and len(gateway.of("image")) == 1


def test_an_answer_without_an_image_fails_the_paint_after_one_call(gateway):
    gateway.refuse["image"] = (200, {"error": {"code": 502, "message": "SECRET-PROVIDER-TEXT"}})
    tok = token()
    body = paint(start(tok), tok)
    assert slug(body) == "upstream-error" and body["data"]["chat"]["versions"] == []
    assert len(gateway.of("image")) == 1 and "SECRET" not in json.dumps(body)


def test_a_timeout_is_not_retried(gateway):
    gateway.raise_for["image"] = httpx.ReadTimeout("slow")
    tok = token()
    body = paint(start(tok), tok)
    assert slug(body) == "timeout" and len(gateway.of("image")) == 1


@pytest.mark.parametrize("image_model,ok", [
    (f"openrouter:{MODEL}", True), ("openai:gpt-image-2.5-flare", True), ("azure:my-deployment", True),
    ("anthropic:claude-haiku-5-5", False), ("gpt-image-2.5-flare", False), ("openrouter:", False),
])
def test_the_image_models_provider_picks_the_protocol(image_model, ok):
    g = modes.GatewaySettings(signing_key=KEY, issuer=ISSUER, audience="covers", llm_base_url=LLM, chat_model="openai:gpt-4o-mini",
                              concept_model="openai:gpt-6-luna", image_model=image_model)
    calls = modes.choose(main.settings, main.image_settings, gateway=g).calls
    available, note = calls.images_available()
    assert available is ok
    if ok:
        provider = image_model.partition(":")[0]
        assert calls.image_settings.type == ("OpenRouter" if provider == "openrouter" else "OpenAI")
        assert calls.image_settings.endpoint == f"{LLM}/{'azure/openai' if provider == 'azure' else provider}/v1/"
    else:
        assert note == f"GATEWAY_IMAGE_MODEL must be openrouter:, openai: or azure: and a model (got {image_model!r})."


# --- Standalone -------------------------------------------------------------------------------------------------------

def test_standalone_without_an_endpoint_posts_to_openrouter_with_its_key_and_counts_its_cost(standalone):
    standalone.usage = {"intent": {"cost": 0.0001}, "chat": {"cost": 0.0005}, "concept": {"cost": 0.002}}
    body = paint(start(None), None)
    assert "errors" not in body, body
    (request,) = standalone.of("image")
    assert str(request.url) == "https://openrouter.ai/api/v1/images"
    assert request.headers["authorization"] == "Bearer sk-or-image-test-key"
    assert json.loads(request.content)["model"] == MODEL
    message = body["data"]["chat"]["messages"][-1]
    assert message["image"]["costUsd"] == 0.04
    # The provider's own costs: the intent call, two chat calls, the concept call and the image.
    assert message["usage"]["costUsd"] == pytest.approx(0.0001 + 0.0005 + 0.0005 + 0.002 + 0.04)
    assert "sk-or-image-test-key" not in json.dumps(body)


def test_standalone_settings_accept_openrouter_and_name_both_types():
    s = images.ImageSettings(type="OpenRouter", model=MODEL, endpoint="", api_key="k", size="1024x1536", quality="medium", timeout_seconds=30)
    assert s.available() == (True, None)
    other = images.ImageSettings(type="Stability", model="m", endpoint="", api_key="k", size="1024x1536", quality="medium", timeout_seconds=30)
    assert other.available() == (False, "IMAGE_TYPE must be OpenAI or OpenRouter (got 'Stability').")


@pytest.mark.parametrize("case,expected,reason", [
    ("openrouter-image-402-credits", "provider-unavailable", "credits"),
    ("openrouter-402-key-limit", "provider-unavailable", "key-limit"),
    ("openrouter-image-502-no-charge", "upstream-error", None),
    ("openrouter-image-403-moderation", "content-policy", None),
    ("openrouter-image-400-provider-declined", "content-policy", None),
    ("openrouter-image-400-bad-parameter", "upstream-rejected", None),
])
def test_standalone_failures_are_sent_once_and_classified(standalone, case, expected, reason):
    recorded = FIXTURES[case]
    standalone.refuse["image"] = (recorded["status"], recorded["body"])
    body = paint(start(None), None)
    problem = body["errors"][0]["extensions"]["problem"]
    assert slug(body) == expected and problem.get("reason") == reason and body["data"]["chat"]["versions"] == []
    assert len(standalone.of("image")) == 1
    if reason:
        assert problem["provider"] == "OpenRouter" and "image-api-key" not in problem["detail"]
    assert recorded["body"]["error"]["message"] not in json.dumps(body)  # the provider's text never reaches the answer


def test_standalone_rejected_key_names_the_image_secret(standalone):
    standalone.refuse["image"] = (401, FIXTURES["openrouter-401-key-disabled"]["body"])
    body = paint(start(None), None)
    problem = body["errors"][0]["extensions"]["problem"]
    assert problem["detail"] == "Your OpenRouter key was rejected. Set it again with `aspire secret set Parameters:image-api-key <key>`."


def test_standalone_429_is_retried_once(standalone, fast_retries):
    standalone.refuse["image"] = (429, FIXTURES["openrouter-429-free-tier"]["body"], 1, {"Retry-After": "1"})
    body = paint(start(None), None)
    assert "errors" not in body, body
    assert len(standalone.of("image")) == 2


# --- The client itself ------------------------------------------------------------------------------------------------

def test_without_a_policy_client_a_plain_one_is_used_with_the_timeout(monkeypatch):
    fake, made, real = FakeProxy(), [], httpx.Client

    def plain(**kwargs):
        made.append(kwargs)
        return real(transport=httpx.MockTransport(fake.answer), **kwargs)

    monkeypatch.setattr(images.httpx, "Client", plain)
    s = images.ImageSettings(type="OpenRouter", model=MODEL, endpoint="https://example.test/api/v1", api_key="k", size="auto", quality="high", timeout_seconds=45)
    png, info, _ = images._generate(s, "a moor at night")
    assert made == [{"timeout": 45}] and png.startswith(images.PNG_SIGNATURE)
    (request,) = fake.requests
    assert str(request.url) == "https://example.test/api/v1/images"
    assert json.loads(request.content) == {"model": MODEL, "prompt": "a moor at night", "n": 1, "quality": "high"}  # no shape to send
    assert (info.input_tokens, info.output_tokens, info.cost_usd) == (12, 4175, 0.04)


@pytest.mark.parametrize("size,ratio", [("1024x1536", "2:3"), ("1536x1024", "3:2"), ("1024x1024", "1:1"), ("2K", None), ("auto", None), ("0x10", None)])
def test_the_shape_goes_as_an_aspect_ratio(size, ratio):
    assert images.aspect_ratio(size) == ratio
