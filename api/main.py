# GraphQL API: `books` over an in-memory list, `availability`, and design
# sessions with the Cover designer agent (`startSession`, `chat`; designer.py),
# which can paint the cover plate (images.py) and set its type (typography.py).
# GET /download/{session}/{n} renders a version with its type baked in. No database, nothing on disk.
# Gateway or standalone (modes.py): behind the AgentBullpen.AI site every request but /health needs the site's
# JWT, model calls go through the site's LLM proxy with it, and the member's budget comes from its headers.
# Errors (problems.py): REST answers RFC 9457 application/problem+json; GraphQL errors keep extensions.code and add
# extensions.problem (ProblemRouter below). docs/design/errors.md says what the author sees.
import asyncio
import base64
import contextlib
import dataclasses
import logging
import re
import sys
import unicodedata

import fastapi
import fastapi.exceptions
import fastapi.responses
import opentelemetry.instrumentation.fastapi as otel_fastapi
import strawberry
from agent_framework.observability import enable_instrumentation
from graphql import GraphQLError
from opentelemetry import trace
from strawberry.extensions import SchemaExtension
from strawberry.extensions.tracing import OpenTelemetryExtension
from strawberry.fastapi import GraphQLRouter
from strawberry.scalars import JSON
from starlette.exceptions import HTTPException as StarletteHTTPException

import images
import modes
import problems
import telemetry
import legibility
import typography
from designer import Designer, DesignSession, Reply, Settings

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

settings, image_settings = Settings.from_env(), images.ImageSettings.from_env()
# Once, at startup. Half-filled gateway settings stop the api here, with the setting named, so it never starts without
# the sign-in it was meant to have.
try:
    mode = modes.choose(settings, image_settings)
except modes.GatewayConfigurationError as e:
    print(f"The API did not start: {e}", file=sys.stderr)
    sys.exit(1)
designer = Designer(settings, image_settings, mode)


@strawberry.type
class Book:
    title: str
    author: str


# The three demo books: public-domain titles (Treasure Island as in AgentBullpen-TemporalGraph's book list).
BOOKS = [
    Book(title="The Hound of the Baskervilles", author="Arthur Conan Doyle"),
    Book(title="The Three Musketeers", author="Alexandre Dumas"),
    Book(title="Treasure Island", author="Robert Louis Stevenson"),
]


@strawberry.input
class BookInput:
    title: str
    author: str
    genre: str | None = strawberry.UNSET
    about: str | None = strawberry.UNSET
    style: str | None = strawberry.UNSET  # designer | classic | minimal | painted | cinematic | animated | other; demo books ignore it
    style_text: str | None = strawberry.UNSET  # the author's own words when style is "other"


@strawberry.type
class Availability:
    chat: bool
    chat_note: str | None  # the reason when chat is false
    images: bool
    images_note: str | None  # the reason when images is false
    chat_problem: JSON | None = None  # model-not-configured, as a problem
    images_problem: JSON | None = None  # images-not-configured, as a problem


@strawberry.type
class Usage:
    # The turn's totals over every model call. cost_usd: what they cost (gateway: the site's; standalone: the
    # provider's own when it sends one), or null when a call came without a cost. Never estimated.
    input_tokens: int
    output_tokens: int
    cost_usd: float | None = None


@strawberry.type
class ImageInfo:
    model: str
    size: str
    quality: str
    input_tokens: int | None
    output_tokens: int | None


@strawberry.type
class Legibility:
    # The measured contrast (APCA) of each line's ink against the plate under it, after any fix (legibility.py).
    title: str  # the median over the title's ink, "Lc 78"
    author: str
    changed: str | None  # what the check did or found: "scrim raised to strong", "text switched to light", "still low"


@strawberry.type
class Typography:
    # The cover's type; the values are designer.typography.ALLOWED.
    font: str  # a library id (typography.FACES)
    title_size: str  # S | M | L
    title_place: str  # top | middle | bottom
    align: str  # left | center | right
    title_caps: bool
    author_place: str  # top | bottom
    author_size: str  # S | M
    color: str  # light | dark | accent
    scrim: str  # none | soft | strong
    font_family: str  # the face's own name, e.g. "Comic Neue"
    requested_font: str | None  # the author's words for the font, when the face was set for them
    substituted: bool  # those words named a typeface that isn't in the library; the closest face was set
    legibility: Legibility | None = None  # the measured check of this type on its plate; None = not measured


