# Gateway and standalone modes, the Python counterpart of AgentBullpen-TemporalGraph's `ApiMode`: one
# build, the mode chosen once at startup, and four small adapters that hold all of the difference.
#
# | Adapter        | Gateway (GATEWAY_SIGNING_KEY set)                                   | Standalone (unset)            |
# |----------------|---------------------------------------------------------------------|-------------------------------|
# | current user   | the site's HS256 JWT (iss, aud `covers`, 30 s leeway); else 401      | the anonymous local user      |
# | model calls    | {GATEWAY_LLM_BASE_URL}/{provider}/…, key = the request's JWT        | LLM_* / IMAGE_* and their keys|
# | model catalog  | GET {GATEWAY_LLM_BASE_URL}/models (chat models, cached 60 s)        | the one configured chat model |
# | budget         | the X-Budget-* headers: a daily allowance in US dollars             | unlimited                     |
#
# Every OpenAI-compatible chat call (OpenAI, Azure OpenAI, OpenRouter, and the site's /llm/openai and /llm/openrouter
# routes) uses the Chat Completions protocol, so the path is always …/chat/completions and nothing depends on a
# conversation stored at the provider: the designer keeps each session's history itself. Anthropic has its own client.
#
# A half-filled gateway configuration (a key that is still a <placeholder>, or another GATEWAY_* setting without the
# key) is refused at startup (GatewayConfigurationError), so a deployment meant for the gateway never runs open.
# Every model call, in both modes, goes through one policy transport (below): the proxy's problem answers
# (gateway) or the provider's classified failures (standalone) become problems.Problem, and the retry rules for paid
# calls live there. The site's provider text never reaches a problem.
# The other side of the contract (the route, the proxy, the allowlist, the budgets) belongs to the AgentBullpen.AI site.
import asyncio
import contextvars
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Protocol

# httpx2: both SDKs' HTTP stack (the anthropic SDK rejects a plain httpx client).
import httpx2 as httpx
import jwt

import images
import problems
import provider_failures

logger = logging.getLogger(__name__)

BUDGET_LEVEL_HEADER = "X-Budget-Level"
BUDGET_DAILY_HEADER = "X-Budget-Daily-Usd"
BUDGET_REMAINING_HEADER = "X-Budget-Remaining-Usd"
BUDGET_RESETS_HEADER = "X-Budget-Resets-At"
CALL_COST_HEADER = "X-Call-Cost-Usd"
CONVERSATION_HEADER = "X-Conversation-Id"
# What the SDK clients are built with in gateway mode; the transport hook replaces it with the request's JWT on every call.
PLACEHOLDER_KEY = "gateway-token-not-set"


def _setting(name: str, default: str = "") -> str:
    """The env var's value; a value still starting with `<` (a template placeholder) counts as unset."""
    value = os.environ.get(name, "").strip()
    return default if not value or value.startswith("<") else value


# --- Per-request state --------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class User:
    sub: str  # "" for the anonymous local user
    email: str = ""
    name: str = ""
    role: tuple[str, ...] = ()


LOCAL_USER = User(sub="")


@dataclass
class RequestState:
    """Set by main.py's middleware for each HTTP request. Mutable on purpose: tool calls run in tasks with copied
    contexts, and a refusal recorded there must be seen by the turn that started them."""

    user: User = LOCAL_USER
    token: str | None = field(default=None, repr=False)  # the raw gateway JWT (gateway mode only); never logged
    level: str | None = None  # X-Budget-Level: off, light, standard, generous, custom or none
    daily_usd: float | None = None  # X-Budget-Daily-Usd
    remaining_usd: float | None = None  # X-Budget-Remaining-Usd: what was left when the request arrived
    resets_at: str | None = None  # X-Budget-Resets-At
    cost_usd: float = 0.0  # this turn's model calls that came with a cost, summed
    unpriced: int = 0  # this turn's model calls that came without one
    problem: "problems.Problem | None" = None  # the latest failed model call's problem (cleared by whoever handles it)
    refusal: "problems.Problem | None" = None  # the first turn-ending one (budget, signed out, model not allowed)
    painting: bool = False  # this turn began a paint: a rate-limited chat call is now retried here, not by the web
    fallback: str | None = None  # X-Model-Fallback of the last chat answer: "<requested> -> <used>"


