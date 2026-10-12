# Cover art: one plain call to the image provider, independent of the chat provider. Two protocols: OpenAI's Images
# API (…/images/generations, through its SDK) and OpenRouter's (POST …/images, plain JSON). Settings come from env
# vars (docs/guide/configuration.md); the app starts without them.
import asyncio
import base64
import io
import logging
import math
import os
import re
from dataclasses import dataclass, field

import httpx2 as httpx  # the SDKs' HTTP stack (modes.py)
import openai
from opentelemetry import trace
from PIL import Image

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

TYPES = ("OpenAI", "OpenRouter")
OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _setting(name: str) -> str:
    """The env var's value; a value still starting with `<` (a template placeholder) counts as unset."""
    value = os.environ.get(name, "").strip()
    return "" if value.startswith("<") else value


@dataclass(frozen=True)
class ImageSettings:
    type: str
    model: str
    endpoint: str
    api_key: str = field(repr=False)
    size: str
    quality: str
    timeout_seconds: float

    @staticmethod
    def from_env() -> "ImageSettings":
        try:
            timeout = float(_setting("IMAGE_TIMEOUT_SECONDS") or 120)
        except ValueError:
            timeout = 120.0
        return ImageSettings(
            type=_setting("IMAGE_TYPE"),
            model=_setting("IMAGE_MODEL"),
            endpoint=_setting("IMAGE_ENDPOINT"),
            api_key=_setting("IMAGE_API_KEY"),
            size=_setting("IMAGE_SIZE") or "1024x1536",
            quality=_setting("IMAGE_QUALITY") or "medium",
            timeout_seconds=timeout,
        )

    def available(self) -> tuple[bool, str | None]:
        """(True, None) when image generation is configured, else (False, the reason). Never includes the key."""
        missing = [n for n, v in (("IMAGE_TYPE", self.type), ("IMAGE_MODEL", self.model), ("IMAGE_API_KEY", self.api_key)) if not v]
        if missing:
            return False, f"Image generation not configured: {', '.join(missing)} not set."
        if self.type not in TYPES:
            return False, f"IMAGE_TYPE must be {' or '.join(TYPES)} (got {self.type!r})."
        return True, None


@dataclass
class ImageInfo:
    model: str
    size: str  # OpenAI: the size asked for; OpenRouter: the painted image's own pixels
    quality: str
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None = None  # the provider's own cost of this image, when it reports one (OpenRouter)


class ImageError(Exception):
    """The image call failed; the message is a one-line summary without the key or the response body."""


class ProviderError(Exception):
    """An OpenRouter images answer of 400 or above: the status and the parsed JSON body (never shown)."""

    def __init__(self, status_code: int, body):
        super().__init__(f"HTTP {status_code}")
        self.status_code, self.body = status_code, body


def _summary(error: BaseException) -> str:
    names, status, e = [], None, error
    while e is not None and len(names) < 4:
        names.append(type(e).__name__)
        status = status or getattr(e, "status_code", None)
        e = e.__cause__ or e.__context__
    return "The image call failed: " + " ← ".join(names) + (f" (HTTP {status})" if status else "")


def _api_error_code(error: BaseException) -> str | None:
    """The provider's error code/type (e.g. "model_not_found"), not its message."""
    body = getattr(error, "body", None)
    if isinstance(body, dict):
        body = body.get("error", body)
        if isinstance(body, dict):
            return body.get("code") or body.get("type")
    return getattr(error, "code", None)


def aspect_ratio(size: str) -> str | None:
    """"1024x1536" → "2:3"; None when the size isn't pixels (a tier, "auto")."""
    m = re.fullmatch(r"\s*(\d+)\s*[xX×]\s*(\d+)\s*", size or "")
    if not m or not int(m.group(1)) or not int(m.group(2)):
        return None
    w, h = int(m.group(1)), int(m.group(2))
    d = math.gcd(w, h)
    return f"{w // d}:{h // d}"