def to_typography(t: typography.Typography, found: "legibility.Legibility | None" = None) -> Typography:
    measured = Legibility(title=f"Lc {found.title.lc_median:.0f}", author=f"Lc {found.author.lc_median:.0f}", changed=found.changed) if found else None
    return Typography(**dataclasses.asdict(t), font_family=typography.FACES[t.font].family, legibility=measured)


@strawberry.type
class FallbackUsed:
    # The site answered with its fallback model (X-Model-Fallback), by the models' display names.
    requested: str
    used: str


@strawberry.type
class Message:
    role: str  # "designer" | "user"
    text: str
    usage: Usage | None = None  # designer messages only
    model: str | None = None
    image: ImageInfo | None = None  # the painting made in this turn, if any
    version: int | None = None  # the n this answer made: a paint's, or a type change's
    license: str | None = None  # the artistic license level it painted at (painted answers only)
    typography: Typography | None = None  # the settings set_typography left this turn (typography turns only)
    paint_problem: JSON | None = None  # the paint this turn failed: its problem (the note under the answer)
    fallback_used: FallbackUsed | None = None  # answered by the site's fallback model


@strawberry.type
class Concept:
    title: str
    emblem: str
    composition: str
    medium: str
    palette: str
    reason: str


@strawberry.type
class Version:
    n: int
    image_data_url: str
    scene: str  # the image prompt the concept call wrote (before framing and the textless rule)
    created_at: str
    image: ImageInfo
    license: str  # faithful | interpretive | bold | wild
    concept: str  # the chosen concept's title — why it was chosen
    concepts: list[Concept]  # every concept the concept call listed (1 / 3 / 6 / 10 by level)
    chosen: int  # index of the painted one in concepts
    noir_sense: str | None  # "shadow" | "paperback" when the look is noir
    designer_notes: str  # the designer's own suggestions passed to paint_cover ("" = none)
    typography: Typography  # a new version inherits the viewed version's; set_typography makes one with it changed
    kind: str  # "paint": a new plate | "type": the plate of `from`, with the type changed
    from_: int | None = strawberry.field(name="from")  # the version it was made from; null for the first


@strawberry.type
class Budget:
    # The member's daily allowance in US dollars (gateway: the site's headers); unlimited standalone and for the level
    # none. level: off, light, standard, generous, custom or none; null standalone.
    level: str | None
    daily_usd: float | None
    remaining_usd: float | None
    unlimited: bool
    # When it starts again (the site's reset time, else the next UTC midnight); null standalone and for off.
    resets_at_utc: str | None = None


@strawberry.type
class ChatModel:
    id: str  # standalone: the configured model; gateway: provider:model from the site's list
    display_name: str
    provider: str
    is_default: bool
    available: bool = True  # false while the site has tripped its provider (/llm/models)
    unavailable_until_utc: str | None = None


@strawberry.type
class Session:
    id: strawberry.ID
    title: str
    author: str
    license: str  # faithful | interpretive | bold | wild: the level the next turn runs at
    model: str  # the chat model id the next turn runs on
    messages: list[Message]
    versions: list[Version]
    # The allowance after this request (gateway: the header balance less this turn's cost); null in gateway mode without
    # the headers.
    budget: Budget | None


def to_budget(b: modes.Budget | None) -> Budget | None:
    if not b:
        return None
    return Budget(level=b.level, daily_usd=b.daily_usd, remaining_usd=b.remaining_usd, unlimited=b.unlimited, resets_at_utc=b.resets_at_utc)


def to_session(s: DesignSession, spent: float | None = None) -> Session:
    versions = [
        Version(
            n=v.n,
            image_data_url=v.image_data_url,
            scene=v.scene,
            created_at=v.created_at,
            image=ImageInfo(**vars(v.image)),
            license=v.license,
            concept=v.concept,
            concepts=[Concept(**c.model_dump()) for c in v.concepts],
            chosen=v.chosen,
            noir_sense=v.noir_sense,
            designer_notes=v.designer_notes,
            typography=to_typography(v.typography, v.legibility),
            kind=v.kind,
            from_=v.made_from,
        )
        for v in s.versions
    ]
    return Session(
        id=strawberry.ID(s.id), title=s.title, author=s.author, license=s.license, model=s.chat_model, messages=list(s.messages),
        versions=versions, budget=to_budget(mode.budget.current(spent)),
    )