_request: contextvars.ContextVar[RequestState | None] = contextvars.ContextVar("cover_request", default=None)
# The design session a model call belongs to (X-Conversation-Id); set by the designer for each turn.
conversation: contextvars.ContextVar[str | None] = contextvars.ContextVar("cover_conversation", default=None)


def begin(state: RequestState) -> contextvars.Token:
    return _request.set(state)


def end(token: contextvars.Token) -> None:
    _request.reset(token)


def current() -> RequestState:
    return _request.get() or RequestState()


def current_user() -> User:
    return current().user


def refusal() -> "problems.Problem | None":
    """A turn-ending problem earlier in this request (budget, signed out, model not allowed), or None."""
    return current().refusal


def begin_turn() -> None:
    """A new turn: nothing spent yet."""
    state = current()
    state.cost_usd, state.unpriced = 0.0, 0


def turn_cost() -> float | None:
    """What this turn's model calls cost, or None when any of them came without a cost (a partial sum is never
    shown as the turn's cost) or none was made."""
    state = current()
    return round(state.cost_usd, 6) if state.cost_usd > 0 and state.unpriced == 0 else None


def _usd(value) -> float | None:
    """A dollar amount from a header or a JSON number; None when it isn't one."""
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    return amount if amount >= 0 and amount == amount and amount != float("inf") else None


# --- Settings ----------------------------------------------------------------------------------------------------

GATEWAY_KEY = "GATEWAY_SIGNING_KEY"
# The other six settings: one of them set means the gateway was meant.
GATEWAY_ONLY = ("GATEWAY_ISSUER", "GATEWAY_AUDIENCE", "GATEWAY_LLM_BASE_URL", "GATEWAY_CHAT_MODEL", "GATEWAY_CONCEPT_MODEL",
                "GATEWAY_IMAGE_MODEL")


class GatewayConfigurationError(Exception):
    """The gateway settings are half-filled: the api would run standalone, with no sign-in. main.py stops on it."""


def check_gateway_configuration() -> None:
    """Refuses a signing key that is still a <placeholder>, and an empty key beside any other gateway setting (a
    <placeholder> in those counts as unset). All seven unset is standalone."""
    key = os.environ.get(GATEWAY_KEY, "").strip()
    if key.startswith("<"):
        raise GatewayConfigurationError(
            f"{GATEWAY_KEY} is still a <placeholder>. Set the key for gateway mode, or clear it for standalone.")
    if not key and (name := next((n for n in GATEWAY_ONLY if _setting(n)), None)):
        raise GatewayConfigurationError(
            f"{name} is set but {GATEWAY_KEY} is empty. Set the key for gateway mode, or clear {name} for standalone.")


@dataclass(frozen=True)
class GatewaySettings:
    signing_key: str = field(repr=False)
    issuer: str
    audience: str
    llm_base_url: str  # the site's …/llm, no trailing slash
    chat_model: str  # provider:model, the default chat model
    concept_model: str  # provider:model, config (not picked)
    image_model: str  # provider:model, config (not picked)

    @staticmethod
    def from_env() -> "GatewaySettings":
        check_gateway_configuration()
        return GatewaySettings(
            signing_key=_setting(GATEWAY_KEY),
            issuer=_setting("GATEWAY_ISSUER", "https://api.agentbullpen.ai"),
            audience=_setting("GATEWAY_AUDIENCE", "covers"),
            llm_base_url=_setting("GATEWAY_LLM_BASE_URL", "https://api.agentbullpen.ai/llm").rstrip("/"),
            chat_model=_setting("GATEWAY_CHAT_MODEL", "openai:gpt-4o-mini"),
            concept_model=_setting("GATEWAY_CONCEPT_MODEL", "openai:gpt-6-luna"),
            image_model=_setting("GATEWAY_IMAGE_MODEL", "openai:gpt-image-2.5-flare"),
        )

    @property
    def enabled(self) -> bool:
        return bool(self.signing_key)


