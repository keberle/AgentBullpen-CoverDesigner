# The Cover designer: one Microsoft Agent Framework agent over the configured
# chat model with two tools: paint_cover, which paints the front-cover plate
# through images.py after a structured concept call (concepts.py) brainstorms and
# chooses the concept, and set_typography, which makes a new version of the same plate with the type changed
# (typography.py; no paint). After either, code measures how the type reads on the plate and fixes it (legibility.py). Its instructions end with the cover-design craft skill
# (skills/cover-craft.md); each turn carries the session's artistic license level. Holds the in-memory design sessions (no files, no database;
# each paint and each type change appends a version whose plate is a data URL on the session). Settings come from env vars only
# (docs/guide/configuration.md); the app starts without them and
# `available()` says what is missing.
import asyncio
import base64
import contextvars
import dataclasses
import logging
import os
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal

from agent_framework import Agent, AgentSession, tool
from opentelemetry import trace
from pydantic import Field

import concepts
from concepts import Concept
import images
import intent
import legibility
import modes
import problems
import typography
from typography import Typography

logger = logging.getLogger(__name__)

# Which tools a turn gets is decided in code, by the intent step (intent.py), not by this text: a type request runs with
# no paint tool. The text still says what each tool is for, and that a reply may only describe what its tool calls did.
INSTRUCTIONS = (
    "You are the cover designer for one book. You design front covers only. You talk with the author about mood,"
    " palette, composition, imagery and typography, in plain language, and you keep one consistent direction for the"
    " whole conversation unless the author changes it. You have a tool, paint_cover. Describe only what your tool calls"
    " did in this turn: if you didn't call set_typography, don't say the type changed; if you didn't call paint_cover,"
    " don't say you painted.\n\n"
    "When the author asks for a cover, art, a picture, an image, a version, a look, a redo, or any change to the picture"
    " itself (not the type) — however they phrase it — call paint_cover in that same turn. Never ask a clarifying question before"
    " painting: make your best call, paint, and then ask what to change. Never ask for permission to paint. Do not"
    " paint when the author is only talking about ideas without asking for a cover.\n\n"
    "When you paint, call paint_cover. The tool reads the author's own words, brainstorms concepts, chooses one and"
    " paints it. Put your own ideas in designer_notes only if you have them; never present them as the author's. Do"
    " not write the image prompt yourself. If the tool result names a noir sense, say in your reply which one you went"
    " with — shadow (film-noir lighting) or paperback (bright painted 1950s crime paperback). When the author's style"
    " is \"designer's choice\", propose a style in your first message; the tool writes the style paragraph when it"
    " first paints. You also have set_typography, which changes the cover's font, title size and placement, alignment,"
    " capitals, author placement and size, colour and the shadow gradient behind the text, without painting. Use it"
    " whenever the author asks about type, lettering, fonts or where the title sits, and after a paint if the title"
    " would sit on a busy part of the image. Never paint a new image just to change type. The fonts are a library of"
    " open-licence faces: when set_typography's result says a font the author named isn't one of them, say so in one"
    " sentence of your reply and name the face that was set. If the legibility line says something was changed or is"
    " still low, say so in one sentence. Answer in two to five"
    " sentences. Never invent facts about the book; work from the title, the author, the genre and what the author"
    " tells you."
)

# Appended to the instructions above, in this order, with the precedence paragraph last; then the craft skill.
LICENSE_NOTE = (
    "Every turn comes with a bracketed line, [Artistic license for this turn: <level> — <what you may change ·"
    " concepts generated · how the seeded style binds>], the author's chosen level from §5 of the craft notes below;"
    " paint_cover brainstorms that many concepts at that level. Never repeat the bracketed line to the author."
)
PRECEDENCE = (
    "The author's explicit request always overrides the book's seeded style, at every artistic-license level,"
    " including Faithful: if they ask for a minimalist, noir, vintage, typographic or any other look, write a new style"
    " for that look and drop whatever in the seeded style conflicts with it. The seeded style is only your starting"
    " point when the author hasn't said otherwise."
)
CRAFT_HEADER = "--- Cover design craft (read before every paint) ---"
# Loaded once at startup; every word of it is sent with every run.
CRAFT = (Path(__file__).parent / "skills" / "cover-craft.md").read_text(encoding="utf-8")
FULL_INSTRUCTIONS = "\n\n".join((INSTRUCTIONS, LICENSE_NOTE, PRECEDENCE, CRAFT_HEADER + "\n" + CRAFT.strip()))

# Artistic license levels: id → name. Each level's row (what you may change · concepts generated · how the seeded
# style binds) is read verbatim from the craft skill's §5 table.
LICENSES = {"faithful": "Faithful", "interpretive": "Interpretive", "bold": "Bold", "wild": "Wild"}
DEFAULT_LICENSE = "interpretive"