async def chat_models() -> list[modes.ChatModel]:
    return await mode.catalog.chat_models()  # a CatalogError carries the site's problem


async def fallback_used(header: str | None) -> FallbackUsed | None:
    """X-Model-Fallback "<requested> -> <used>" by the catalog's display names (the raw ids when not listed)."""
    if not header or "->" not in header:
        return None
    requested, _, used = (x.strip() for x in header.partition("->"))
    try:
        models = await chat_models()
    except problems.ProblemError:
        models = []

    def name(model: str) -> str:
        return next((m.display_name for m in models if modes.split_model(m.id)[1] == model), model)

    trace.get_current_span().set_attribute("chat.model_fallback", header)
    logger.info("The site answered with its fallback model")
    return FallbackUsed(requested=name(requested), used=name(used))


async def designer_message(reply: Reply) -> Message:
    return Message(
        role="designer",
        text=reply.text,
        usage=Usage(input_tokens=reply.input_tokens, output_tokens=reply.output_tokens, cost_usd=reply.cost_usd),
        model=reply.model,
        image=ImageInfo(**vars(reply.image)) if reply.image else None,
        version=reply.version,
        license=reply.license,
        typography=to_typography(reply.typography, reply.legibility) if reply.typography else None,
        paint_problem=reply.paint_problem.body() if reply.paint_problem else None,
        fallback_used=await fallback_used(reply.fallback),
    )


@strawberry.type
class Query:
    @strawberry.field
    def books(self) -> list[Book]:
        logger.info("Returning %d books", len(BOOKS))
        return BOOKS

    @strawberry.field
    async def availability(self) -> Availability:
        ok, note = designer.available()
        if ok and mode.gateway and not await chat_models():
            ok, note = False, "The site lists no chat model."

        images_ok, images_note = designer.images_available()
        return Availability(
            chat=ok, chat_note=note, images=images_ok, images_note=images_note,
            chat_problem=None if ok else problems.make("model-not-configured", note).body(),
            images_problem=None if images_ok else problems.make("images-not-configured", images_note).body(),
        )

    @strawberry.field
    def session(self, id: strawberry.ID) -> Session | None:
        # Resume after a reload; sessions live in api memory, so after a restart this is null (not an error). Another
        # member's session is null too.
        s = designer.find(id)
        return to_session(s) if s else None

    @strawberry.field
    async def chat_models(self) -> list[ChatModel]:
        # The models the author may pick (gateway: the site's list; standalone: the configured one). The picker shows
        # only when there is more than one.
        return [
            ChatModel(id=m.id, display_name=m.display_name, provider=m.provider, is_default=m.is_default, available=m.available,
                      unavailable_until_utc=m.unavailable_until_utc)
            for m in await chat_models()
        ]

    @strawberry.field
    def budget(self) -> Budget | None:
        return to_budget(mode.budget.current())


@strawberry.type
class Mutation:
    @strawberry.mutation
    async def start_session(self, book: BookInput, info: strawberry.Info, license: str | None = None) -> Session:
        # license: faithful | interpretive | bold | wild; absent = interpretive. A ChatError leaves as its problem.
        session = designer.start(book.title, book.author, book.style, book.style_text, license)
        logger.info("Started session for %s", book.title)
        # Without a model the session has no messages; the web shows the no-model note.
        if designer.available()[0]:
            reply = await designer.opening(session, book.genre, book.about, book.style, book.style_text)
            session.messages.append(await designer_message(reply))
            note_image_error(info, reply)
            return to_session(session, reply.cost_usd)
        return to_session(session)

    @strawberry.mutation
    async def chat(
        self, session_id: strawberry.ID, message: str, info: strawberry.Info, license: str | None = None, version: int | None = None,
        model: str | None = None,
    ) -> Session:
        # license, when present, sets the session's level before this turn (and for the turns after it). version: the n
        # the author is looking at (set_typography makes a new version of it; a new paint inherits its type); absent = the latest.
        # model: a chatModels id, from this turn on; the design session and its versions go on (unlike TemporalGraph,
        # where another model starts a new conversation).
        session = designer.get(session_id)
        designer.set_license(session, license)
        if model is not None and model != session.chat_model:
            # Listed and available (a model whose provider the site has tripped is listed unavailable).
            if model not in {m.id for m in await chat_models() if m.available}:
                raise problems.ProblemError(problems.make("model-not-allowed", "That model isn't available.", model=model))
            designer.set_model(session, model)
        reply = await designer.run(session, message, from_author=True, version=version)
        session.messages.append(Message(role="user", text=message))
        session.messages.append(await designer_message(reply))
        note_image_error(info, reply)
        logger.info("Chat reply for %s", session.title)
        return to_session(session, reply.cost_usd)


