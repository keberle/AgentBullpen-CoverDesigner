// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import type { BookInput, Budget, Problem } from "./graphql.ts";

// The app behind the site and standalone, with the site and the api played by one fetch: the load-time check (nothing renders
// until the site confirms the member), the top bar, and what the pick screen and the own-idea form show while a session
// starts, when it fails and when the member is out of tokens. Standalone: no call to the site, the top bar as it was, and a
// failed start still says why.

// Each test loads the app's modules afresh; the first load after an install is slow.
vi.setConfig({ testTimeout: 60_000 });

const site = "https://agentbullpen.test";
const api = "https://api.agentbullpen.test";
const problems = "https://agentbullpen.ai/problems/";
const member = { firstName: "Kim", lastName: "Evans", initials: "KE", email: "kim@example.test", roles: ["Admin"] };
const apps = [
  { id: "covers", name: "Book Cover", url: "https://covers.agentbullpen.test", status: "live" },
  { id: "temporal", name: "Temporal Graph", url: "https://temporal.agentbullpen.test", status: "live" },
];
const books = [
  { title: "The Hound of the Baskervilles", author: "Arthur Conan Doyle" },
  { title: "The Three Musketeers", author: "Alexandre Dumas" },
  { title: "Treasure Island", author: "Robert Louis Stevenson" },
];
const plenty: Budget = { level: "standard", dailyUsd: 1, remainingUsd: 0.98, unlimited: false, resetsAtUtc: "2026-10-12T00:00:00Z" };

/** A start's answer: a session id, a problem (a GraphQL error), an HTTP status with no body, or one of those later. */
type Answer = { session: string } | { problem: Partial<Problem> & { slug: string }; code?: string } | { status: number };
const failing = (slug: string, extra: Partial<Problem> = {}): Answer => ({ problem: { slug, ...extra } });

/** The book list's answer: the list, a failed fetch, an HTTP status with no body, or a body with its status. */
type BooksAnswer = "books" | "network" | { status: number } | { body: unknown; http: number };

/** As fetch does: the answer, or the signal's reason when the call is aborted first. */
function untilAborted<T>(answer: Promise<T>, signal?: AbortSignal | null): Promise<T> {
  if (!signal) return answer;
  return Promise.race([answer, new Promise<never>((_, reject) => signal.addEventListener("abort", () => reject(signal.reason)))]);
}

function later<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => (resolve = r));
  return { promise, resolve };
}

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

