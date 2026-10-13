// The design-session calls to the GraphQL api; no client library.
// Standalone (VITE_API_BASE_URL empty): same-origin POST /graphql, proxied by Vite to the api.
// Gateway mode (AgentBullpen-TemporalGraph's pattern; VITE_API_BASE_URL = the site's API, e.g.
// https://api.agentbullpen.ai): the site's gateway at <base>/covers/graphql, cross-origin with the member's HttpOnly
// session cookie (credentials: "include"), and the site's antiforgery token as X-CSRF-TOKEN on every POST (fetched once
// from <base>/api/antiforgery/token, refetched once on a 400). A 401 from the gateway is SignedOutError, and the visitor goes
// to the site's sign-in page; the site's app-unavailable problem sends them to its under-construction page (site.ts).
// Every GraphQL error carries extensions.problem (RFC 9457; problems.ts says what the author sees for each).

import { isAppUnavailable, onAppUnavailable, onSignedOut } from "./site.ts";

export type Book = { title: string; author: string };
/** What startSession takes: a demo book, or "Your own book idea" with its form values. */
export type BookInput = Book & { genre?: string; about?: string; style?: string; styleText?: string };
/** A turn's totals over every model call. costUsd: what they cost, or null when a call came without a cost. */
export type Usage = { inputTokens: number; outputTokens: number; costUsd?: number | null };
/** costUsd: the provider's own cost of this image, when it reports one. */
export type ImageInfo = { model: string; size: string; quality: string; inputTokens: number | null; outputTokens: number | null; costUsd?: number | null };
/**
 * The member's daily allowance in US dollars (from the gateway's headers); unlimited standalone and for the level "none".
 * level: off, light, standard, generous, custom, none; null standalone. resetsAtUtc: when it starts again.
 */
export type Budget = { level?: string | null; dailyUsd: number | null; remainingUsd: number | null; unlimited: boolean; resetsAtUtc?: string | null };
/** A chat model the author may pick (gateway: the site's list; standalone: the configured one). `available`
 * is false while the site has tripped its provider, until `unavailableUntilUtc`. */
export type ChatModel = {
  id: string;
  displayName: string;
  provider: string;
  isDefault: boolean;
  available?: boolean;
  unavailableUntilUtc?: string | null;
};
/** An RFC 9457 problem (extensions.problem): type https://agentbullpen.ai/problems/<slug>, and its typed extensions. */
export type Problem = {
  type: string;
  title?: string;
  status?: number;
  detail?: string;
  traceId?: string;
  resetsAtUtc?: string;
  /** budget-exhausted: the day's allowance and what was used of it. */
  dailyAllowanceUsd?: number;
  usedTodayUsd?: number;
  /** chat-off: the account's level ("off"). */
  level?: string;
  retryAfterSeconds?: number;
  reason?: string;
  provider?: string;
  model?: string;
  /** app-unavailable: the site's page for a demo that isn't open. */
  underConstructionUrl?: string | null;
};
/** The artistic license level. */
export type License = "faithful" | "interpretive" | "bold" | "wild";
/** The legibility check's finding: each line's median contrast ("Lc 78") and what the check changed or found
 * ("scrim raised to strong", "text switched to light", "still low"), or null. */
export type Legibility = { title: string; author: string; changed: string | null };
/** The cover's type: nine settings on a version. The designer's set_typography tool makes a new version with them changed. */
export type Typography = {
  /** A face's id in the library (CoverType.tsx has the list). */
  font: string;
  /** The face's own name, e.g. "Comic Neue". */
  fontFamily?: string;
  /** The author's words for the font, when the face was set for them. */
  requestedFont?: string | null;
  /** Those words named a typeface that isn't in the library; the closest face was set. */
  substituted?: boolean;
  /** The measured contrast (APCA) of each line against the plate under it, after any fix; null = not measured. */
  legibility?: Legibility | null;
  titleSize: "S" | "M" | "L";
  titlePlace: "top" | "middle" | "bottom";
  align: "left" | "center" | "right";
  titleCaps: boolean;
  authorPlace: "top" | "bottom";
  authorSize: "S" | "M";
  color: "light" | "dark" | "accent";
  scrim: "none" | "soft" | "strong";
};
export type Message = {
  role: "designer" | "user";
  text: string;
  usage: Usage | null;
  model: string | null;
  image: ImageInfo | null;
  /** The version this turn made: a paint's, or a type change's. */
  version: number | null;
  license: License | null;
  /** The settings set_typography left on this turn's version (typography turns only). */
  typography: Typography | null;
  /** This turn's paint failed: why (the note under the answer). */
  paintProblem?: Problem | null;
  /** The site answered with its fallback model (display names). */
  fallbackUsed?: { requested: string; used: string } | null;
};
/** One idea the concept call listed before a paint. */
export type Concept = { title: string; emblem: string; composition: string; medium: string; palette: string; reason: string };
/** One version of the cover (n = 1, 2, …): every paint appends one, and so does every type change. `concept` is the
 * designer's chosen concept and its reason (a type version's are its plate's). */
