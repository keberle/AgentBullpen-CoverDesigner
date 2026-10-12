# One error model for both transports, RFC 9457 problem details (docs/design/errors.md). A Problem is built where
# a failure is understood (a model call's response, a ChatError, an unknown session) and leaves the API as:
#   REST     application/problem+json: type, title, status, detail, traceId and the typed extensions (main.py's handlers);
#   GraphQL  the error's extensions.code (today's codes, kept) plus extensions.problem (main.py's ProblemRouter).
# Type URIs are the AgentBullpen.AI site's: https://agentbullpen.ai/problems/<slug>, for the site's types
# and this demo's own. Every problem sent increments coverdesigner.problems{type,reason} and marks the span Error.
# Never in a problem: the provider's own error text, a key, an endpoint, or (gateway) the site's provider account.
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from opentelemetry import metrics, trace

logger = logging.getLogger(__name__)

BASE = "https://agentbullpen.ai/problems/"

# slug → (status, title, GraphQL code). The first ten are the site's; the rest this demo's.
CATALOGUE: dict[str, tuple[int, str, str]] = {
    "budget-exhausted": (403, "Daily allowance used up", "BUDGET_EXHAUSTED"),
    "chat-off": (403, "The designer is off for this account", "CHAT_OFF"),
    "rate-limited": (429, "Too many requests", "RATE_LIMITED"),
    "model-not-allowed": (403, "Model not allowed", "UNKNOWN_MODEL"),
    "provider-unavailable": (503, "Model provider unavailable", "PROVIDER_UNAVAILABLE"),
    "upstream-error": (502, "Model provider error", "MODEL_ERROR"),
    "unauthorized": (401, "Unauthorized", "UNAUTHORIZED"),
    "upstream-rejected": (400, "Request rejected by the model provider", "MODEL_ERROR"),
    "request-too-large": (413, "Request too large", "MODEL_ERROR"),
    "provider-not-found": (404, "Model provider not available", "UNKNOWN_MODEL"),
    "model-not-configured": (503, "No chat model configured", "NO_MODEL"),
    "images-not-configured": (503, "Image generation not configured", "IMAGES_NOT_CONFIGURED"),
    "timeout": (504, "The model call timed out", "TIMEOUT"),
    "content-policy": (422, "The image service declined the picture", "CONTENT_POLICY"),
    "concept-failed": (502, "The concept step failed", "CONCEPT_ERROR"),
    "unknown-session": (404, "Unknown design session", "UNKNOWN_SESSION"),
    "unknown-version": (404, "Unknown version", "UNKNOWN_VERSION"),
    "not-found": (404, "Not found", "NOT_FOUND"),
    "bad-request": (400, "Bad request", "BAD_REQUEST"),
    "internal-error": (500, "Internal error", "INTERNAL_ERROR"),
}

# Today's codes → their type (an error raised with a code only, and the web's fallback for an older API).
CODES = {code: slug for slug, (_, _, code) in CATALOGUE.items()} | {
    "MODEL_ERROR": "upstream-error",
    "UNKNOWN_MODEL": "model-not-allowed",
    "BAD_LICENSE": "bad-request",
    "IMAGE_ERROR": "upstream-error",
}

# A problem with one of these ends the turn and changes what the author can do (the input, the picker); any other
# problem inside paint_cover is a failed paint under a normal answer.
TURN_ENDING = {"budget-exhausted", "chat-off", "unauthorized", "model-not-allowed", "provider-not-found"}

# The extensions of the site's problems that reach the author (TemporalGraph's PassedExtensions); never `provider`
# (the site's account) or `detail` (the proxy's wording).
PASSED_EXTENSIONS = ("resetsAtUtc", "dailyAllowanceUsd", "usedTodayUsd", "level", "retryAfterSeconds", "reason", "model")

_meter = metrics.get_meter("CoverDesigner.Api")
_counter = _meter.create_counter("coverdesigner.problems", description="Problems returned to the web, by type and reason")


