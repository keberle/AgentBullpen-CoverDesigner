# The concept step: before every paint, one structured-output call lists N distinct cover concepts (N by
# artistic license level), chooses one and writes its image prompt. The lever for variety is generating
# several distinct ideas and choosing, so the app makes it happen instead of leaving it to the agent.
# Both MAF 1.21 clients take a Pydantic model as `response_format`: OpenAIChatCompletionClient (Chat Completions) sends
# it as a strict `json_schema` response format, AnthropicClient as the GA `output_config.format`.
import asyncio
import logging
import time
from typing import Literal

from agent_framework import Message
from opentelemetry import trace
from pydantic import BaseModel, Field, ValidationError

import modes
import problems

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

COUNTS = {"faithful": 1, "interpretive": 3, "bold": 6, "wild": 10}

INSTRUCTION = (
    "Generate exactly {N} cover concepts, then choose one. Concepts must be mutually distinct: each uses a different"
    " emblem or subject AND a different composition. At Bold and Wild, discard the obvious first idea before you list"
    " them. The author's explicit request overrides the style block (skill §0). If the author asks for a first edition"
    " or a period-original cover, reproduce every physical detail the style block lists for that edition (cloth"
    " colour, gilt or gold stamping, silhouette, texture). If the look is noir, say which sense — \"shadow\" (film noir"
    " lighting) or \"paperback\" (bright painted 1950s crime paperback). Then write the image prompt for the chosen"
    " concept using skill §6's labelled order (USE, SCENE/GROUND, SUBJECT, DETAILS); do not write the CONSTRAINTS"
    " segment — the app appends it."
)


class Concept(BaseModel):
    title: str
    emblem: str
    composition: str
    medium: str
    palette: str
    reason: str


class ConceptPlan(BaseModel):
    concepts: list[Concept]
    chosen: int = Field(description="Index of the chosen concept in concepts, counting from 0 (the first concept is 0).")
    chosenReason: str
    style: str  # the style paragraph this paint uses
    noirSense: Literal["shadow", "paperback"] | None
    prompt: str  # USE: … SCENE/GROUND: … SUBJECT: … DETAILS: …
    openQuestions: list[str]


class ConceptError(Exception):
    """The concept call failed twice (or returned an invalid plan twice); the paint is not made."""


def brief(craft: str, level_name: str, level_row: str, n: int, book: list[str], style_block: str | None,
          recent: list[tuple[str, str]], author_request: str, designer_notes: str,
          previous: list[str]) -> str:
    """The one user message of the concept call, in a fixed order."""
    parts = [
        craft.strip(),
        f"--- Artistic license ---\n{level_name}: {level_row}",
        "--- Book ---\n" + "\n".join(book),
        "--- Current style block ---\n" + (style_block or "none yet — designer's choice"),
        "--- Recent conversation ---\n" + ("\n".join(f"{role}: {text}" for role, text in recent) or "(none)"),
        f'Author\'s request (their exact words): "{author_request.strip()}"',
        "Designer's notes (the designer's own suggestions — the author did NOT ask for these; at Faithful and"
        " Interpretive use them only if they fit the author's request, at Bold and Wild ignore them unless one of your"
        f" distinct concepts needs them): {designer_notes.strip() or 'none'}",
        "--- Earlier versions' concepts (don't repeat them unless the author asks to refine one) ---\n"
        + ("\n".join(previous) or "(none)"),
        INSTRUCTION.replace("{N}", str(n)),
    ]
    return "\n\n".join(parts)


def _valid(plan: ConceptPlan | None, n: int) -> str | None:
    """None when the plan is usable, else what is wrong."""
    if plan is None:
        return "no structured value"
    if len(plan.concepts) != n:
        return f"{len(plan.concepts)} concepts, expected {n}"
    if not 0 <= plan.chosen < n:
        return f"chosen {plan.chosen} out of range"
    if not plan.prompt.strip():
        return "empty prompt"
    return None


async def generate(client, model: str, license: str, text: str, timeout: float, reasoning_effort: str | None = None
                   ) -> tuple[ConceptPlan, int, int]:
    """(the plan, input tokens, output tokens), both attempts summed. One retry on any failure, then ConceptError."""
    n = COUNTS[license]
    options: dict = {"response_format": ConceptPlan}
    if reasoning_effort:
        options["reasoning_effort"] = reasoning_effort  # Chat Completions' own parameter
    tokens_in = tokens_out = 0
    with tracer.start_as_current_span("generate_concepts") as span:
        span.set_attribute("gen_ai.operation.name", "generate_concepts")
        span.set_attribute("gen_ai.request.model", model)
        span.set_attribute("cover.license", license)
        span.set_attribute("cover.concepts.requested", n)
        started = time.monotonic()
        problem = "not run"
        for attempt in (1, 2):
            try:
                response = await asyncio.wait_for(
                    client.get_response([Message(role="user", contents=[text])], options=options), timeout=timeout
                )
                usage = response.usage_details or {}
                tokens_in += usage.get("input_token_count") or 0
                tokens_out += usage.get("output_token_count") or 0
                try:
                    plan = response.value
                except (ValidationError, ValueError):
                    plan = None
                problem = _valid(plan, n)
            except TimeoutError:
                problem = f"timed out after {timeout} s"
                modes.record(problems.make("timeout"))
            except Exception as e:  # provider errors: type only, never the message or body
                problem = f"call failed ({type(e).__name__})"
            if problem is None:
                break
            logger.warning("Concept call attempt %d invalid: %s", attempt, problem)
            # The one retry is for invalid output, an error or a timeout. A refusal, an account failure or a
            # rate limit the transport already retried is never sent again.
            recorded = modes.current().problem
            if modes.refusal() or (recorded and recorded.slug not in ("upstream-error", "timeout")):
                break
            if attempt == 1:
                modes.current().problem = None
        span.set_attribute("gen_ai.usage.input_tokens", tokens_in)
        span.set_attribute("gen_ai.usage.output_tokens", tokens_out)
        span.set_attribute("cover.concepts.seconds", round(time.monotonic() - started, 1))
        if problem is not None:
            span.set_attribute("cover.concepts.error", problem)
            raise ConceptError(problem)
        span.set_attribute("cover.concepts.count", len(plan.concepts))
        span.set_attribute("cover.concepts.chosen", plan.chosen)
        if plan.noirSense:
            span.set_attribute("cover.noir_sense", plan.noirSense)
        logger.info("Concepts: %d at %s, chose %d (%d in / %d out tokens)", len(plan.concepts), license, plan.chosen, tokens_in, tokens_out)
    return plan, tokens_in, tokens_out