export type Version = {
  n: number;
  /** "paint": a new plate. "type": the plate of the version it was made from, with the type changed. */
  kind?: "paint" | "type";
  /** The version it was made from; null for the first. */
  from?: number | null;
  imageDataUrl: string;
  scene: string;
  createdAt: string;
  image: ImageInfo;
  license: License;
  concept: string;
  /** All the concepts considered (1 / 3 / 6 / 10 by level); `chosen` indexes the painted one. */
  concepts: Concept[];
  chosen: number;
  noirSense: string | null;
  /** The designer's own suggestions passed to paint_cover ("" = none). */
  designerNotes: string;
  typography: Typography;
};
export type Session = {
  id: string;
  title: string;
  author: string;
  license: License;
  /** The chat model id the next turn runs on. */
  model: string;
  messages: Message[];
  versions: Version[];
  /** The allowance after this request (gateway), unlimited (standalone), or null (gateway without the headers). */
  budget: Budget | null;
};
export type Availability = { chat: boolean; chatNote: string | null; images: boolean; imagesNote: string | null };
/** A session, plus the IMAGE_ERROR or CONCEPT_ERROR message when the turn succeeded but its painting failed. */
export type Turn = { session: Session; imageError: GraphQLError | null };

const imageFields = "image { model size quality inputTokens outputTokens costUsd }";
const typographySettings = "font fontFamily requestedFont substituted titleSize titlePlace align titleCaps authorPlace authorSize color scrim legibility { title author changed }";
const typographyFields = `typography { ${typographySettings} }`;
const budgetFields = "budget { level dailyUsd remainingUsd unlimited resetsAtUtc }";
const sessionFields = `id title author license model ${budgetFields} messages { role text usage { inputTokens outputTokens costUsd } model ${imageFields} version license ${typographyFields} paintProblem fallbackUsed { requested used } } versions { n kind from imageDataUrl scene createdAt ${imageFields} license concept concepts { title emblem composition medium palette reason } chosen noirSense designerNotes ${typographyFields} }`;

/** A GraphQL error; `code` is its `extensions.code` (UNKNOWN_SESSION, BAD_LICENSE, NO_MODEL, TIMEOUT, MODEL_ERROR, IMAGE_ERROR, CONCEPT_ERROR,
 * PROVIDER_UNAVAILABLE, INTERNAL_ERROR; gateway: UNAUTHORIZED, BUDGET_EXHAUSTED, CHAT_OFF, RATE_LIMITED, UNKNOWN_MODEL) and `problem`
 * its `extensions.problem`, when there are. */
export class GraphQLError extends Error {
  readonly code?: string;
  readonly problem?: Problem;
  constructor(message: string, code?: string, problem?: Problem) {
    super(message);
    this.code = code;
    this.problem = problem;
  }
}

type RawError = { message: string; extensions?: { code?: string; problem?: Problem } };
type Body<T> = { data?: T | null; errors?: RawError[] } & Partial<Problem>;
const toError = (e: RawError) => new GraphQLError(e.message, e.extensions?.code, e.extensions?.problem);

/** The gateway answered 401 (signed out, or the session expired). */
export class SignedOutError extends Error {
  constructor() {
    super("You are signed out of AgentBullpen.AI.");
  }
}

/** The demo isn't open (the site's app-unavailable problem); the visitor is being sent to the under-construction page. */
export class AppUnavailableError extends Error {
  readonly problem: Problem;
  constructor(problem: Problem) {
    super(problem.detail ?? "This demo isn't open right now.");
    this.problem = problem;
  }
}

/** A 401 mid-session also sends the visitor to the site's sign-in page (to come back here); the error is for the caller. */
function signedOut(): SignedOutError {
  onSignedOut();
  return new SignedOutError();
}

/** The site's API base, without a trailing slash; empty = standalone. */
export const apiBase = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/+$/, "");
/** The site's address (gateway mode), for its sign-in page, the user menu's links and the header's; empty = none of them. */
export const siteUrl = (import.meta.env.VITE_SITE_URL ?? "").replace(/\/+$/, "");
export const gatewayMode = apiBase !== "";

/** Where GraphQL requests go: <base>/covers/graphql behind the gateway, else /graphql. */
export function graphqlUrl(base: string = apiBase): string {
  return base ? `${base}/covers/graphql` : "/graphql";
}

/** The cover PNG with its type (the api's GET /download): through the gateway when there is one. A plain link, so the
 * member's cookie goes with the navigation. */
export function downloadUrl(sessionId: string, n: number, base: string = apiBase): string {
  return `${base ? `${base}/covers` : ""}/download/${encodeURIComponent(sessionId)}/${n}`;
}

// One fetch shared by the requests that start together (the screen loads the session and the availability at once);
// a failed fetch is forgotten, so the next request tries again.
let csrfToken: Promise<string> | null = null;

async function fetchCsrf(): Promise<string> {
  const response = await fetch(`${apiBase}/api/antiforgery/token`, { credentials: "include", headers: { Accept: "application/json" } });
  if (response.status === 401) throw signedOut();
  return ((await response.json()) as { token: string }).token;
}

