# The intent step: before the designer answers an author's message, one small structured-output call on the same chat
# model labels what the message asks for, and code gives the designer only the tools that label allows (designer.py).
# A message that asks for a type change gets no paint tool, so it cannot paint: the choice is not left to the wording
# of the designer's instructions. The label comes from the model's reading of the message, never from keyword rules.
import asyncio
import logging
from typing import Literal

from agent_framework import Message
from opentelemetry import trace
from pydantic import BaseModel, ValidationError

import modes

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

MAX_TOKENS = 150
PREVIOUS_REPLY_CHARS = 600


class Intent(BaseModel):
    intent: Literal["type", "picture", "both", "talk"]
    reason: str


class IntentError(Exception):
    """The intent call gave no usable label (an error, a timeout, or invalid output twice). The message never holds
    the provider's own text."""


# The examples the prompt ends with: (the author's message, its label).
EXAMPLES = (
    ("Please change to a more modern font too", "type"),
    ("Make the title bigger.", "type"),
    ("Use dark text.", "type"),
    ("Make it more modern.", "picture"),
    ("Futuristic cover, please", "picture (also when a cover is already painted)"),
    ("Make the sky gold.", "picture"),
    ("Make the title gold.", "type"),
    ("Lose the fleur-de-lis and use a serif title.", "both"),
    ("What mood would suit this book?", "talk"),
)
PROMPT = (
    "You sort one message from an author to their book-cover designer. The cover has two parts: the painted picture,"
    " and the lettering laid over it (the title and the author's name). Say what the message asks to change.\n\n"
    "- type: the message names the lettering and asks to change only that: the font, typeface or lettering, the title's"
    " size, place, alignment or capitals, the author's name or line, the text's colour, or the shadow behind the text."
    " A message is type only if it names one of these.\n"
    "- picture: the message asks for a cover, art, an image, a version, a redo or another try, or for any change to"
    " \"it\", \"the cover\" or what is painted: the subject, scene, setting, colours or mood of the art, the medium, or"
    " the style of the illustration. A request for a cover is picture even if an earlier message asked for the same"
    " thing.\n"
    "- both: the message names a change to what is painted and, separately, names the lettering.\n"
    "- talk: a question, an opinion or an idea, with no request to change anything.\n\n"
    "Words like \"too\", \"also\", \"more modern\", \"different\" or \"bolder\" take their meaning from what they are"
    " attached to: \"a more modern font\" is type; \"make it more modern\" is picture.\n\n"
    "Examples:\n"
    + "".join(f'"{message}" -> {label}\n' for message, label in EXAMPLES)
    + "\nGive the label and a reason of one short sentence."
)


def brief(previous_reply: str, message: str, has_version: bool) -> str:
    """The one user message of the intent call: the designer's last words, the author's message as written, and
    whether there is a painting yet."""
    return "\n\n".join((
        PROMPT,
        "A painted version of the cover exists: " + ("yes" if has_version else "no, nothing has been painted yet"),
        "The designer's previous reply (its end):\n" + (previous_reply.strip()[-PREVIOUS_REPLY_CHARS:] or "(none)"),
        f'The author\'s message (their exact words): "{message.strip()}"',
    ))


async def classify(client, model: str, previous_reply: str, message: str, has_version: bool, timeout: float) -> tuple[Intent, int, int]:
    """(the label, input tokens, output tokens), both attempts summed. One retry on invalid output, an error or a
    timeout, then IntentError. A refusal or an account failure is never sent again: it is on the request state
    (modes.py) for the caller to end the turn with."""
    options: dict = {"response_format": Intent, "max_tokens": MAX_TOKENS}
    tokens_in = tokens_out = 0
    text = brief(previous_reply, message, has_version)
    with tracer.start_as_current_span("classify_intent") as span:
        span.set_attribute("gen_ai.operation.name", "classify_intent")
        span.set_attribute("gen_ai.request.model", model)
        problem = "not run"
        label: Intent | None = None
        for attempt in (1, 2):
            try:
                response = await asyncio.wait_for(client.get_response([Message(role="user", contents=[text])], options=options), timeout=timeout)
                usage = response.usage_details or {}
                tokens_in += usage.get("input_token_count") or 0
                tokens_out += usage.get("output_token_count") or 0
                try:
                    label = response.value
                except (ValidationError, ValueError):
                    label = None
                problem = None if isinstance(label, Intent) else "no structured value"
            except TimeoutError:
                problem = f"timed out after {timeout} s"
            except Exception as e:  # provider errors: the type only, never the message or body
                problem = f"call failed ({type(e).__name__})"
            if problem is None:
                break
            logger.warning("Intent call attempt %d gave no label: %s", attempt, problem)
            recorded = modes.current().problem
            if modes.refusal() or (recorded and recorded.slug not in RETRIED):
                break
            if attempt == 1:
                modes.current().problem = None
        span.set_attribute("gen_ai.usage.input_tokens", tokens_in)
        span.set_attribute("gen_ai.usage.output_tokens", tokens_out)
        if problem is not None or label is None:
            span.set_attribute("designer.intent", "failed")
            raise IntentError(problem or "no structured value")
        span.set_attribute("designer.intent", label.intent)
    return label, tokens_in, tokens_out


# A failed intent call with one of these recorded is tried once more, and after that the turn goes on with every tool.
# Any other recorded problem (a rate limit, an account failure) ends the turn, as it would on the designer's own call.
RETRIED = ("upstream-error", "timeout", "upstream-rejected")