@dataclass
class Problem:
    slug: str
    detail: str | None = None
    extensions: dict = field(default_factory=dict)
    code: str | None = None  # the GraphQL code; None = the catalogue's
    status: int | None = None  # None = the catalogue's (upstream-rejected keeps the provider's 4xx)

    @property
    def type(self) -> str:
        return BASE + self.slug

    @property
    def title(self) -> str:
        return CATALOGUE.get(self.slug, CATALOGUE["internal-error"])[1]

    @property
    def http_status(self) -> int:
        return self.status or CATALOGUE.get(self.slug, CATALOGUE["internal-error"])[0]

    @property
    def graphql_code(self) -> str:
        return self.code or CATALOGUE.get(self.slug, CATALOGUE["internal-error"])[2]

    @property
    def reason(self) -> str:
        return str(self.extensions.get("reason") or "")

    @property
    def message(self) -> str:
        """The GraphQL error's message: the detail, else the title."""
        return self.detail or self.title

    def body(self) -> dict:
        """The RFC 9457 object (both transports), with the current trace id."""
        out = {"type": self.type, "title": self.title, "status": self.http_status}
        if self.detail:
            out["detail"] = self.detail
        out.update({k: v for k, v in self.extensions.items() if v is not None})
        if tid := trace_id():
            out["traceId"] = tid
        return out


class ProblemError(Exception):
    """Raised anywhere a request should end with this problem."""

    def __init__(self, problem: Problem):
        super().__init__(problem.message)
        self.problem = problem

    @property
    def code(self) -> str:
        return self.problem.graphql_code


def make(slug: str, detail: str | None = None, code: str | None = None, status: int | None = None, **extensions) -> Problem:
    return Problem(slug, detail, {k: v for k, v in extensions.items() if v is not None}, code, status)


def from_code(code: str, message: str | None = None) -> Problem:
    """A problem for one of today's codes (ChatError's)."""
    return Problem(CODES.get(code, "internal-error"), message, code=code)


def internal(trace: str | None = None) -> Problem:
    return make("internal-error", f"Something went wrong (ref {trace or trace_id() or 'unknown'}).")


def trace_id() -> str | None:
    """The current OpenTelemetry trace id, 32 hex characters (what the Aspire dashboard's trace search takes)."""
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None


def report(problem: Problem) -> None:
    """Counts the problem and marks the current span: once per problem sent to the web."""
    _counter.add(1, {"type": problem.slug, "reason": problem.reason})
    span = trace.get_current_span()
    span.set_attribute("problem.type", problem.slug)
    if problem.reason:
        span.set_attribute("problem.reason", problem.reason)
    span.set_status(trace.Status(trace.StatusCode.ERROR, problem.slug))


def next_utc_midnight(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).isoformat().replace("+00:00", "Z")


# --- The site's answers (gateway mode) --------------------------------------------------------------------------------

def from_proxy(status: int, body: bytes, image: bool = False) -> Problem:
    """The site's LLM proxy refused or failed a call. Its problem type decides; only PASSED_EXTENSIONS are
    kept. An older proxy without types still gets the status rules below."""
    try:
        data = json.loads(body or b"{}")
    except ValueError:
        data = {}
    data = data if isinstance(data, dict) else {}
    kind = data.get("type")
    ext = {k: data[k] for k in PASSED_EXTENSIONS if data.get(k) is not None}
    if isinstance(kind, str) and kind.startswith(BASE):
        slug = kind[len(BASE):]
        if slug == "upstream-rejected" and image and status in (400, 403):
            # The proxy doesn't pass the provider's body (it can name the account), so a moderation refusal (OpenAI's
            # 400 moderation_blocked, OpenRouter's 403) arrives as upstream-rejected. This demo's image request is
            # fixed (model, shape, quality from config), so a 400 or 403 on it is taken as the content policy.
            # [assumption]
            return make("content-policy", "The image service declined that picture.", via="upstream-rejected")
        if slug == "budget-exhausted":
            ext.setdefault("resetsAtUtc", next_utc_midnight())
        if slug in CATALOGUE:
            return make(slug, status=status if slug == "upstream-rejected" else None, **ext)
        return make("upstream-error")
    title = (data.get("title") or "").strip().lower()
    if status == 401:
        return make("unauthorized")
    if status == 403:
        return make("model-not-allowed") if title == "model not allowed" else make("budget-exhausted", resetsAtUtc=next_utc_midnight())
    if status == 429:
        return make("rate-limited", **ext)
    if status == 503:
        return make("provider-unavailable")
    return make("upstream-error")