/** The app, mounted at `path` on fresh modules; `gateway` sets the two build-time settings before they are read. */
async function mount(opts: {
  gateway: boolean;
  path?: string;
  me?: unknown | Promise<unknown>;
  budget?: Budget | null | (() => Budget | null);
  starts?: (Answer | Promise<Answer>)[];
  /** The book list's answers, in order; after them (and by default) the list at once. */
  books?: (BooksAnswer | Promise<BooksAnswer>)[];
}) {
  vi.resetModules();
  if (opts.gateway) {
    vi.stubEnv("VITE_API_BASE_URL", api);
    vi.stubEnv("VITE_SITE_URL", site);
  }
  vi.doMock("./DesignScreen.tsx", () => ({ default: () => "the design screen" }));
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

  const calls: string[] = [];
  const started: BookInput[] = [];
  const redirects: string[] = [];
  const queue = [...(opts.starts ?? [])];
  const bookAnswers = [...(opts.books ?? [])];
  const graphql = async (init?: RequestInit): Promise<Response> => {
    const { query, variables } = JSON.parse(String(init?.body)) as { query: string; variables: { book?: BookInput } };
    if (query.includes("startSession")) {
      calls.push("startSession");
      started.push(variables.book!);
      const answer = await queue.shift()!;
      if ("status" in answer) return new Response("", { status: answer.status });
      if ("session" in answer) {
        return json({ data: { startSession: { id: answer.session, title: variables.book!.title, author: "", license: "interpretive", model: "m", budget: null, messages: [], versions: [] } } });
      }
      const { slug, ...extensions } = answer.problem;
      return json({ data: null, errors: [{ message: "failed", extensions: { code: answer.code ?? "X", problem: { type: problems + slug, ...extensions } } }] });
    }
    if (query.includes("books")) {
      calls.push("books");
      const answer = await untilAborted(Promise.resolve(bookAnswers.shift() ?? "books"), init?.signal);
      if (answer === "books") return json({ data: { books } });
      if (answer === "network") throw new TypeError("Failed to fetch");
      if ("status" in answer) return new Response("", { status: answer.status });
      return json(answer.body, answer.http);
    }
    if (query.trim().startsWith("{ budget")) {
      calls.push("budget");
      return json({ data: { budget: typeof opts.budget === "function" ? opts.budget() : (opts.budget ?? plenty) } });
    }
    throw new Error(`unexpected query: ${query}`);
  };
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    if (url === "/graphql" || url === `${api}/covers/graphql`) return graphql(init);
    calls.push(url);
    if (url === `${api}/api/me`) {
      const me = await (opts.me === undefined ? member : opts.me);
      return me === null ? new Response("", { status: 401 }) : json(me);
    }
    if (url === `${api}/api/apps/covers/status`) return json({ status: "live" });
    if (url === `${api}/api/apps`) return json(apps);
    if (url === `${api}/api/antiforgery/token`) return json({ token: "t" });
    throw new Error(`unexpected call: ${url}`);
  });

  const { act, createElement } = await import("react");
  const { createRoot } = await import("react-dom/client");
  const { MemoryRouter } = await import("react-router");
  const { setRedirect } = await import("./site.ts");
  const { default: App } = await import("./App.tsx");
  setRedirect((u) => redirects.push(u));

  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  /** Lets the answers on their way arrive (and, with fake timers, `ms` pass). */
  const settle = (ms = 0) =>
    act(async () => {
      if (vi.isFakeTimers()) await vi.advanceTimersByTimeAsync(ms);
      else for (let i = 0; i < 5; i++) await new Promise((r) => setTimeout(r, 0));
    });
  await act(async () => {
    root.render(createElement(MemoryRouter, { initialEntries: [opts.path ?? "/"] }, createElement(App)));
  });
  await settle();

  const all = (selector: string) => [...container.querySelectorAll<HTMLElement>(selector)];
  const card = (title: string) => all("main button").find((b) => b.textContent!.includes(title))!;
  const button = (label: string) => all("button").find((b) => b.textContent === label);
  const click = (el: HTMLElement) => act(async () => el.click());
  const type = async (label: string, value: string) => {
    const field = all("label").find((l) => l.textContent!.startsWith(label))!.querySelector<HTMLInputElement | HTMLTextAreaElement>("input, textarea")!;
    const proto = field instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    await act(async () => {
      Object.getOwnPropertyDescriptor(proto, "value")!.set!.call(field, value);
      field.dispatchEvent(new Event("input", { bubbles: true }));
    });
  };
  return {
    container, calls, started, redirects, settle, all, card, button, click, type,
    alert: () => container.querySelector('[role="alert"]')?.textContent?.replace(/Try again$/, "") ?? null,
    statuses: () => all('[role="status"]').map((s) => s.textContent),
    header: () => container.querySelector("header")!,
    unmount: () => act(async () => root.unmount()),
  };
}

type Mounted = Awaited<ReturnType<typeof mount>>;
let page: Mounted | null = null;
const open = async (opts: Parameters<typeof mount>[0]) => (page = await mount(opts));

afterEach(async () => {
  await page?.unmount();
  page = null;
  document.body.innerHTML = "";
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.doUnmock("./DesignScreen.tsx");
  vi.resetModules();
});