def note_image_error(info: strawberry.Info, reply: Reply) -> None:
    if reply.concept_error or reply.image_error:
        code = "CONCEPT_ERROR" if reply.concept_error else "IMAGE_ERROR"
        problem = reply.paint_problem or problems.make("concept-failed" if reply.concept_error else "upstream-error")
        info.context["paint_error"] = problems.Problem(problem.slug, problem.detail, problem.extensions, code, problem.status)


class ImageErrorExtension(SchemaExtension):
    # A failed paint doesn't fail the turn: the session (with the designer's answer) is returned as data, and the
    # failure is added next to it as a GraphQL error with code IMAGE_ERROR (the image call failed) or CONCEPT_ERROR
    # (the concept call failed; no image call was made), whose extensions.problem says why (provider-unavailable,
    # upstream-error, timeout, content-policy, rate-limited, concept-failed).
    def on_execute(self):
        yield
        context = self.execution_context.context if isinstance(self.execution_context.context, dict) else {}
        result = self.execution_context.result
        if (problem := context.get("paint_error")) and result is not None:
            error = GraphQLError(problem.message, original_error=problems.ProblemError(problem))
            result.errors = [*(result.errors or []), error]


class CoverSchema(strawberry.Schema):
    # Problems (problems.ProblemError, and errors with a code) are expected and already logged in one line by
    # designer.py / modes.py / images.py; only unexpected errors get Strawberry's error log with a traceback.
    def process_errors(self, errors, execution_context=None):
        unexpected = [e for e in errors if not isinstance(e.original_error, problems.ProblemError) and not (e.extensions or {}).get("code")]
        super().process_errors(unexpected, execution_context)


def problem_of(error: GraphQLError) -> problems.Problem:
    """The problem a GraphQL error stands for: its own, one from its code, bad-request for a query the schema
    rejected (no original error: a client bug), else internal-error (no message, no stack, only the trace id)."""
    original = error.original_error
    if isinstance(original, problems.ProblemError):
        return original.problem
    if code := (error.extensions or {}).get("code"):
        return problems.from_code(code, error.message)
    if original is None:
        return problems.make("bad-request", error.message)
    return problems.internal()


class ProblemRouter(GraphQLRouter):
    # The error-formatting hook. Strawberry's process_result turns the execution result into the response body;
    # here each error keeps extensions.code and gains extensions.problem (RFC 9457 fields, typed extensions, traceId),
    # and is counted (problems.report). It runs for every result, the schema's validation errors included.
    async def process_result(self, request, result):
        body = await super().process_result(request, result)
        if result.errors:
            formatted = []
            for error in result.errors:
                problem = problem_of(error)
                problems.report(problem)
                f = error.formatted
                f["message"] = problem.message
                f["extensions"] = {"code": problem.graphql_code, "problem": problem.body()}
                formatted.append(f)
            body["errors"] = formatted
        return body


schema = CoverSchema(query=Query, mutation=Mutation, extensions=[OpenTelemetryExtension, ImageErrorExtension])


@contextlib.asynccontextmanager
async def lifespan(app):
    # OpenTelemetry (logs, metrics, traces to the Aspire dashboard), then MAF's gen_ai.* spans on the same
    # providers. Sensitive data (prompts and answers) stays off the spans.
    telemetry.configure_opentelemetry()
    enable_instrumentation(enable_sensitive_data=False)
    ok, note = designer.available()
    images_ok, images_note = designer.images_available()
    logger.info(
        "Cover Designer API started; chat %s; images %s",
        f"model {designer.settings.model}" if ok else f"off ({note})",
        f"model {designer.image_settings.model}" if images_ok else f"off ({images_note})",
    )
    yield