def _license_rows(craft: str) -> dict[str, str]:
    rows = {}
    for line in craft.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 4 and cells[0] in LICENSES.values():
            rows[cells[0]] = " · ".join(cells[1:])
    missing = set(LICENSES.values()) - set(rows)
    if missing:
        raise RuntimeError(f"skills/cover-craft.md §5 has no row for {sorted(missing)}")
    return rows


LICENSE_ROWS = _license_rows(CRAFT)


def license_line(license: str) -> str:
    name = LICENSES[license]
    return f"[Artistic license for this turn: {name} — {LICENSE_ROWS[name]}]"


OPENING_PROMPT = "Introduce yourself in one or two sentences and ask what mood the author has in mind for this cover."

# Prompt assembly (front cover only): the concept call's prompt | framing | textless rule, the rule last.
FRONT_FRAMING = (
    "Front-cover plate, portrait 2:3: a single-panel composition with the subject forward; keep the top quarter and"
    " the bottom sixth quiet and low-detail so title and author text can be laid over them."
)
TEXTLESS_RULE = "No text, lettering, titles, logos or watermarks anywhere in the image; the typography is added afterwards."

CONCEPT_FAILED = (
    "The concept step failed ({e}), so nothing was painted and no version was added. Tell the author in one short"
    " sentence that the picture couldn't be painted this time; do not describe a painting or say you painted one."
)
# A failed paint never reads as a painting. The tool result says it failed; the web adds the note under it.
PAINT_FAILED = (
    "The picture could not be painted ({why}); nothing was painted and no version was added. Tell the author in one"
    " short sentence that the picture couldn't be painted this time; do not describe a painting or say you painted one."
)
CONTENT_DECLINED = (
    "The image service declined this picture on safety grounds; nothing was painted and no version was added. Tell"
    " the author in one short sentence and suggest describing it differently; do not describe a painting."
)
NOT_CONFIGURED = "Image generation isn't configured; tell the author you can only describe the cover for now."
ALREADY_PAINTED = "You already painted once in this turn; describe that painting to the author and ask what to change."
REFUSED = "The site refused this call; nothing was painted. Reply with one short sentence and do nothing else."

# The tools a turn gets, by the intent step's label. `type`: only the type tool, so the turn cannot paint. `picture`:
# both, the type tool for the follow-up when the title would sit on a busy part of the new image. `talk`: none.
PAINT, TYPE = "paint_cover", "set_typography"
TOOLS_FOR = {"type": (TYPE,), "picture": (PAINT, TYPE), "both": (PAINT, TYPE), "talk": ()}
ALL_TOOLS = (PAINT, TYPE)
# On a turn the intent step gives no tool (`talk`, or `type` before anything is painted), so the reply can't claim one.
NO_TOOL = "[No tool this turn: don't say you painted or changed the type; answer in words only.]"
NO_VERSION_YET = "[Nothing has been painted yet, so there is no type to change: say the type can be set once a cover is painted.]"

STYLES_DIR = Path(__file__).parent / "styles"
# Demo books (Query.books) → their seeded style block, styles/book-<id>.md.
DEMO_BOOKS = {"The Hound of the Baskervilles": "hound-of-the-baskervilles", "The Three Musketeers": "three-musketeers", "Treasure Island": "treasure-island"}
PRESETS = ("classic", "minimal", "painted", "cinematic", "animated")

TYPES = ("OpenAI", "Anthropic")


def _setting(name: str) -> str:
    """The env var's value; a value still starting with `<` (a template placeholder) counts as unset."""
    value = os.environ.get(name, "").strip()
    return "" if value.startswith("<") else value


def read_style(name: str) -> str:
    """The whole body of styles/<name>.md: every word of it is sent to the model."""
    return (STYLES_DIR / f"{name}.md").read_text(encoding="utf-8").strip()


@dataclass(frozen=True)
class Settings:
    type: str
    model: str
    endpoint: str
    api_key: str = field(repr=False)
    timeout_seconds: float
    # The concept call's model (same type, endpoint and key); empty = `model`. Reasoning effort: OpenAI type only.
    concept_model: str = ""
    concept_reasoning_effort: str = ""

    @staticmethod
    def from_env() -> "Settings":
        try:
            timeout = float(_setting("LLM_TIMEOUT_SECONDS") or 60)
        except ValueError:
            timeout = 60.0
        return Settings(
            type=_setting("LLM_TYPE"),
            model=_setting("LLM_MODEL"),
            endpoint=_setting("LLM_ENDPOINT"),
            api_key=_setting("LLM_API_KEY"),
            timeout_seconds=timeout,
            concept_model=_setting("LLM_CONCEPT_MODEL"),
            concept_reasoning_effort=_setting("LLM_CONCEPT_REASONING_EFFORT"),
        )

    @property
    def concepts_model(self) -> str:
        return self.concept_model or self.model

    def available(self) -> tuple[bool, str | None]:
        """(True, None) when a chat model is configured, else (False, the reason). Never includes the key."""
        missing = [n for n, v in (("LLM_TYPE", self.type), ("LLM_MODEL", self.model), ("LLM_API_KEY", self.api_key)) if not v]
        if missing:
            return False, f"No model configured: {', '.join(missing)} not set."
        if self.type not in TYPES:
            return False, f"LLM_TYPE must be OpenAI or Anthropic (got {self.type!r})."
        if self.type == "Anthropic" and self.endpoint:
            return False, "LLM_ENDPOINT must be empty when LLM_TYPE is Anthropic."
        return True, None


