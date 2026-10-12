# Cover art: one plain call to the image provider's Images API (OpenAI only in
# this build), independent of the chat provider. Settings come from env vars
# (docs/guide/configuration.md); the app starts without them.
import asyncio
import base64
import logging
import os
from dataclasses import dataclass, field

import openai
from opentelemetry import trace

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

TYPES = ("OpenAI",)


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
            return False, f"IMAGE_TYPE must be OpenAI in this build (got {self.type!r})."
        return True, None


@dataclass
class ImageInfo:
    model: str
    size: str
    quality: str
    input_tokens: int | None
    output_tokens: int | None


class ImageError(Exception):
    """The image call failed; the message is a one-line summary without the key or the response body."""


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


def _generate(s: ImageSettings, prompt: str, http_client=None) -> tuple[bytes, ImageInfo, str | None]:
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
        logger.info("Painted a %s %s image with %s (%d bytes)", s.size, s.quality, s.model, len(png))
        return png, info