app = fastapi.FastAPI(lifespan=lifespan, telemetry={"auto_configure": False})
otel_fastapi.FastAPIInstrumentor.instrument_app(app, exclude_spans=["send"])

app.include_router(ProblemRouter(schema), prefix="/graphql")


def problem_response(problem: problems.Problem, headers: dict | None = None) -> fastapi.responses.JSONResponse:
    """An RFC 9457 answer (REST); counted like the GraphQL ones."""
    problems.report(problem)
    return fastapi.responses.JSONResponse(problem.body(), status_code=problem.http_status, media_type="application/problem+json", headers=headers)


@app.exception_handler(problems.ProblemError)
async def on_problem(request: fastapi.Request, e: problems.ProblemError):
    return problem_response(e.problem)


@app.exception_handler(StarletteHTTPException)
async def on_http_error(request: fastapi.Request, e: StarletteHTTPException):
    if e.status_code == 404:
        return problem_response(problems.make("not-found"))
    if e.status_code < 500:
        return problem_response(problems.make("bad-request", status=e.status_code), headers=getattr(e, "headers", None))
    return problem_response(problems.internal())


@app.exception_handler(fastapi.exceptions.RequestValidationError)
async def on_bad_request(request: fastapi.Request, e: fastapi.exceptions.RequestValidationError):
    return problem_response(problems.make("bad-request", "The request's parameters aren't valid."))


@app.exception_handler(Exception)
async def on_unexpected(request: fastapi.Request, e: Exception):
    # No exception text, no stack: only the trace id (the log has the traceback).
    logger.exception("Unexpected error on %s", request.url.path)
    return problem_response(problems.internal())


@app.middleware("http")
async def current_user(request: fastapi.Request, call_next):
    # The current user adapter (modes.py) for every request but /health (anonymous, for the probe). Gateway: a missing
    # or bad JWT gets 401; the token and the budget headers are kept for this request only (never logged).
    if request.url.path == "/health":
        return await call_next(request)
    try:
        user, token = mode.users.authenticate(request.headers)
    except modes.Unauthorized as e:
        logger.info("Rejected a request without a valid gateway token (%s)", e)
        return problem_response(problems.make("unauthorized", "Sign in again."))
    state = modes.RequestState(user=user, token=token)
    mode.budget.read(request.headers, state)
    if user.sub:
        trace.get_current_span().set_attribute("user.id", user.sub)
    token_ref = modes.begin(state)
    try:
        return await call_next(request)
    finally:
        modes.end(token_ref)


def slug(name: str) -> str:
    """"The Hound of the Baskervilles" → "the-hound-of-the-baskervilles" (the web's slug rule; accents dropped)."""
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", plain).strip("-")


@app.get("/download/{session_id}/{n}")
async def download(session_id: str, n: int):
    # The version's plate at full size with the scrim, title and author drawn in (typography.render), on a worker
    # thread (CPU work). GET, so a plain link works through the gateway. Another member's session is a 404.
    session = designer.find(session_id)
    if session is None:
        raise problems.ProblemError(problems.make("unknown-session"))
    version = next((v for v in session.versions if v.n == n), None)
    if version is None:
        raise problems.ProblemError(problems.make("unknown-version", f"This session has no version {n}."))
    with tracer.start_as_current_span("render_cover") as span:
        span.set_attribute("cover.version", n)
        for name, value in dataclasses.asdict(version.typography).items():
            if value is not None:
                span.set_attribute(f"cover.typography.{name}", value)
        plate = base64.b64decode(version.image_data_url.split(",", 1)[1])
        png = await asyncio.to_thread(typography.render, plate, session.title, session.author, version.typography)
        span.set_attribute("cover.bytes", len(png))
    logger.info("Rendered version %d of %s for download", n, session.title)
    return fastapi.responses.Response(
        png, media_type="image/png", headers={"Content-Disposition": f'attachment; filename="{slug(session.title)}-v{n}.png"'}
    )


@app.get("/health", response_class=fastapi.responses.PlainTextResponse)
async def health():
    return "Healthy"