class ChatError(problems.ProblemError):
    """A failure the API returns as a GraphQL error with `extensions.code`."""

    def __init__(self, code: str, message: str):
        super().__init__(problems.from_code(code, message))

    @staticmethod
    def of(problem: problems.Problem) -> "ChatError":
        e = ChatError.__new__(ChatError)
        problems.ProblemError.__init__(e, problem)
        return e


@dataclass
class Reply:
    text: str
    input_tokens: int
    output_tokens: int
    model: str
    cost_usd: float | None = None  # every model call of the turn (chat, concept, image); None when one came without a cost
    image: images.ImageInfo | None = None
    image_error: str | None = None  # the paint failed; the turn itself succeeded
    concept_error: str | None = None  # the concept step failed (no image call); the turn itself succeeded
    paint_problem: problems.Problem | None = None  # why the paint failed (with image_error or concept_error)
    fallback: str | None = None  # X-Model-Fallback, "<requested> -> <used>", when the site's fallback answered
    version: int | None = None  # the n this turn made: a paint's, or a type change's
    license: str | None = None  # the level it painted at (painted turns only)
    typography: Typography | None = None  # the settings set_typography left on this turn's version (typography turns)
    legibility: "legibility.Legibility | None" = None  # the measured check on the version this turn left (typography turns)
    intent: str | None = None  # the intent step's label for this turn; "failed" when it gave none; None for the opening


@dataclass
class Version:
    n: int  # 1, 2, … in the order made, paints and type changes together
    image_data_url: str  # data:image/png;base64,…
    scene: str
    created_at: str  # ISO 8601, UTC
    image: images.ImageInfo
    license: str  # the artistic license level it was painted at
    concept: str  # the chosen concept's title — the concept call's reason for choosing it
    concepts: list[Concept] = field(default_factory=list)  # all N considered (concept call)
    chosen: int = 0  # index into concepts
    noir_sense: str | None = None  # "shadow" | "paperback" when the look is noir
    designer_notes: str = ""  # the agent's own suggestions passed to paint_cover ("" = none)
    typography: Typography = field(default_factory=Typography)
    kind: str = "paint"  # "paint": a new plate | "type": the plate of the version it was made from, with other type
    made_from: int | None = None  # the version on screen when this one was made; None for the first
    legibility: "legibility.Legibility | None" = None  # the measured check on this plate with this type; None = not measured


@dataclass
class DesignSession:
    id: str
    title: str
    author: str
    genre: str | None = None  # own idea only; for the concept call
    about: str | None = None
    # The style block for every paint; None = designer's choice until a paint sets it. A style passed on any paint
    # replaces it from then on.
    style_block: str | None = None
    license: str = DEFAULT_LICENSE  # the artistic license level for the next turns
    messages: list = field(default_factory=list)  # main.Message
    agent_session: AgentSession | None = None
    owner: str = ""  # the member's `sub` (gateway); "" = the anonymous local user. Another member sees it as unknown.
    chat_model: str = ""  # the catalog id the agent runs on (chat's `model` changes it from that turn on)
    versions: list[Version] = field(default_factory=list)  # every paint and type change appends; none is ever replaced


@dataclass
class Turn:
    """What the tool did during one agent run."""

    session: DesignSession
    message: str = ""  # the author's words this turn (the concept call's last chat message)
    painted: bool = False
    concept_error: str | None = None  # the concept step failed; nothing was painted
    concept_input_tokens: int = 0
    concept_output_tokens: int = 0
    image: images.ImageInfo | None = None
    image_error: str | None = None
    paint_problem: problems.Problem | None = None
    version: int | None = None  # the version this turn made (a turn makes at most one)
    viewed: int | None = None  # the version the author is looking at (chat's `version`); None = the latest
    typography: Typography | None = None  # set_typography ran this turn: the settings on this turn's version
    legibility: "legibility.Legibility | None" = None  # the measured check on the version this turn changed


_turn: contextvars.ContextVar[Turn | None] = contextvars.ContextVar("cover_turn", default=None)