describe("Behind the site the app is for signed-in members only", () => {
  it("renders nothing, and asks the api for nothing, until the site has confirmed the member; then the pick screen with the menu", async () => {
    const me = later<unknown>();
    const p = await open({ gateway: true, me: me.promise });
    expect(p.container.innerHTML).toBe("");
    expect(p.calls).toEqual([`${api}/api/me`]);
    me.resolve(member);
    await p.settle();
    expect(p.container.querySelector("h1")!.textContent).toBe("Pick a book");
    expect(p.calls.slice(0, 3)).toEqual([`${api}/api/me`, `${api}/api/apps/covers/status`, `${api}/api/apps`]);
    expect(p.calls).toContain("books");
    // The top bar: the wordmark links to the site, then "/ Book Cover", Other demos, and the member's menu on the right.
    const header = p.header();
    expect(header.querySelector(`a[href="${site}"]`)!.textContent).toBe("AgentBullpen.AI");
    expect(header.textContent).toContain("/ Book Cover");
    expect(p.button("Other demos")).toBeDefined();
    expect(header.lastElementChild!.getAttribute("aria-label")).toBe("User menu for Kim Evans");
    expect(header.textContent).not.toContain("Standalone");
    // The pick screen names no book and has no Change book: that is the design screen's.
    expect(header.textContent).toContain("/ Book Cover");
    expect(header.textContent).not.toContain("·");
    expect(p.button("Change book")).toBeUndefined();
    // Nothing differs for an admin: no Admin item, no role anywhere on the page.
    expect(p.container.innerHTML).not.toMatch(/admin|roles/i);
    expect(p.redirects).toEqual([]);
  });

  it("a visitor the site doesn't confirm sees none of it: the sign-in page with this page as returnUrl, and no other call", async () => {
    for (const me of [null, "<!doctype html>", []]) {
      const p = await open({ gateway: true, me });
      expect(p.container.innerHTML).toBe("");
      expect(p.calls).toEqual([`${api}/api/me`]);
      expect(p.redirects).toEqual([`${site}/signin?returnUrl=${encodeURIComponent(window.location.href)}`]);
      await p.unmount();
    }
  });

  it("the own-idea form has the same top bar", async () => {
    const p = await open({ gateway: true, path: "/own" });
    expect(p.container.querySelector("h1")!.textContent).toBe("Your own book idea");
    expect(p.header().querySelector(`a[href="${site}"]`)).not.toBeNull();
    expect(p.button("Other demos")).toBeDefined();
    expect(p.header().lastElementChild!.getAttribute("aria-label")).toBe("User menu for Kim Evans");
    expect(p.button("Change book")).toBeUndefined();
    expect(p.header().textContent).not.toContain("·");
  });
});

