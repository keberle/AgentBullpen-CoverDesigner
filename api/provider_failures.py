# Standalone: this demo calls the providers itself, so it classifies their failures itself with the site's rules
# (copied here, no shared package). The recorded response
# shapes are in tests/fixtures/provider-failures.json, each with its source.
#   402 (OpenRouter): key-limit when error.metadata.limit_source names the key, else credits; any other 402 → credits.
#   400 whose message says the credit balance is too low (Anthropic) → credits.
#   429: insufficient_quota (type or code), "exceeded your current quota", or "quota" as a word → credits (OpenAI) /
#        quota (Azure). OpenAI's documented billing codes (credit_balance_exhausted → credits; the spend and usage
#        limits → quota). A wait over 60 s → quota. Any other 429 → rate-limited with the provider's wait.
#   401, and 403 except OpenRouter's (its moderation) → auth.
#   5xx and Anthropic's 529 → transient. Any other 4xx → rejected; on the image call OpenAI's moderation_blocked →
#   content policy.
import json
import re
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone

QUOTA_WAIT_SECONDS = 60

UNAVAILABLE, RATE_LIMITED, TRANSIENT, REJECTED, CONTENT_POLICY = "unavailable", "rate-limited", "transient", "rejected", "content-policy"

# OpenAI's 429 billing codes (developers.openai.com/api/docs/guides/error-codes).
OPENAI_BILLING = {
    "credit_balance_exhausted": "credits",
    "insufficient_quota": "credits",
    "organization_spend_limit_exceeded": "quota",
    "project_spend_limit_exceeded": "quota",
    "organization_usage_limit_exceeded": "quota",
}
# The image call's safety refusals: GPT Image's documented error.code (developers.openai.com/api/docs/guides/
# image-generation: type image_generation_user_error, code moderation_blocked) and DALL·E's older one.
MODERATION_CODES = {"moderation_blocked", "content_policy_violation"}


@dataclass(frozen=True)
class Failure:
    kind: str  # UNAVAILABLE, RATE_LIMITED, TRANSIENT, REJECTED, CONTENT_POLICY
    status: int
    reason: str | None = None  # UNAVAILABLE: credits | key-limit | quota | auth
    retry_after: int | None = None  # seconds


def provider_of(llm_type: str, endpoint: str) -> str:
    """openai | anthropic | azure | openrouter, from the configured type and endpoint; "" for any other endpoint."""
    host = (endpoint or "").lower()
    if llm_type.lower() == "anthropic":
        return "anthropic"
    if not host:
        return "openai"
    if "openrouter.ai" in host:
        return "openrouter"
    if ".azure.com" in host or ".azure-api.net" in host:
        return "azure"
    if "api.openai.com" in host:
        return "openai"
    return ""


def label(provider: str) -> str:
    """The provider's name for the person running this copy (their own account); "model provider" for an endpoint."""
    return {"openai": "OpenAI", "anthropic": "Anthropic", "azure": "Azure OpenAI", "openrouter": "OpenRouter"}.get(provider, "model provider")


def fix_it(provider: str, reason: str, key_parameter: str) -> str:
    """What to do: no failover and no email standalone, so the message says how to fix it."""
    if reason == "auth":
        return f"Your {label(provider)} key was rejected. Set it again with `aspire secret set Parameters:{key_parameter} <key>`."
    return (f"Your {label(provider)} account can't serve requests right now ({reason}). Top it up, or switch to another"
            " provider in docs › Configuration.")


def _error(body: bytes) -> dict:
    try:
        data = json.loads(body or b"{}")
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    inner = data.get("error")
    return inner if isinstance(inner, dict) else data


def _str(value) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None


def _contains(text: str | None, what: str) -> bool:
    return bool(text) and what.lower() in text.lower()


def retry_after_seconds(headers, message: str | None) -> int | None:
    """Retry-After (seconds or a date), Azure's retry-after-ms, else "retry after N seconds" in the message."""
    value = headers.get("retry-after") if headers is not None else None
    if value:
        value = value.strip()
        if value.isdigit():
            return int(value)
        try:
            return max(0, int((parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds() + 0.999))
        except (TypeError, ValueError):
            pass
    ms = headers.get("retry-after-ms") if headers is not None else None
    if ms:
        try:
            return int(float(ms) / 1000 + 0.999)
        except ValueError:
            pass
    if message and (m := re.search(r"retry after (\d+) second", message, re.IGNORECASE)):
        return int(m.group(1))
    return None


def classify(provider: str, status: int, headers, body: bytes, image: bool = False) -> Failure:
    e = _error(body)
    message, code, kind = _str(e.get("message")), _str(e.get("code")), _str(e.get("type"))
    metadata = e.get("metadata") if isinstance(e.get("metadata"), dict) else {}
    retry_after = retry_after_seconds(headers, message)

    if status == 402:
        limit_source = _str(metadata.get("limit_source"))
        key_limit = _contains(limit_source, "key") if limit_source is not None else _contains(message, "key limit")
        return Failure(UNAVAILABLE, status, "key-limit" if provider == "openrouter" and key_limit else "credits")

    if status == 400 and _contains(message, "credit balance is too low"):
        return Failure(UNAVAILABLE, status, "credits")

    if status == 429:
        billing = OPENAI_BILLING.get(code or "") or OPENAI_BILLING.get(kind or "")
        spent = billing is not None or _contains(message, "exceeded your current quota") or (
            bool(message) and re.search(r"\bquota\b", message, re.IGNORECASE) is not None and not _contains(message, "rate limit"))
        if spent:
            return Failure(UNAVAILABLE, status, "quota" if provider == "azure" else (billing or "credits"))
        if retry_after is not None and retry_after > QUOTA_WAIT_SECONDS:
            return Failure(UNAVAILABLE, status, "quota")
        return Failure(RATE_LIMITED, status, retry_after=max(1, retry_after if retry_after is not None else 10))

    if status == 401 or (status == 403 and provider != "openrouter"):
        return Failure(UNAVAILABLE, status, "auth")

    if status >= 500:
        return Failure(TRANSIENT, status)

    if image and (code in MODERATION_CODES or kind == "image_generation_user_error"):
        return Failure(CONTENT_POLICY, status)

    return Failure(REJECTED, status if 400 <= status < 500 else 502)