def _summary(error: BaseException) -> str:
    """One line for a provider error: the exception types and HTTP status, never the message or response body."""
    names, status, e = [], None, error
    while e is not None and len(names) < 4:
        names.append(type(e).__name__)
        status = status or getattr(e, "status_code", None)
        e = e.__cause__ or e.__context__
    return "The model call failed: " + " ← ".join(names) + (f" (HTTP {status})" if status else "")


def assemble_prompt(prompt: str) -> str:
    return " | ".join((prompt.strip(), FRONT_FRAMING, TEXTLESS_RULE))


def painted_result(n: int, plan: concepts.ConceptPlan, legibility_line: str = "", type_changed: bool = False) -> str:
    """The tool result for the agent."""
    text = f'Painted version {n}. Concept: "{plan.concepts[plan.chosen].title}" — {plan.chosenReason}.'
    if plan.noirSense:
        text += f" Noir sense: {plan.noirSense}."
    if plan.openQuestions:
        text += " Open questions: " + "; ".join(plan.openQuestions)
    if legibility_line:
        text += " " + legibility_line
    return (text + ("" if type_changed else " The type was not changed in this call.")
            + " Tell the author what you painted in one or two sentences, name the concept, and ask what to change.")


def style_for(title: str, style: str | None, style_text: str | None) -> tuple[str | None, str]:
    """(the session's style block or None for designer's choice, the style line for the opening message)."""
    book = DEMO_BOOKS.get(title) or (style.removeprefix("book-") if style and style.removeprefix("book-") in DEMO_BOOKS.values() else None)
    if book:
        block = read_style(f"book-{book}")
    elif style in PRESETS:
        block = read_style(f"preset-{style}")
    elif style == "other" and style_text and style_text.strip():
        block = "Style: " + style_text.strip()
    else:
        return None, "designer's choice"
    return block, block


def check_license(license: str | None) -> None:
    """None (not given) is fine; anything else must be a level id."""
    if license is not None and license not in LICENSES:
        logger.info("Unknown license level")
        raise ChatError("BAD_LICENSE", f"license must be one of: {', '.join(LICENSES)}")