def split_model(model_id: str) -> tuple[str, str]:
    """"openai:gpt-4o-mini" → ("openai", "gpt-4o-mini"); the model part may itself hold a "/" (OpenRouter)."""
    provider, _, model = model_id.partition(":")
    return provider.lower(), model


# --- Model calls: one policy, both modes -----------------------------------------------------------------------------
# Every model call (chat, concept, image, the site's model list) goes through a policy transport:
#   gateway     the request's JWT replaces the SDK's placeholder key (TemporalGraph's GatewayCredentialHandler) and the
#               site's problem answers are read (problems.from_proxy);
#   standalone  the provider's own failures are classified with the site's rules (provider_failures).
# The failure becomes a Problem on the request state; designer.py decides what it means for the turn.
# Retry rules for the paid calls. The SDK clients have max_retries=0, so these are the only retries:
#   429 rate-limited  wait retryAfterSeconds (at most 30 s) and send once more: the image and concept calls, and a chat
#                     call after this turn began a paint (sending the turn again would pay for those again). A chat
#                     call's 429 before that ends the turn; the web says "One moment…" and sends the message once more.
#   5xx, no answer    the image call never (not idempotent: it would be billed twice); a standalone chat call once, at
#                     once; gateway never (the proxy already retried once); the concept call's own loop retries it.
#   account failures, refusals  never.

RATE_LIMIT_WAIT_CAP = 30.0
FALLBACK_HEADER = "X-Model-Fallback"


class GatewayTokenMissing(Exception):
    """A model call in gateway mode with no gateway JWT on the request: nothing is sent."""


def _credentials(request: httpx.Request, anthropic: bool) -> None:
    """The request's JWT replaces whatever key the SDK put on (it runs after the SDK, on the wire request)."""
    token = current().token
    if not token:
        record(problems.make("unauthorized"))
        raise GatewayTokenMissing("No gateway token on this request")
    request.headers.pop("authorization", None)
    request.headers.pop("x-api-key", None)
    if anthropic:
        request.headers["x-api-key"] = token
    else:
        request.headers["authorization"] = f"Bearer {token}"
    if conversation_id := conversation.get():
        request.headers[CONVERSATION_HEADER] = conversation_id[:128]


def record(problem: problems.Problem) -> None:
    """The latest model-call problem of this request; the first turn-ending one is also kept as its refusal."""
    state = current()
    state.problem = problem
    if problem.slug in problems.TURN_ENDING and state.refusal is None:
        state.refusal = problem