describe("The pick screen while the api starts", () => {
  const starting = "Starting the cover designer…";
  const sleeps = "It sleeps when nobody is using it. The first visit after a quiet spell takes up to a minute.";
  const didntStart = "The cover designer didn't start. Try again in a moment.";
  const titles = [...books.map((b) => b.title), "Your own book idea"];

  /** Fake timers, with AbortSignal.timeout on the same clock. */
  const fakeClock = () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    return vi.spyOn(AbortSignal, "timeout").mockImplementation((ms) => {
      const controller = new AbortController();
      setTimeout(() => controller.abort(new DOMException("The operation timed out.", "TimeoutError")), ms);
      return controller.signal;
    });
  };
  const skeletons = (p: Mounted) => p.all('main div.grid > [aria-hidden="true"]');
  const cards = (p: Mounted) => p.all("main div.grid > button").map((b) => b.firstElementChild!.nextElementSibling!.firstElementChild!.textContent);

  it("until the books are here: the status line and four skeleton cards, and no own-idea card; the second line after 10 s; then all four cards together", async () => {
    fakeClock();
    const answer = later<BooksAnswer>();
    const p = await open({ gateway: false, books: [answer.promise] });
    expect(p.statuses()).toEqual([starting]);
    const status = p.container.querySelector('[role="status"]')!;
    expect(status.getAttribute("aria-live")).toBe("polite");
    expect(skeletons(p)).toHaveLength(4);
    expect(p.all("main button")).toEqual([]);
    expect(p.container.textContent).not.toContain("Your own book idea");
    expect(p.alert()).toBeNull();
    await p.settle(9_999);
    expect(p.statuses()).toEqual([starting]);
    await p.settle(1);
    expect(p.statuses()).toEqual([starting + sleeps]);
    expect(skeletons(p)).toHaveLength(4);
    expect(p.all("main button")).toEqual([]);
    answer.resolve("books");
    await p.settle();
    expect(cards(p)).toEqual(titles);
    expect(skeletons(p)).toEqual([]);
    expect(p.statuses()).toEqual([]);
    expect(p.alert()).toBeNull();
  });

  it.each<[string, BooksAnswer]>([
    ["the call failed", "network"],
    ["an answer without the list", { status: 502 }],
  ])("%s: the alert with Try again and no cards; Try again goes back to loading and asks again", async (_, failed) => {
    fakeClock();
    const second = later<BooksAnswer>();
    const p = await open({ gateway: false, books: [failed, second.promise] });
    expect(p.alert()).toBe(didntStart);
    expect(p.statuses()).toEqual([]);
    expect(p.container.querySelector("main div.grid")).toBeNull();
    expect(p.all("main button").map((b) => b.textContent)).toEqual(["Try again"]);
    await p.click(p.button("Try again")!);
    expect(p.alert()).toBeNull();
    expect(p.statuses()).toEqual([starting]);
    expect(skeletons(p)).toHaveLength(4);
    expect(p.calls).toEqual(["books", "books"]);
    second.resolve("books");
    await p.settle();
    expect(cards(p)).toEqual(titles);
  });

  it("no answer in 150 s: the call is given up and the alert shows, not an empty list; Try again asks again", async () => {
    const timeout = fakeClock();
    const p = await open({ gateway: true, books: [later<BooksAnswer>().promise] });
    expect(timeout.mock.calls).toEqual([[150_000]]);   // the book list only: the budget read has no timeout
    await p.settle(149_999);
    expect(p.statuses()).toEqual([starting + sleeps]);
    expect(p.alert()).toBeNull();
    await p.settle(1);
    expect(p.alert()).toBe(didntStart);
    expect(p.statuses()).toEqual([]);
    expect(p.container.querySelector("main div.grid")).toBeNull();
    await p.click(p.button("Try again")!);
    await p.settle();
    expect(p.alert()).toBeNull();
    expect(cards(p)).toEqual(titles);
    expect(p.calls.filter((c) => c === "books")).toHaveLength(2);
    expect(p.redirects).toEqual([]);
  });

  it("a 401 still goes to the sign-in page, and a demo that isn't open to the under-construction page: no alert", async () => {
    let p = await open({ gateway: true, books: [{ status: 401 }] });
    expect(p.redirects).toEqual([`${site}/signin?returnUrl=${encodeURIComponent(window.location.href)}`]);
    expect(p.alert()).toBeNull();
    await p.unmount();
    p = await open({ gateway: true, books: [{ body: { type: `${problems}app-unavailable`, status: 503 }, http: 503 }] });
    expect(p.redirects).toEqual([`${site}/under-construction?app=covers`]);
    expect(p.alert()).toBeNull();
  });
});