class Designer:
    def __init__(self, settings: Settings, image_settings: images.ImageSettings, mode: modes.Mode | None = None):
        self.settings = settings
        # Gateway or standalone (modes.py), chosen once: the model calls, the catalog's default chat model.
        self.mode = mode or modes.choose(settings, image_settings)
        self.sessions: dict[str, DesignSession] = {}
        self._clients: dict[str, object] = {}  # the chat client, one per chat model id (the agent's and the intent call's)
        self._agents: dict[str, Agent] = {}  # one per chat model id, built with no tools: each run is given its own
        self._tools = {PAINT: self._paint_tool(), TYPE: self._typography_tool()}
        self._concept_client = None  # the concept call's chat client (Llm:Concept:Model, else the chat model)

    @property
    def image_settings(self) -> images.ImageSettings:
        return self.mode.calls.image_settings

    def available(self) -> tuple[bool, str | None]:
        return self.mode.calls.chat_available()

    def images_available(self) -> tuple[bool, str | None]:
        return self.mode.calls.images_available()

    def _paint_tool(self):
        designer = self

        @tool(
            name="paint_cover",
            description=(
                "Only for a change to the picture itself. Never for the font, lettering, title or author placement,"
                " text colour, capitals or the shadow behind the text — use set_typography for those."
                " The tool reads the author's own words, brainstorms concepts at the session's artistic license level,"
                " chooses one and paints the front-cover art (no text). Put your own ideas in designer_notes only if"
                " you have them; never present them as the author's. Do not write the image prompt yourself."
            ),
        )
        async def paint_cover(
            designer_notes: Annotated[
                str,
                Field(description="Optional: your own suggestions for this paint, if you have any. Never the author's words; the tool reads those itself."),
            ] = "",
        ) -> str:
            turn = _turn.get()
            if turn is None or not designer.images_available()[0]:
                return NOT_CONFIGURED
            if turn.painted:
                return ALREADY_PAINTED
            turn.painted = True
            state = modes.current()
            state.painting, state.problem = True, None
            session = turn.session
            try:
                plan, tokens_in, tokens_out = await designer.concepts_for(session, turn.message, designer_notes)
            except concepts.ConceptError as e:
                if modes.refusal():
                    return REFUSED  # run() ends the turn with the refusal's code
                turn.concept_error = f"The concept step failed: {e}"
                return designer.paint_failed(turn, state.problem or problems.make("concept-failed", turn.concept_error), CONCEPT_FAILED.format(e=e))
            turn.concept_input_tokens += tokens_in
            turn.concept_output_tokens += tokens_out
            if plan.style.strip() and plan.style.strip() != (session.style_block or ""):
                session.style_block = plan.style.strip()
                logger.info("designer.style_replaced=true")
                trace.get_current_span().set_attribute("designer.style_replaced", True)
            prompt = assemble_prompt(plan.prompt)
            try:
                png, info = await images.generate_cover_art(
                    designer.image_settings, prompt, sensitive=_setting("ENABLE_SENSITIVE_DATA").lower() == "true",
                    http_client=designer.mode.calls.image_http_client(),
                )
            except images.ImageError as e:
                if modes.refusal():
                    return REFUSED
                turn.image_error = str(e)
                return designer.paint_failed(turn, state.problem or problems.make("upstream-error"))
            shown = designer.viewed_version(session, turn.viewed) if session.versions else None
            version = Version(
                n=len(session.versions) + 1,
                image_data_url="data:image/png;base64," + base64.b64encode(png).decode("ascii"),
                scene=plan.prompt.strip(),
                created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                image=info,
                license=session.license,
                concept=f"{plan.concepts[plan.chosen].title} — {plan.chosenReason}",
                concepts=plan.concepts,
                chosen=plan.chosen,
                noir_sense=plan.noirSense,
                designer_notes=designer_notes.strip(),
                typography=shown.typography if shown else Typography(),
                made_from=shown.n if shown else None,
            )
            session.versions.append(version)
            turn.image, turn.version = info, version.n
            # The new plate inherits the type: measure it there, and fix it if it reads badly.
            line = await designer.check_legibility(turn, version)
            turn.legibility = version.legibility if turn.typography else None
            return painted_result(version.n, plan, line, bool(version.legibility and version.legibility.changes))

        return paint_cover

    def _typography_tool(self):
        designer = self

        @tool(
            name="set_typography",
            description=(
                "For every type request; it never paints."
                " Makes a new version of the cover the author is looking at: the same picture, with the type changed."
                " Pass only the settings to change. font: one id from the library of open-licence faces — " + typography.library() + "."
                " Pick the closest face for what the author asked, and pass their own words for it in requested_font."
                " When the author named a typeface that isn't in the library (Comic Sans, Futura), set the closest one,"
                " pass the name they asked for in requested_font and set requested_as to name; a well-known name is"
                " then matched to its stand-in for you."
                " title_size: S | M | L;"
                " title_place: top | middle | bottom; align: left | center | right; title_caps: true | false;"
                " author_place: top | bottom; author_size: S | M; color: light | dark | accent; scrim (the shadow"
                " gradient behind the text): none | soft | strong."
            ),
        )
        async def set_typography(
            font: Annotated[str | None, Field(description="A library id: " + " | ".join(typography.FACES))] = None,
            requested_font: Annotated[str | None, Field(description="The author's own words for the font they asked for: a style (comic book, spooky) or a typeface's name (Comic Sans). Leave out when they asked for no font.")] = None,
            requested_as: Annotated[Literal["name", "style"] | None, Field(description="What requested_font is. name: a typeface's own name, as on a font menu (Comic Sans, Futura, Helvetica). style: a description of a look (comic book, spooky, western, typewriter, elegant script).")] = None,
            title_size: Annotated[str | None, Field(description="S, M or L")] = None,
            title_place: Annotated[str | None, Field(description="top, middle or bottom")] = None,
            align: Annotated[str | None, Field(description="left, center or right")] = None,
            title_caps: Annotated[bool | None, Field(description="true = the title in capitals")] = None,
            author_place: Annotated[str | None, Field(description="top or bottom")] = None,
            author_size: Annotated[str | None, Field(description="S or M")] = None,
            color: Annotated[str | None, Field(description="light (white), dark (near-black) or accent (gold)")] = None,
            scrim: Annotated[str | None, Field(description="none, soft or strong")] = None,
        ) -> str:
            turn = _turn.get()
            if turn is None or not turn.session.versions:
                return "There is no painted version yet, so there is no type to change. Tell the author the type can be set once a cover is painted."
            # The version this turn already made (a paint, or an earlier type call), else the one the author is looking
            # at, else the latest.
            session = turn.session
            version = designer.viewed_version(session, turn.version or turn.viewed)
            # A font is a request by name: the author's words are kept; a name that is a library face sets that face;
            # a name that isn't one stands beside the face set in its place, and the result says so.
            asked = " ".join(requested_font.split()) if isinstance(requested_font, str) and requested_font.strip() else None
            by_name = isinstance(requested_as, str) and "name" in requested_as.lower()  # the argument, loosely
            substituted = False
            if asked and by_name:
                # Code resolves a name before the designer's own choice counts: a library face is itself, a known
                # closed face gets its stand-in, and only for any other name does the designer's `font` stand.
                resolved, substituted = typography.resolve_name(asked)
                font = resolved or font
            elif asked and (named := typography.named_face(asked)):
                font = named
            if asked and font is None:
                return (f'"{asked}" is not a library id. Call set_typography again with font set to the closest library id'
                        " and the same requested_font. Nothing was changed.")
            try:
                new, changed = typography.apply(
                    version.typography, font=font, title_size=title_size, title_place=title_place, align=align,
                    title_caps=title_caps, author_place=author_place, author_size=author_size, color=color, scrim=scrim,
                )
            except ValueError as e:
                return str(e)
            if font is not None:
                # The request belongs to the face it set: a later font change without one clears it.
                new = dataclasses.replace(new, requested_font=asked, substituted=substituted)
            if turn.version is None:
                # A type change is a version of its own: the plate of the one on screen (the same string, not a copy),
                # no image call. A second type call in a turn, or one after this turn's paint, changes that version.
                shown = version
                version = dataclasses.replace(
                    shown, n=len(session.versions) + 1, created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    kind="type", made_from=shown.n, legibility=None,
                )
                session.versions.append(version)
                turn.version = version.n
            version.typography = new
            turn.typography = new
            logger.info("Typography on version %d (%s): %s", version.n, version.kind, ", ".join(changed) or "no change")
            face = typography.FACES[new.font]
            fields_text = ", ".join(
                f"font {v} ({face.family})" if k == "font" else f"{k} {str(v).lower() if isinstance(v, bool) else v}" for k, v in changed.items()
            ) or "no change"
            made = f"Made version {version.n}, version {version.made_from}'s picture with the type changed" if version.kind == "type" else f"Typography updated on version {version.n}"
            text = f"{made}: {fields_text}."
            if font is not None and new.substituted:
                text += (f" {asked} isn't one of the open-licence fonts here; set {face.family}, the closest."
                         " Say that to the author in one sentence of your reply.")
            # A colour or a scrim the author set in this call stays; the check may only move the other one.
            locked = frozenset(name for name, value in (("color", color), ("scrim", scrim)) if value is not None)
            if line := await designer.check_legibility(turn, version, locked):
                text += " " + line
            turn.legibility = version.legibility
            return text + " Describe the change in one sentence. No picture was painted."

        return set_typography

    @staticmethod
    async def check_legibility(turn: Turn, version: Version, locked: frozenset[str] = frozenset()) -> str:
        """Measures the title and the author line on the version's plate, fixes the colour or the scrim when a line
        reads badly (never a field the author set in this call), and returns the line for the tool result ("" when
        there was nothing to measure)."""
        session = turn.session
        plate = base64.b64decode(version.image_data_url.split(",", 1)[1])
        with trace.get_tracer(__name__).start_as_current_span("check_legibility") as span:
            fixed, found = await asyncio.to_thread(legibility.fix, plate, session.title, session.author, version.typography, locked)
            span.set_attribute("cover.version", version.n)
            if not found.measured:
                version.legibility = None
                return ""
            for f in (found.title, found.author):
                span.set_attribute(f"cover.legibility.{f.label}.verdict", f.verdict)
                span.set_attribute(f"cover.legibility.{f.label}.lc_median", round(f.lc_median, 1))
                span.set_attribute(f"cover.legibility.{f.label}.lc_p5", round(f.lc_p5, 1))
                span.set_attribute(f"cover.legibility.{f.label}.edge_density", round(f.edge_density, 3))
            span.set_attribute("cover.legibility.changed", found.changed or "")
        if fixed != version.typography:
            version.typography = fixed
            turn.typography = fixed
        version.legibility = found
        logger.info("Legibility on version %d: title %s, author %s%s", version.n, found.title.verdict, found.author.verdict,
                    f" ({found.changed})" if found.changed else "")
        return found.line()

    @staticmethod
    def paint_failed(turn: Turn, problem: problems.Problem, text: str | None = None) -> str:
        """A paint that failed: no version; the problem goes to the web as the note under the answer, and
        the tool result tells the designer it failed."""
        turn.paint_problem = problem
        modes.current().problem = None  # handled here: not the chat call's
        if problem.slug == "content-policy":
            return CONTENT_DECLINED
        why = {"provider-unavailable": "the image service is unavailable right now", "timeout": "it took too long"}.get(problem.slug, "it failed")
        return text or PAINT_FAILED.format(why=why)

    @staticmethod
    def viewed_version(session: DesignSession, n: int | None) -> Version:
        """Version n when it exists, else the latest (the session must have one)."""
        return next((v for v in session.versions if v.n == n), session.versions[-1])

    def _get_client(self, model_id: str):
        if model_id not in self._clients:
            self._clients[model_id] = self.mode.calls.chat_client(model_id)
        return self._clients[model_id]

    def _get_agent(self, model_id: str) -> Agent:
        # No tools on the agent itself: a run's `tools` are added to the agent's own, so a subset can only be given to
        # an agent that has none.
        if model_id not in self._agents:
            self._agents[model_id] = Agent(client=self._get_client(model_id), name="Cover designer", instructions=FULL_INSTRUCTIONS)
        return self._agents[model_id]

    async def intent_for(self, session: DesignSession, message: str) -> tuple[str, int, int]:
        """(the label for the author's message, the call's input tokens, its output tokens). "failed" when the call
        gave no label; the turn then runs with every tool. A refusal or an account failure ends the turn."""
        previous = next((m.text for m in reversed(session.messages) if m.role == "designer"), "")
        span = trace.get_current_span()
        try:
            label, tokens_in, tokens_out = await intent.classify(
                self._get_client(session.chat_model), self.mode.calls.model_name(session.chat_model), previous, message,
                bool(session.versions), self.settings.timeout_seconds,
            )
        except intent.IntentError as e:
            state = modes.current()
            if problem := modes.refusal() or (state.problem if state.problem and state.problem.slug not in intent.RETRIED else None):
                raise ChatError.of(problem) from None
            state.problem = None  # handled here: the turn goes on
            logger.warning("designer.intent=failed (%s); the turn runs with every tool", e)
            span.set_attribute("designer.intent", "failed")
            return "failed", 0, 0
        logger.info("designer.intent=%s", label.intent)
        span.set_attribute("designer.intent", label.intent)
        span.set_attribute("designer.intent_reason", label.reason[:200])
        return label.intent, tokens_in, tokens_out

    async def concepts_for(self, session: DesignSession, message: str, designer_notes: str) -> tuple[concepts.ConceptPlan, int, int]:
        """The concept call for one paint: N concepts at the session's level, one chosen, its image prompt. The author's
        request is their latest chat message, taken by code (never the model's paraphrase): the message this turn is
        answering, which main.py appends to session.messages only after the run; else the last stored author message."""
        if self._concept_client is None:
            self._concept_client = self.mode.calls.concept_client()
        name = LICENSES[session.license]
        book = [f"Title: {session.title}", f"Author: {session.author}"]
        if session.genre:
            book.append(f"Genre: {session.genre}")
        if session.about:
            book.append(f"About: {session.about}")
        recent = [(m.role, m.text) for m in session.messages] + ([("user", message)] if message else [])
        author_request = message or next((m.text for m in reversed(session.messages) if m.role == "user"), "")
        previous = [f"v{v.n}: {v.concept}" for v in session.versions]
        text = concepts.brief(CRAFT, name, LICENSE_ROWS[name], concepts.COUNTS[session.license], book, session.style_block,
                              recent[-6:], author_request, designer_notes, previous)
        calls = self.mode.calls
        return await concepts.generate(
            self._concept_client, calls.concept_model, session.license, text, self.settings.timeout_seconds, calls.concept_reasoning_effort
        )

    def start(self, title: str, author: str, style: str | None = None, style_text: str | None = None, license: str | None = None) -> DesignSession:
        check_license(license)
        block, _ = style_for(title, style, style_text)
        session = DesignSession(
            id=secrets.token_urlsafe(16), title=title, author=author, style_block=block, license=license or DEFAULT_LICENSE,
            owner=modes.current_user().sub, chat_model=self.mode.default_chat_model,
        )
        self.sessions[session.id] = session
        if self.available()[0]:
            # The conversation: its messages are kept in this session object (the agent's in-memory history), and every
            # turn sends them all. Nothing is stored at the provider.
            session.agent_session = self._get_agent(session.chat_model).create_session()
        return session

    def set_model(self, session: DesignSession, model_id: str | None) -> None:
        """A catalog id (checked by the caller) runs the agent from this turn on. Unlike TemporalGraph, the design session
        and its versions go on, and so does the conversation: its history is kept here, in the session, so the next
        model reads all of it."""
        if not model_id or model_id == session.chat_model:
            return
        logger.info("Session model changed")
        session.chat_model = model_id
        if session.agent_session is None:
            session.agent_session = self._get_agent(model_id).create_session()

    @staticmethod
    def set_license(session: DesignSession, license: str | None) -> None:
        """A level id sets the session's level; None keeps it."""
        check_license(license)
        if license is not None:
            session.license = license

    def find(self, session_id: str) -> DesignSession | None:
        """The session when it exists and belongs to the current user (another member's id counts as unknown)."""
        session = self.sessions.get(session_id)
        return session if session is not None and session.owner == modes.current_user().sub else None

    def get(self, session_id: str) -> DesignSession:
        session = self.find(session_id)
        if session is None:
            logger.info("Unknown session")
            raise ChatError("UNKNOWN_SESSION", "Unknown session")
        return session

    async def opening(self, session: DesignSession, genre: str | None, about: str | None, style: str | None, style_text: str | None) -> Reply:
        # What the model knows about the book goes in the first user message only (the instructions hold no facts
        # about any book); the agent session keeps it in the history for later turns.
        _, style_line = style_for(session.title, style, style_text)
        session.genre = genre.strip() if genre and genre.strip() else None
        session.about = about.strip() if about and about.strip() else None
        lines = [f"Book: {session.title} by {session.author}"]
        if genre and genre.strip():
            lines.append(f"Genre: {genre.strip()}")
        if about and about.strip():
            lines.append(f"About: {about.strip()}")
        lines.append(f"Cover style: {style_line}")
        return await self.run(session, "\n".join(lines) + "\n\n" + OPENING_PROMPT)

    async def _attempt(self, session: DesignSession, prompt: str, timeout: float, tools: tuple[str, ...] = ALL_TOOLS, note: str = ""):
        # The license line is prepended to the turn's user message (the chat shows only the author's words). `tools`:
        # the ones this turn may call; a tool that isn't given cannot be called.
        message = license_line(session.license) + "\n" + (note + "\n" if note else "") + prompt
        given = [self._tools[name] for name in tools]
        try:
            return await asyncio.wait_for(
                self._get_agent(session.chat_model).run(message, session=session.agent_session, tools=given or None), timeout=timeout)
        except TimeoutError:
            logger.warning("Model call timed out after %s s", timeout)
            raise ChatError("TIMEOUT", "The model did not answer") from None
        except Exception as e:
            # The policy transport (modes.py) recorded why: a refusal, a classified provider failure, a rate limit.
            if problem := modes.refusal() or modes.current().problem:
                raise ChatError.of(problem) from None
            summary = _summary(e)
            logger.warning("%s", summary)
            raise ChatError("MODEL_ERROR", summary) from None

    async def run(self, session: DesignSession, prompt: str, from_author: bool = False, version: int | None = None) -> Reply:
        """One turn. `from_author`: the prompt is the author's chat message (not the opening), so a paint's concept call
        gets it as the author's request."""
        ok, note = self.available()
        if not ok or session.agent_session is None:
            raise ChatError("NO_MODEL", note or "No model configured.")
        # A turn may include one paint: the concept call (up to two attempts, each with the chat timeout) and the image,
        # which has its own (longer) timeout.
        images_on = self.images_available()[0]
        # A paint's rate-limited calls may each wait once more (modes.RATE_LIMIT_WAIT_CAP).
        timeout = self.settings.timeout_seconds + (
            2 * self.settings.timeout_seconds + self.image_settings.timeout_seconds + 3 * modes.RATE_LIMIT_WAIT_CAP if images_on else 0)
        turn = Turn(session=session, message=prompt if from_author else "", viewed=version)
        state = modes.current()
        state.problem, state.painting, state.fallback = None, False, None
        modes.begin_turn()
        token = _turn.set(turn)
        conversation = modes.conversation.set(session.id)  # X-Conversation-Id on every model call of this turn
        label, intent_in, intent_out, tools, note = None, 0, 0, ALL_TOOLS, ""
        try:
            if from_author:
                # What the message asks for decides the tools: a type request gets no paint tool.
                label, intent_in, intent_out = await self.intent_for(session, prompt)
                tools = TOOLS_FOR.get(label, ALL_TOOLS)
                if label == "type" and not session.versions:
                    tools, note = (), NO_VERSION_YET
                if label in TOOLS_FOR and not tools:
                    note = NO_TOOL + ("\n" + note if note else "")
            result = await self._attempt(session, prompt, timeout, tools, note)
        finally:
            modes.conversation.reset(conversation)
            _turn.reset(token)
        # A refusal inside paint_cover (the concept or the image call) ends the turn with its problem.
        if refusal := modes.refusal():
            raise ChatError.of(refusal)
        usage = result.usage_details or {}
        # The Chat tokens include the intent call's and the concept call's; the model names both when they differ.
        model = self.mode.calls.model_name(session.chat_model)
        concept_model = self.mode.calls.concept_model
        if turn.concept_input_tokens and concept_model != model:
            model += f" + {concept_model} (concepts)"
        return Reply(
            text=result.text,
            input_tokens=(usage.get("input_token_count") or 0) + turn.concept_input_tokens + intent_in,
            output_tokens=(usage.get("output_token_count") or 0) + turn.concept_output_tokens + intent_out,
            model=model,
            cost_usd=modes.turn_cost(),
            image=turn.image,
            image_error=turn.image_error,
            concept_error=turn.concept_error,
            paint_problem=turn.paint_problem,
            fallback=state.fallback,
            version=turn.version,
            license=session.license if turn.image is not None else None,
            typography=turn.typography,
            legibility=turn.legibility if turn.typography else None,
            intent=label,
        )