def _count(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _cost(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and value >= 0 else None


def _as_png(data: bytes) -> tuple[bytes, str]:
    """The image as PNG (the rest of the app expects one) and its pixels, "1024x1536"."""
    with Image.open(io.BytesIO(data)) as picture:
        size = f"{picture.width}x{picture.height}"
        if data.startswith(PNG_SIGNATURE):
            return data, size
        out = io.BytesIO()
        (picture if picture.mode in ("RGB", "RGBA") else picture.convert("RGBA" if "A" in picture.getbands() else "RGB")).save(out, format="PNG")
        return out.getvalue(), size


def _generate_openrouter(s: ImageSettings, prompt: str, http_client=None) -> tuple[bytes, ImageInfo, str | None]:
    # One POST, never sent twice here. http_client: the policy transport (modes.sync_http_client), which turns a
    # failure into a problem, retries only a 429 once and, in gateway mode, puts the request's JWT on the call.
    # The body holds only what image models list as supported: the shape goes as an aspect ratio (the provider
    # picks the pixels), and no output format is asked for; whatever comes back is made a PNG.
    body = {"model": s.model, "prompt": prompt, "n": 1, "quality": s.quality}
    if ratio := aspect_ratio(s.size):
        body["aspect_ratio"] = ratio
    client = http_client or httpx.Client(timeout=s.timeout_seconds)
    try:
        response = client.post((s.endpoint or OPENROUTER_ENDPOINT).rstrip("/") + "/images", json=body,
                               headers={"Authorization": f"Bearer {s.api_key}"})
    finally:
        client.close()
    try:
        answer = response.json()
    except ValueError:
        answer = None
    data = answer.get("data") if isinstance(answer, dict) else None
    if response.status_code >= 400 or not data:
        # No image in a 200 is a failed generation too (OpenRouter can report one in the body); nothing was billed.
        raise ProviderError(response.status_code, answer)
    item = data[0]
    png, size = _as_png(base64.b64decode(item["b64_json"]))
    usage = answer.get("usage") if isinstance(answer.get("usage"), dict) else {}
    info = ImageInfo(
        model=s.model,
        size=size,
        quality=s.quality,
        input_tokens=_count(usage.get("prompt_tokens")),
        output_tokens=_count(usage.get("completion_tokens")),
        cost_usd=_cost(usage.get("cost")),
    )
    return png, info, None


def _generate(s: ImageSettings, prompt: str, http_client=None) -> tuple[bytes, ImageInfo, str | None]:
    if s.type == "OpenRouter":
        return _generate_openrouter(s, prompt, http_client)
    # max_retries=0: an image call is paid and not idempotent, so the SDK never retries it. http_client: the policy
    # transport (modes.sync_http_client), which classifies a failure and retries only a 429 (refused before any
    # generation, so nothing is billed twice) once; in gateway mode it also puts the request's JWT on the call.
    client = openai.OpenAI(api_key=s.api_key, base_url=s.endpoint or None, timeout=s.timeout_seconds, max_retries=0, http_client=http_client)
    try:
        result = client.images.generate(model=s.model, prompt=prompt, size=s.size, quality=s.quality, output_format="png")
    finally:
        if http_client is not None:
            client.close()
    png = base64.b64decode(result.data[0].b64_json)
    usage = result.usage
    info = ImageInfo(
        model=s.model,
        size=s.size,
        quality=s.quality,
        input_tokens=usage.input_tokens if usage else None,
        output_tokens=usage.output_tokens if usage else None,
    )
    return png, info, getattr(result, "model", None)


async def generate_cover_art(s: ImageSettings, prompt: str, sensitive: bool = False, http_client=None) -> tuple[bytes, ImageInfo]:
    """One image for the prompt, in a `generate_content` span. Raises ImageError on any provider failure."""
    with tracer.start_as_current_span("generate_content") as span:
        span.set_attribute("gen_ai.operation.name", "generate_content")
        span.set_attribute("gen_ai.request.model", s.model)
        span.set_attribute("cover.image.size", s.size)
        span.set_attribute("cover.image.quality", s.quality)
        if sensitive:
            span.set_attribute("gen_ai.prompt", prompt)
        try:
            png, info, response_model = await asyncio.to_thread(_generate, s, prompt, http_client)
        except Exception as e:
            summary = _summary(e)
            code = _api_error_code(e)
            if code:
                summary += f" [{code}]"
            span.set_attribute("error.type", type(e).__name__)
            span.set_status(trace.Status(trace.StatusCode.ERROR, summary))
            logger.warning("%s", summary)
            raise ImageError(summary) from None
        if response_model:
            span.set_attribute("gen_ai.response.model", response_model)
        if info.input_tokens is not None:
            span.set_attribute("gen_ai.usage.input_tokens", info.input_tokens)
        if info.output_tokens is not None:
            span.set_attribute("gen_ai.usage.output_tokens", info.output_tokens)
        if info.cost_usd is not None:
            span.set_attribute("cover.image.cost_usd", info.cost_usd)
        logger.info("Painted a %s %s image with %s (%d bytes)", info.size, s.quality, s.model, len(png))
        return png, info