describe("Starting a session", () => {
  it("the clicked card reads Opening…, the others stay as they are, and only one session starts", async () => {
    const answer = later<Answer>();
    const p = await open({ gateway: true, starts: [answer.promise] });
    await p.click(p.card("Treasure Island"));
    expect(p.card("Treasure Island").querySelector('[role="status"]')!.textContent).toBe("Opening…");
    expect(p.statuses()).toEqual(["Opening…"]);
    for (const other of ["The Hound of the Baskervilles", "The Three Musketeers", "Your own book idea"]) {
      expect(p.card(other).querySelector('[role="status"]')).toBeNull();
      expect(p.card(other).getAttribute("aria-disabled")).toBeNull();
    }
    await p.click(p.card("The Three Musketeers"));   // a second click while the first is on its way
    expect(p.started.map((b) => b.title)).toEqual(["Treasure Island"]);
    answer.resolve({ session: "s1" });
    await p.settle();
    expect(p.container.textContent).toBe("the design screen");
  });

  const opened = "The designer couldn't open Treasure Island — ";
  it.each<[string, Answer, string]>([
    ["provider-unavailable", failing("provider-unavailable", { detail: "The site's OpenAI account is out of credits." }), "the model service is unavailable right now. Try again in a moment."],
    ["upstream-error", failing("upstream-error"), "that took too long or failed."],
    ["timeout", failing("timeout"), "that took too long or failed."],
    ["internal-error", failing("internal-error", { traceId: "203107a3af70d48d98ad3b3e1e023356" }), "something went wrong (ref 203107a3af70d48d98ad3b3e1e023356)."],
    ["a type the app doesn't know", failing("something-new", { traceId: "abc123" }), "something went wrong (ref abc123)."],
  ])("%s: the panel above the cards says why, with Try again", async (_, answer, reason) => {
    const p = await open({ gateway: true, starts: [answer] });
    await p.click(p.card("Treasure Island"));
    await p.settle();
    expect(p.alert()).toBe(opened + reason);
    expect(p.button("Try again")).toBeDefined();
    expect(p.statuses()).toEqual([]);   // no longer opening, and the cards are on
    expect(p.card("Treasure Island").getAttribute("aria-disabled")).toBeNull();
    // The panel is above the cards.
    const alert = p.container.querySelector('[role="alert"]')!;
    expect(alert.compareDocumentPosition(p.card("Treasure Island")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("rate-limited: one automatic second try; when that fails too, the panel", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const limited = failing("rate-limited", { retryAfterSeconds: 2 });
    const p = await open({ gateway: true, starts: [limited, limited] });
    await p.click(p.card("Treasure Island"));
    await p.settle();
    expect(p.started).toHaveLength(1);
    expect(p.alert()).toBeNull();
    expect(p.statuses()).toEqual(["Opening…"]);   // still opening while it waits
    await p.settle(2000);
    expect(p.started.map((b) => b.title)).toEqual(["Treasure Island", "Treasure Island"]);
    expect(p.alert()).toBe(opened + "too many requests at once — try again in a minute.");
    expect(p.statuses()).toEqual([]);
  });

  it("rate-limited, and the second try opens the book", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const p = await open({ gateway: true, starts: [failing("rate-limited", { retryAfterSeconds: 1 }), { session: "s1" }] });
    await p.click(p.card("Treasure Island"));
    await p.settle();
    await p.settle(1000);
    expect(p.container.textContent).toBe("the design screen");
  });

  it("Try again repeats the same start; the panel clears while it opens and when it succeeds", async () => {
    const second = later<Answer>();
    const p = await open({ gateway: true, starts: [failing("timeout"), second.promise] });
    await p.click(p.card("The Three Musketeers"));
    await p.settle();
    expect(p.alert()).toBe("The designer couldn't open The Three Musketeers — that took too long or failed.");
    await p.click(p.button("Try again")!);
    expect(p.alert()).toBeNull();
    expect(p.card("The Three Musketeers").querySelector('[role="status"]')!.textContent).toBe("Opening…");
    expect(p.started).toEqual([books[1], books[1]]);
    second.resolve({ session: "s2" });
    await p.settle();
    expect(p.container.textContent).toBe("the design screen");
  });

  it("the panel clears when the author picks another book", async () => {
    const second = later<Answer>();
    const p = await open({ gateway: true, starts: [failing("timeout"), second.promise] });
    await p.click(p.card("Treasure Island"));
    await p.settle();
    expect(p.alert()).not.toBeNull();
    await p.click(p.card("The Hound of the Baskervilles"));
    expect(p.alert()).toBeNull();
    expect(p.started.map((b) => b.title)).toEqual(["Treasure Island", "The Hound of the Baskervilles"]);
  });

  it("budget-exhausted: no panel; the notice with the day's allowance and the reset time, every card off, and back on by itself at the reset", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"], now: new Date("2026-10-11T17:00:00Z") });
    const { localTime } = await import("./problems.ts");
    const resets = "2026-10-11T18:00:00Z";
    let budget: Budget = plenty;
    const p = await open({ gateway: true, budget: () => budget, starts: [failing("budget-exhausted", { resetsAtUtc: resets, dailyAllowanceUsd: 1, usedTodayUsd: 1.0003 })] });
    await p.click(p.card("Treasure Island"));
    await p.settle();
    expect(p.alert()).toBeNull();
    expect(p.statuses()).toEqual([`You've used today's $1.00. The designer comes back at ${localTime(new Date(resets))}.`]);
    const cards = p.all("main button");
    expect(cards).toHaveLength(4);
    for (const c of cards) {
      expect(c.getAttribute("aria-disabled")).toBe("true");
      expect(c.className).toMatch(/cursor-not-allowed/);
      expect(c.className).toMatch(/opacity-45/);
    }
    await p.click(p.card("The Three Musketeers"));
    await p.click(p.card("Your own book idea"));
    expect(p.started).toHaveLength(1);
    expect(p.container.querySelector("h1")!.textContent).toBe("Pick a book");
    // The reset time passes: the cards come back, and the budget is read again.
    budget = { ...plenty, resetsAtUtc: "2026-10-12T00:00:00Z" };
    const before = p.calls.filter((c) => c === "budget").length;
    await p.settle(60 * 60 * 1000 + 300);
    expect(p.statuses()).toEqual([]);
    for (const c of p.all("main button")) expect(c.getAttribute("aria-disabled")).toBeNull();
    expect(p.calls.filter((c) => c === "budget").length).toBe(before + 1);
  });

  it("an allowance that is already used up when the pick screen loads: the same state, before any start", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"], now: new Date("2026-10-11T17:00:00Z") });
    const { localTime } = await import("./problems.ts");
    const p = await open({ gateway: true, budget: { level: "generous", dailyUsd: 2, remainingUsd: 0, unlimited: false, resetsAtUtc: "2026-10-12T00:00:00Z" } });
    expect(p.statuses()).toEqual([`You've used today's $2.00. The designer comes back at ${localTime(new Date("2026-10-12T00:00:00Z"))}.`]);
    for (const c of p.all("main button")) expect(c.getAttribute("aria-disabled")).toBe("true");
    await p.click(p.card("Treasure Island"));
    expect(p.started).toEqual([]);
  });

  const off: Budget = { level: "off", dailyUsd: null, remainingUsd: 0, unlimited: false, resetsAtUtc: null };

  it("the designer off for the account: its own notice, every card off, and nothing to wait for", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"], now: new Date("2026-10-11T17:00:00Z") });
    const p = await open({ gateway: true, budget: off });
    expect(p.statuses()).toEqual(["The designer isn't turned on for your account yet."]);
    const cards = p.all("main button");
    expect(cards).toHaveLength(4);
    for (const c of cards) expect(c.getAttribute("aria-disabled")).toBe("true");
    await p.click(p.card("Treasure Island"));
    expect(p.started).toEqual([]);
    // No reset: a day later it is still off, and the budget was not read again by a timer.
    const reads = p.calls.filter((c) => c === "budget").length;
    await p.settle(26 * 60 * 60 * 1000);
    expect(p.statuses()).toEqual(["The designer isn't turned on for your account yet."]);
    expect(p.calls.filter((c) => c === "budget").length).toBe(reads);
  });

  it("a start refused with chat-off: no panel; the off state", async () => {
    const p = await open({ gateway: true, starts: [{ problem: { slug: "chat-off", level: "off" }, code: "CHAT_OFF" }] });
    await p.click(p.card("Treasure Island"));
    await p.settle();
    expect(p.alert()).toBeNull();
    expect(p.statuses()).toEqual(["The designer isn't turned on for your account yet."]);
    for (const c of p.all("main button")) expect(c.getAttribute("aria-disabled")).toBe("true");
  });

  it("no limit: no notice, and the cards stay on", async () => {
    const p = await open({ gateway: true, budget: { level: "none", dailyUsd: null, remainingUsd: null, unlimited: true, resetsAtUtc: null } });
    expect(p.statuses()).toEqual([]);
    for (const c of p.all("main button")) expect(c.getAttribute("aria-disabled")).toBeNull();
  });

  it("the allowance is read again when the tab gets the focus back: a level changed on the site shows without a reload", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"], now: new Date("2026-10-11T17:00:00Z") });
    let budget: Budget = off;
    const p = await open({ gateway: true, budget: () => budget });
    expect(p.statuses()).toEqual(["The designer isn't turned on for your account yet."]);
    const reads = () => p.calls.filter((c) => c === "budget").length;
    const before = reads();
    budget = plenty;
    vi.setSystemTime(new Date("2026-10-11T17:00:05Z"));
    window.dispatchEvent(new Event("focus"));
    await p.settle();
    expect(reads()).toBe(before + 1);
    expect(p.statuses()).toEqual([]);
    for (const c of p.all("main button")) expect(c.getAttribute("aria-disabled")).toBeNull();
    // And when the page is shown again after being hidden.
    budget = off;
    vi.setSystemTime(new Date("2026-10-11T17:00:10Z"));
    document.dispatchEvent(new Event("visibilitychange"));
    await p.settle();
    expect(reads()).toBe(before + 2);
    expect(p.statuses()).toEqual(["The designer isn't turned on for your account yet."]);
  });

  it("unauthorized (the session ended): no panel; the sign-in page, to come back to this page", async () => {
    const signIn = `${site}/signin?returnUrl=${encodeURIComponent(window.location.href)}`;
    // The gateway's 401…
    let p = await open({ gateway: true, starts: [{ status: 401 }] });
    await p.click(p.card("Treasure Island"));
    await p.settle();
    expect(p.redirects).toEqual([signIn]);
    expect(p.alert()).toBeNull();
    await p.unmount();
    // …or the api's unauthorized problem.
    p = await open({ gateway: true, starts: [{ problem: { slug: "unauthorized" }, code: "UNAUTHORIZED" }] });
    await p.click(p.card("Treasure Island"));
    await p.settle();
    expect(p.redirects).toEqual([signIn]);
    expect(p.alert()).toBeNull();
  });
});