@dataclass(frozen=True)
class CallPolicy:
    kind: str  # chat | concept | image | catalog
    gateway: bool
    provider: str = ""  # standalone: openai | anthropic | azure | openrouter | "" (another endpoint)
    anthropic: bool = False  # the JWT goes in x-api-key (gateway)
    key_parameter: str = "llm-api-key"  # the AppHost secret the standalone fix-it text names

    def prepare(self, request: httpx.Request) -> None:
        if self.gateway:
            _credentials(request, self.anthropic)

    def problem(self, status: int, headers, body: bytes) -> problems.Problem:
        if self.gateway:
            return problems.from_proxy(status, body, image=self.kind == "image")
        f = provider_failures.classify(self.provider, status, headers, body, image=self.kind == "image")
        if f.kind == provider_failures.UNAVAILABLE:
            return problems.make("provider-unavailable", provider_failures.fix_it(self.provider, f.reason, self.key_parameter),
                                 reason=f.reason, provider=provider_failures.label(self.provider) if self.provider else None)
        if f.kind == provider_failures.RATE_LIMITED:
            return problems.make("rate-limited", retryAfterSeconds=f.retry_after)
        if f.kind == provider_failures.TRANSIENT:
            return problems.make("upstream-error")
        if f.kind == provider_failures.CONTENT_POLICY:
            return problems.make("content-policy", "The image service declined that picture.")
        return problems.make("upstream-rejected", status=f.status)

    def wait_before_retry(self, problem: problems.Problem, status: int) -> float | None:
        """Seconds to wait before the one retry, or None for no retry."""
        if problem.slug == "rate-limited" and (self.kind in ("image", "concept") or (self.kind == "chat" and current().painting)):
            return min(float(problem.extensions.get("retryAfterSeconds") or 5), RATE_LIMIT_WAIT_CAP)
        if problem.slug == "upstream-error" and status >= 500 and self.kind == "chat" and not self.gateway:
            return 0.0
        return None

    def succeeded(self, response: httpx.Response, body: bytes | None = None) -> None:
        # A chat answer from the site's fallback model says so: "<requested> -> <used>".
        if self.kind == "chat" and (fallback := response.headers.get(FALLBACK_HEADER)):
            current().fallback = fallback
        if self.kind in ("chat", "concept", "image"):
            self.count_cost(response, body)

    @property
    def reads_body(self) -> bool:
        """Standalone chat and concept answers are read for the provider's own cost. Not the image's: OpenAI's Images
        answer has none, and it is megabytes of base64."""
        return not self.gateway and self.kind in ("chat", "concept")

    def count_cost(self, response: httpx.Response, body: bytes | None) -> None:
        """This call's cost goes on the turn. Gateway: the site's X-Call-Cost-Usd. Standalone: the provider's own
        `usage.cost` when it sends one (OpenRouter); it is never estimated."""
        cost = None
        if self.gateway:
            cost = _usd(response.headers.get(CALL_COST_HEADER))
        elif body:
            try:
                usage = json.loads(body).get("usage")
                cost = _usd(usage.get("cost")) if isinstance(usage, dict) else None
            except (ValueError, AttributeError):
                cost = None
        state = current()
        if cost is None:
            state.unpriced += 1
        else:
            state.cost_usd += cost

    def failed(self, problem: problems.Problem, status: int | None) -> None:
        record(problem)
        logger.warning("A %s call failed: %s → %s%s", self.kind, f"HTTP {status}" if status else "no answer", problem.slug,
                       f" ({problem.reason})" if problem.reason else "")


class _AsyncPolicyTransport(httpx.AsyncBaseTransport):
    def __init__(self, policy: CallPolicy, inner: httpx.AsyncBaseTransport):
        self.policy, self.inner = policy, inner

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.policy.prepare(request)
        retried = False
        while True:
            try:
                response = await self.inner.handle_async_request(request)
            except httpx.TimeoutException:
                self.policy.failed(problems.make("timeout"), None)
                raise
            except httpx.TransportError:
                self.policy.failed(problems.make("upstream-error"), None)
                raise
            if response.status_code < 400:
                self.policy.succeeded(response, await response.aread() if self.policy.reads_body else None)
                return response
            body = await response.aread()
            problem = self.policy.problem(response.status_code, response.headers, body)
            wait = None if retried else self.policy.wait_before_retry(problem, response.status_code)
            if wait is None:
                self.policy.failed(problem, response.status_code)
                return response
            logger.info("Retrying a %s call once after HTTP %d (%s), in %.0f s", self.policy.kind, response.status_code, problem.slug, wait)
            await asyncio.sleep(wait)
            retried = True

    async def aclose(self) -> None:
        await self.inner.aclose()