function csrf(refresh = false): Promise<string> {
  if (refresh || !csrfToken) csrfToken = fetchCsrf().catch((e) => {
    csrfToken = null;
    throw e;
  });
  return csrfToken;
}

async function post(body: string, refreshCsrf = false, signal?: AbortSignal): Promise<Response> {
  if (!gatewayMode) return fetch("/graphql", { method: "POST", headers: { "Content-Type": "application/json" }, body, signal });
  return fetch(graphqlUrl(), {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json", "X-CSRF-TOKEN": await csrf(refreshCsrf) },
    body,
    signal,
  });
}

/** `timeoutMs`, when given, aborts the call after that long (the fetch rejects with a TimeoutError); absent = no limit. */
async function request<T>(query: string, variables: Record<string, unknown> = {}, timeoutMs?: number): Promise<{ data: T; errors: GraphQLError[] }> {
  const payload = JSON.stringify({ query, variables });
  const signal = timeoutMs === undefined ? undefined : AbortSignal.timeout(timeoutMs);
  let r = await post(payload, false, signal);
  // The gateway rejects a stale antiforgery token with 400: fetch a new one once.
  if (gatewayMode && r.status === 400) r = await post(payload, true, signal);
  if (r.status === 401) throw signedOut();
  const body = (await r.json().catch(() => ({}))) as Body<T>;
  // A timeout while the body was on its way is still a timeout.
  signal?.throwIfAborted();
  // The site's gateway answers a demo that isn't open with the app-unavailable problem (503, problem+json) before it reaches
  // the api; the api passes the same problem on from a model call. Either way: to the under-construction page.
  const unavailable = isAppUnavailable(body as Problem) ? (body as Problem) : body.errors?.map((e) => e.extensions?.problem).find(isAppUnavailable);
  if (unavailable && onAppUnavailable(unavailable)) throw new AppUnavailableError(unavailable);
  const errors = (body.errors ?? []).map(toError);
  if (!body.data) throw errors[0] ?? new GraphQLError(`HTTP ${r.status}`);
  return { data: body.data, errors };
}

/** Data with an IMAGE_ERROR or CONCEPT_ERROR next to it is a successful turn; any other error fails the call. */
function turn(session: Session, errors: GraphQLError[]): Turn {
  const other = errors.find((e) => e.code !== "IMAGE_ERROR" && e.code !== "CONCEPT_ERROR");
  if (other) throw other;
  return { session, imageError: errors[0] ?? null };
}

/** How long the book list waits: deployed, the api may be starting from nothing. Under the site gateway's own timeout. */
export const booksTimeoutMs = 150_000;

/** The book list (pick screen). The first call of a visit, so the one that waits for a sleeping api; it gives up after
 * booksTimeoutMs. */
export async function books(): Promise<Book[]> {
  return (await request<{ books: Book[] }>("{ books { title author } }", {}, booksTimeoutMs)).data.books;
}

/** The models to pick from and the budget. */
export async function modelsAndBudget(): Promise<{ chatModels: ChatModel[]; budget: Budget | null }> {
  return (
    await request<{ chatModels: ChatModel[]; budget: Budget | null }>(
      `{ chatModels { id displayName provider isDefault available unavailableUntilUtc } ${budgetFields} }`,
    )
  ).data;
}

/** The member's allowance alone: the pick screen and the own-idea form, and again when the tab gets the focus back. */
export async function budget(): Promise<Budget | null> {
  return (await request<{ budget: Budget | null }>(`{ ${budgetFields} }`)).data.budget;
}

export async function availability(): Promise<Availability> {
  return (await request<{ availability: Availability }>("{ availability { chat chatNote images imagesNote } }")).data.availability;
}

/** The session, or null when the api doesn't know it (sessions live in api memory and vanish on restart). */
export async function session(id: string): Promise<Session | null> {
  const { data } = await request<{ session: Session | null }>(`query Session($id: ID!) { session(id: $id) { ${sessionFields} } }`, { id });
  return data.session;
}

/** `license` absent = the api's default (interpretive). */
export async function startSession(book: BookInput, license?: License): Promise<Turn> {
  const { data, errors } = await request<{ startSession: Session }>(
    `mutation StartSession($book: BookInput!, $license: String) { startSession(book: $book, license: $license) { ${sessionFields} } }`,
    { book, license },
  );
  return turn(data.startSession, errors);
}

/** `license`, when given, sets the session's level before this turn; `version` is the n on screen (a type change makes a
 * new version of it, a new paint inherits its type); absent = the latest. `model` (a chatModels id) runs this turn and the next ones on
 * it; the session and its versions go on. */
export async function chat(sessionId: string, message: string, license?: License, version?: number, model?: string): Promise<Turn> {
  const { data, errors } = await request<{ chat: Session }>(
    `mutation Chat($sessionId: ID!, $message: String!, $license: String, $version: Int, $model: String) { chat(sessionId: $sessionId, message: $message, license: $license, version: $version, model: $model) { ${sessionFields} } }`,
    { sessionId, message, license, version, model },
  );
  return turn(data.chat, errors);
}