describe("The own-idea form", () => {
  const fill = async (p: Mounted) => {
    await p.type("Working title", "The Salt Road");
    await p.type("Author name", "Kim Evans");
    await p.type("What's it about?", "A caravan crosses a drying sea.");
  };

  it("the submit button reads Opening… and is off while the session starts", async () => {
    const answer = later<Answer>();
    const p = await open({ gateway: true, path: "/own", starts: [answer.promise] });
    await fill(p);
    await p.click(p.button("Start designing")!);
    const submit = p.button("Opening…")!;
    expect(submit.getAttribute("aria-disabled")).toBe("true");
    expect((submit as HTMLButtonElement).disabled).toBe(true);
    expect(p.button("Start designing")).toBeUndefined();
    expect(p.started).toEqual([{ title: "The Salt Road", author: "Kim Evans", genre: "", about: "A caravan crosses a drying sea.", style: "designer", styleText: "" }]);
    answer.resolve({ session: "s3" });
    await p.settle();
    expect(p.container.textContent).toBe("the design screen");
  });

  it("a start that failed: the same panel above the submit button, and Try again repeats it", async () => {
    const p = await open({ gateway: true, path: "/own", starts: [failing("provider-unavailable"), { session: "s4" }] });
    await fill(p);
    await p.click(p.button("Start designing")!);
    await p.settle();
    expect(p.alert()).toBe("The designer couldn't open The Salt Road — the model service is unavailable right now. Try again in a moment.");
    const alert = p.container.querySelector('[role="alert"]')!;
    expect(alert.compareDocumentPosition(p.button("Start designing")!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(alert.closest("form")).not.toBeNull();
    await p.click(p.button("Try again")!);
    await p.settle();
    expect(p.started).toHaveLength(2);
    expect(p.started[1]).toEqual(p.started[0]);
    expect(p.container.textContent).toBe("the design screen");
  });

  it("the allowance used up: the notice, and the submit button off until the reset", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"], now: new Date("2026-10-11T17:00:00Z") });
    const { localTime } = await import("./problems.ts");
    const resets = "2026-10-11T18:00:00Z";
    let budget: Budget = plenty;
    const p = await open({ gateway: true, path: "/own", budget: () => budget, starts: [failing("budget-exhausted", { resetsAtUtc: resets, dailyAllowanceUsd: 1 })] });
    await fill(p);
    await p.click(p.button("Start designing")!);
    await p.settle();
    expect(p.alert()).toBeNull();
    expect(p.statuses()).toEqual([`You've used today's $1.00. The designer comes back at ${localTime(new Date(resets))}.`]);
    expect(p.button("Start designing")!.getAttribute("aria-disabled")).toBe("true");
    await p.click(p.button("Start designing")!);
    expect(p.started).toHaveLength(1);
    budget = { ...plenty };
    await p.settle(60 * 60 * 1000 + 300);
    expect(p.statuses()).toEqual([]);
    expect(p.button("Start designing")!.getAttribute("aria-disabled")).toBeNull();
  });
});

describe("The own-idea form, with the designer off", () => {
  it("the off notice, and the submit button off", async () => {
    const p = await open({ gateway: true, path: "/own", budget: { level: "off", dailyUsd: null, remainingUsd: 0, unlimited: false, resetsAtUtc: null } });
    expect(p.statuses()).toEqual(["The designer isn't turned on for your account yet."]);
    expect(p.button("Start designing")!.getAttribute("aria-disabled")).toBe("true");
  });
});

describe("Standalone", () => {
  it("nothing is read when the tab gets the focus back", async () => {
    const p = await open({ gateway: false });
    window.dispatchEvent(new Event("focus"));
    document.dispatchEvent(new Event("visibilitychange"));
    await p.settle();
    expect(p.calls).toEqual(["books"]);
    expect(p.statuses()).toEqual([]);
  });

  it("the top bar is as it was, and nothing is asked of the site", async () => {
    const p = await open({ gateway: false });
    expect(p.container.querySelector("h1")!.textContent).toBe("Pick a book");
    // header > [left group > [wordmark, "/ Book Cover"]], "Standalone · no sign-in": no link, no Other demos, no user menu.
    const header = p.header();
    expect(header.children).toHaveLength(2);
    const [left, right] = [...header.children];
    expect(left.children).toHaveLength(1);
    expect(left.firstElementChild!.className).toBe("flex items-center gap-16");
    expect([...left.firstElementChild!.children].map((c) => c.textContent)).toEqual(["AgentBullpen.AI", "/ Book Cover"]);
    expect(right.outerHTML).toBe('<span class="text-13 text-fg-muted">Standalone · no sign-in</span>');
    expect(header.querySelector("a, button, nav")).toBeNull();   // no Change book on the pick screen
    expect(p.statuses()).toEqual([]);
    // Only the book list was asked for, of the api on this origin: no site call, no budget, no redirect.
    expect(p.calls).toEqual(["books"]);
    expect(p.redirects).toEqual([]);
  });

  it("a failed start still shows the panel, with the fix-it text of the person running it", async () => {
    const detail = "Your OpenRouter account can't serve requests right now (credits). Top it up, or switch to another provider in docs › Configuration.";
    const p = await open({ gateway: false, starts: [failing("provider-unavailable", { detail }), failing("timeout"), { session: "s5" }] });
    await p.click(p.card("Treasure Island"));
    await p.settle();
    expect(p.alert()).toBe(
      "The designer couldn't open Treasure Island — your OpenRouter account can't serve requests right now (credits). Top it up, or switch to another provider in docs › Configuration.");
    await p.click(p.button("Try again")!);
    await p.settle();
    expect(p.alert()).toBe("The designer couldn't open Treasure Island — that took too long or failed.");
    await p.click(p.button("Try again")!);
    await p.settle();
    expect(p.container.textContent).toBe("the design screen");
    expect(p.calls).toEqual(["books", "startSession", "startSession", "startSession"]);
    expect(p.redirects).toEqual([]);
  });

  it("the own-idea form: the same panel, and no budget is read", async () => {
    const p = await open({ gateway: false, path: "/own", starts: [failing("internal-error", { traceId: "abc123" })] });
    await p.type("Working title", "The Salt Road");
    await p.type("Author name", "Kim Evans");
    await p.type("What's it about?", "A caravan crosses a drying sea.");
    await p.click(p.button("Start designing")!);
    await p.settle();
    expect(p.alert()).toBe("The designer couldn't open The Salt Road — something went wrong (ref abc123).");
    expect(p.calls).toEqual(["startSession"]);
  });
});