class _PolicyTransport(httpx.BaseTransport):
    """The same for the image call, which runs the sync OpenAI SDK on a worker thread (the context is copied there)."""

    def __init__(self, policy: CallPolicy, inner: httpx.BaseTransport):
        self.policy, self.inner = policy, inner

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.policy.prepare(request)
        retried = False
        while True:
            try:
                response = self.inner.handle_request(request)
            except httpx.TimeoutException:
                self.policy.failed(problems.make("timeout"), None)
                raise
            except httpx.TransportError:
                self.policy.failed(problems.make("upstream-error"), None)
                raise
            if response.status_code < 400:
                self.policy.succeeded(response)
                return response
            body = response.read()
            problem = self.policy.problem(response.status_code, response.headers, body)
            wait = None if retried else self.policy.wait_before_retry(problem, response.status_code)
            if wait is None:
                self.policy.failed(problem, response.status_code)
                return response
            logger.info("Retrying a %s call once after HTTP %d (%s), in %.0f s", self.policy.kind, response.status_code, problem.slug, wait)
            time.sleep(wait)
            retried = True

    def close(self) -> None:
        self.inner.close()


def async_http_client(policy: CallPolicy, transport: httpx.AsyncBaseTransport | None = None, timeout: float = 600) -> httpx.AsyncClient:
    """An httpx client for the async SDK clients (chat, concepts) and the model list."""
    return httpx.AsyncClient(transport=_AsyncPolicyTransport(policy, transport or httpx.AsyncHTTPTransport()), timeout=timeout)


def sync_http_client(policy: CallPolicy, transport: httpx.BaseTransport | None = None, timeout: float = 600) -> httpx.Client:
    """The same for the image call."""
    return httpx.Client(transport=_PolicyTransport(policy, transport or httpx.HTTPTransport()), timeout=timeout)


# --- Adapter 1: current user ------------------------------------------------------------------------------------

class Unauthorized(Exception):
    pass


class Users(Protocol):
    def authenticate(self, headers) -> tuple[User, str | None]: ...


class LocalUsers:
    """Standalone: no authentication; everyone is the anonymous local user."""

    def authenticate(self, headers) -> tuple[User, str | None]:
        return LOCAL_USER, None


class GatewayUsers:
    """Gateway: `Authorization: Bearer <jwt>` from the site, HS256 only, issuer, audience, expiry with 30 s leeway."""

    def __init__(self, s: GatewaySettings):
        self.s = s

    def authenticate(self, headers) -> tuple[User, str]:
        header = headers.get("authorization", "")
        if not header.startswith("Bearer "):
            raise Unauthorized("no bearer token")
        token = header[7:].strip()
        try:
            claims = jwt.decode(
                token, self.s.signing_key, algorithms=["HS256"], audience=self.s.audience, issuer=self.s.issuer,
                leeway=30, options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError as e:
            raise Unauthorized(type(e).__name__) from None
        role = claims.get("role") or ()
        role = (role,) if isinstance(role, str) else tuple(role)
        return User(sub=str(claims["sub"]), email=claims.get("email", ""), name=claims.get("name", ""), role=role), token


# --- Adapter 4: budget ------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Budget:
    """The member's daily allowance in US dollars. level: off, light, standard, generous, custom or none; None
    standalone. daily_usd: None for none and off. resets_at_utc: None standalone and for off (nothing resets it)."""

    level: str | None
    daily_usd: float | None
    remaining_usd: float | None
    unlimited: bool
    resets_at_utc: str | None = None


class Budgets(Protocol):
    def read(self, headers, state: RequestState) -> None: ...

    def current(self, spent: float | None = None) -> Budget | None: ...


class LocalBudget:
    def read(self, headers, state: RequestState) -> None:
        return None

    def current(self, spent: float | None = None) -> Budget:
        return Budget(level=None, daily_usd=None, remaining_usd=None, unlimited=True)


class GatewayBudget:
    """The site's allowance headers on each forwarded request (the balance when it arrived); null without them.
    `spent`: this turn's cost, taken off what is left; None (a call without a cost) leaves the balance alone."""

    def read(self, headers, state: RequestState) -> None:
        state.level = (headers.get(BUDGET_LEVEL_HEADER) or "").strip().lower() or None
        state.daily_usd = _usd(headers.get(BUDGET_DAILY_HEADER))
        state.remaining_usd = _usd(headers.get(BUDGET_REMAINING_HEADER))
        state.resets_at = (headers.get(BUDGET_RESETS_HEADER) or "").strip() or None

    def current(self, spent: float | None = None) -> Budget | None:
        state = current()
        if state.level is None and state.daily_usd is None and state.remaining_usd is None:
            return None
        if state.level == "off":
            return Budget(level="off", daily_usd=None, remaining_usd=0.0, unlimited=False)
        if state.level == "none":
            return Budget(level="none", daily_usd=None, remaining_usd=None, unlimited=True)
        remaining = state.remaining_usd
        if remaining is not None and spent:
            remaining = round(max(0.0, remaining - spent), 6)
        return Budget(level=state.level, daily_usd=state.daily_usd, remaining_usd=remaining, unlimited=False,
                      resets_at_utc=state.resets_at or problems.next_utc_midnight())


# --- Adapter 3: model catalog -----------------------------------------------------------------------------------

@dataclass(frozen=True)
class ChatModel:
    id: str  # standalone: the configured model; gateway: provider:model
    display_name: str
    provider: str
    is_default: bool
    available: bool = True  # false while the site has tripped the model's provider
    unavailable_until_utc: str | None = None


class Catalog(Protocol):
    async def chat_models(self) -> list[ChatModel]: ...


class LocalCatalog:
    def __init__(self, settings):
        self.settings = settings

    async def chat_models(self) -> list[ChatModel]:
        s = self.settings
        if not s.available()[0]:
            return []
        return [ChatModel(id=s.model, display_name=s.model, provider=s.type.lower(), is_default=True)]


class GatewayCatalog:
    """GET {GATEWAY_LLM_BASE_URL}/models with the member's JWT: the site's admin-authorised list, chat models only,
    cached for 60 s (it is the site's list, the same for every member)."""

    TTL = 60.0

    def __init__(self, s: GatewaySettings, transport: httpx.AsyncBaseTransport | None = None):
        self.s = s
        self.transport = transport
        self._cache: tuple[float, list[ChatModel]] | None = None

    @staticmethod
    def parse(data: dict, default_id: str) -> list[ChatModel]:
        models = []
        for p in data.get("providers") or []:
            for m in p.get("models") or []:
                if (m.get("kind") or "chat") != "chat" or not m.get("id"):
                    continue
                model_id = f"{p.get('name', '').lower()}:{m['id']}"
                label = (m.get("displayName") or "").strip() or model_id
                available = m.get("available", p.get("available", True)) is not False
                until = m.get("unavailableUntilUtc") or p.get("unavailableUntilUtc")
                models.append(ChatModel(model_id, label, p.get("name", "").lower(), False, available, None if available else until))
        # The default is always an available model (TemporalGraph's rule): the configured one, else the first available.
        usable = [m for m in models if m.available]
        default = default_id if any(m.id == default_id for m in usable) else (usable[0].id if usable else None)
        return [ChatModel(m.id, m.display_name, m.provider, m.id == default, m.available, m.unavailable_until_utc) for m in models]

    async def chat_models(self) -> list[ChatModel]:
        if self._cache and time.monotonic() - self._cache[0] < self.TTL:
            return self._cache[1]
        policy = CallPolicy("catalog", gateway=True)
        async with async_http_client(policy, transport=self.transport, timeout=30) as client:
            response = await client.get(f"{self.s.llm_base_url}/models")
        if response.status_code != 200:
            raise CatalogError(current().problem or problems.make("upstream-error"))
        models = self.parse(response.json(), self.s.chat_model)
        self._cache = (time.monotonic(), models)
        return models


class CatalogError(problems.ProblemError):
    """The site's model list failed: its problem (signed out, rate limited, …)."""


# --- Adapter 2: model calls ---------------------------------------------------------------------------------------

class ModelCalls(Protocol):
    def chat_available(self) -> tuple[bool, str | None]: ...

    def images_available(self) -> tuple[bool, str | None]: ...

    def chat_client(self, model_id: str): ...

    def model_name(self, model_id: str) -> str: ...

    def concept_client(self): ...

    @property
    def concept_model(self) -> str: ...

    @property
    def concept_reasoning_effort(self) -> str | None: ...

    @property
    def image_settings(self) -> images.ImageSettings: ...

    def image_http_client(self) -> httpx.Client: ...


class LocalModelCalls:
    """Standalone: today's LLM_* chat settings and IMAGE_* image settings, with their own keys."""

    def __init__(self, settings, image_settings: images.ImageSettings):
        self.settings = settings
        self._image_settings = image_settings
        self.transport: httpx.AsyncBaseTransport | None = None  # tests: a fake provider
        self.sync_transport: httpx.BaseTransport | None = None

    def chat_available(self):
        return self.settings.available()

    def images_available(self):
        return self._image_settings.available()

    def _client(self, model: str, kind: str):
        # The SDK clients get the policy transport (classification and the retry rules) and max_retries=0.
        s = self.settings
        policy = CallPolicy(kind, gateway=False, provider=provider_failures.provider_of(s.type, s.endpoint))
        if s.type == "Anthropic":
            from anthropic import AsyncAnthropic
            from agent_framework.anthropic import AnthropicClient

            sdk = AsyncAnthropic(api_key=s.api_key, max_retries=0, http_client=async_http_client(policy, self.transport))
            return AnthropicClient(model=model, anthropic_client=sdk)
        from openai import AsyncOpenAI
        from agent_framework.openai import OpenAIChatCompletionClient

        # Chat Completions, the one protocol OpenAI, Azure OpenAI (…/openai/v1/) and OpenRouter all serve.
        sdk = AsyncOpenAI(api_key=s.api_key, base_url=s.endpoint or None, max_retries=0, http_client=async_http_client(policy, self.transport))
        return OpenAIChatCompletionClient(model=model, async_client=sdk)

    def chat_client(self, model_id: str):
        return self._client(model_id, "chat")

    def model_name(self, model_id: str) -> str:
        return model_id

    def concept_client(self):
        return self._client(self.settings.concepts_model, "concept")

    @property
    def concept_model(self) -> str:
        return self.settings.concepts_model

    @property
    def concept_reasoning_effort(self) -> str | None:
        return (self.settings.concept_reasoning_effort if self.settings.type == "OpenAI" else "") or None

    @property
    def image_settings(self) -> images.ImageSettings:
        return self._image_settings

    def image_http_client(self) -> httpx.Client:
        s = self._image_settings
        policy = CallPolicy("image", gateway=False, provider=provider_failures.provider_of(s.type, s.endpoint), key_parameter="image-api-key")
        return sync_http_client(policy, self.sync_transport, timeout=s.timeout_seconds)


class GatewayModelCalls:
    """Gateway: every call goes to the site's proxy, {GATEWAY_LLM_BASE_URL}/{provider} + the path the SDK would use,
    with the request's JWT as the key (async_http_client / sync_http_client). The SDK clients get max_retries=0, so
    a refusal (the 429 included) is never retried, and the paid image call never is either."""

    OPENAI_COMPATIBLE = ("openai", "openrouter", "azure")

    def __init__(self, s: GatewaySettings, timeout_seconds: float, image_size: str, image_quality: str, image_timeout: float,
                 transport: httpx.AsyncBaseTransport | None = None, sync_transport: httpx.BaseTransport | None = None,
                 concept_reasoning_effort: str = ""):
        self.s = s
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        self.sync_transport = sync_transport
        self._effort = concept_reasoning_effort
        provider, model = split_model(s.image_model)
        self._image_settings = images.ImageSettings(
            type="OpenAI", model=model, endpoint=self.base_url(provider), api_key=PLACEHOLDER_KEY,
            size=image_size, quality=image_quality, timeout_seconds=image_timeout,
        )

    def base_url(self, provider: str) -> str:
        """The provider's host replaced by {base}/{provider}, the rest of the SDK's URL kept."""
        if provider == "anthropic":
            return f"{self.s.llm_base_url}/anthropic"  # the SDK adds /v1/messages
        if provider == "azure":
            return f"{self.s.llm_base_url}/azure/openai/v1/"
        return f"{self.s.llm_base_url}/{provider}/v1/"

    def chat_available(self):
        return True, None  # whether the site lists a chat model is the catalog's call (main.py availability)

    def images_available(self):
        return True, None

    def chat_client(self, model_id: str, kind: str = "chat"):
        provider, model = split_model(model_id)
        if provider == "anthropic":
            from anthropic import AsyncAnthropic
            from agent_framework.anthropic import AnthropicClient

            sdk = AsyncAnthropic(api_key=PLACEHOLDER_KEY, base_url=self.base_url(provider), max_retries=0,
                                 http_client=async_http_client(CallPolicy(kind, gateway=True, anthropic=True), self.transport))
            return AnthropicClient(model=model, anthropic_client=sdk)
        from openai import AsyncOpenAI
        from agent_framework.openai import OpenAIChatCompletionClient

        sdk = AsyncOpenAI(api_key=PLACEHOLDER_KEY, base_url=self.base_url(provider), max_retries=0,
                          http_client=async_http_client(CallPolicy(kind, gateway=True), self.transport))
        return OpenAIChatCompletionClient(model=model, async_client=sdk)

    def model_name(self, model_id: str) -> str:
        return split_model(model_id)[1] or model_id

    def concept_client(self):
        return self.chat_client(self.s.concept_model, "concept")

    @property
    def concept_model(self) -> str:
        return self.model_name(self.s.concept_model)

    @property
    def concept_reasoning_effort(self) -> str | None:
        return (self._effort if split_model(self.s.concept_model)[0] in self.OPENAI_COMPATIBLE else "") or None

    @property
    def image_settings(self) -> images.ImageSettings:
        return self._image_settings

    def image_http_client(self) -> httpx.Client:
        return sync_http_client(CallPolicy("image", gateway=True), self.sync_transport, timeout=self._image_settings.timeout_seconds)


# --- The mode, chosen once ----------------------------------------------------------------------------------------

@dataclass
class Mode:
    gateway: bool
    users: Users
    calls: ModelCalls
    catalog: Catalog
    budget: Budgets
    default_chat_model: str


def choose(settings, image_settings: images.ImageSettings, gateway: GatewaySettings | None = None) -> Mode:
    """Gateway when GATEWAY_SIGNING_KEY is set, else standalone. Half-filled gateway settings raise
    GatewayConfigurationError."""
    g = gateway or GatewaySettings.from_env()
    if not g.enabled:
        return Mode(False, LocalUsers(), LocalModelCalls(settings, image_settings), LocalCatalog(settings), LocalBudget(), settings.model)
    calls = GatewayModelCalls(
        g, settings.timeout_seconds, image_settings.size, image_settings.quality, image_settings.timeout_seconds,
        concept_reasoning_effort=settings.concept_reasoning_effort,
    )
    logger.info("Gateway mode: issuer %s, audience %s, LLM proxy %s", g.issuer, g.audience, g.llm_base_url)
    return Mode(True, GatewayUsers(g), calls, GatewayCatalog(g), GatewayBudget(), g.chat_model)
