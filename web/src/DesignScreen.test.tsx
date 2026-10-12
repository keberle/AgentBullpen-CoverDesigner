// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Budget, Message, Problem, Session, Version } from "./graphql.ts";

// The design screen behind the site and standalone, with the api played by one fetch: today's allowance under the input
// in its states (normal, low, used up, off, none for "No limit"), a turn the site refuses, the allowance read again when
// the tab gets the focus back, and the cost at the end of each turn's Details line. Standalone: no footer, nothing read.

// Each test loads the app's modules afresh; the first load after an install is slow.
vi.setConfig({ testTimeout: 60_000 });

const api = "https://api.agentbullpen.test";
const site = "https://agentbullpen.test";
const problems = "https://agentbullpen.ai/problems/";
const resets = "2026-10-12T00:00:00Z";
const standard = (remainingUsd: number, extra: Partial<Budget> = {}): Budget => ({ level: "standard", dailyUsd: 1, remainingUsd, unlimited: false, resetsAtUtc: resets, ...extra });
const off: Budget = { level: "off", dailyUsd: null, remainingUsd: 0, unlimited: false, resetsAtUtc: null };
const noLimit: Budget = { level: "none", dailyUsd: null, remainingUsd: null, unlimited: true, resetsAtUtc: null };
const local: Budget = { level: null, dailyUsd: null, remainingUsd: null, unlimited: true, resetsAtUtc: null };

const typography = { font: "playfair", titleSize: "M", titlePlace: "top", align: "center", titleCaps: true, authorPlace: "bottom", authorSize: "S", color: "light", scrim: "soft" } as const;
const image = { model: "gpt-image-2.5-flare", size: "1024x1536", quality: "medium", inputTokens: 100, outputTokens: 180 };
const answer = (text: string, extra: Partial<Message> = {}): Message => ({
  role: "designer", text, usage: { inputTokens: 4000, outputTokens: 106, costUsd: null }, model: "gpt-4o-mini", image: null, version: null, license: null,
  typography: null, paintProblem: null, fallbackUsed: null, ...extra,
});
const version = (n: number, extra: Partial<Version> = {}): Version => ({
  n, kind: "paint", from: null, imageDataUrl: "data:image/png;base64,iVBORw0KGgo=", scene: "a map", createdAt: "2026-10-11T17:00:00Z", image, license: "interpretive",
  concept: "The Unfolded Map — clearest", concepts: [], chosen: 0, noirSense: null, designerNotes: "", typography, ...extra,
});
const session = (extra: Partial<Session> = {}): Session => ({
  id: "s1", title: "Treasure Island", author: "Robert Louis Stevenson", license: "interpretive", model: "openai:gpt-4o-mini", budget: null,
  messages: [answer("Hello. What mood do you want?")], versions: [], ...extra,
});

type Refusal = { slug: string } & Partial<Problem>;

async function mount(opts: { gateway: boolean; budget?: Budget | null | (() => Budget | null); session?: Session; chats?: (Session | Refusal)[] }) {
  vi.resetModules();
  if (opts.gateway) {
    vi.stubEnv("VITE_API_BASE_URL", api);
    vi.stubEnv("VITE_SITE_URL", site);
  }
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  Element.prototype.scrollTo = () => {};
  const calls: string[] = [];
  const chats = [...(opts.chats ?? [])];
  const budget = () => (typeof opts.budget === "function" ? opts.budget() : opts.budget === undefined ? (opts.gateway ? standard(0.98) : local) : opts.budget);
  const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    if (url === `${api}/api/antiforgery/token`) return json({ token: "t" });
    if (url !== "/graphql" && url !== `${api}/covers/graphql`) {
      calls.push(url);
      throw new Error(`unexpected call: ${url}`);
    }
    const { query } = JSON.parse(String(init?.body)) as { query: string };
    if (query.includes("mutation Chat")) {
      calls.push("chat");
      const next = chats.shift()!;
      if ("slug" in next) {
        const { slug, ...extensions } = next;
        return json({ data: null, errors: [{ message: "refused", extensions: { code: "X", problem: { type: problems + slug, ...extensions } } }] });
      }
      return json({ data: { chat: next } });
    }
    if (query.includes("availability")) return json({ data: { availability: { chat: true, chatNote: null, images: true, imagesNote: null } } });
    if (query.includes("query Session")) return json({ data: { session: opts.session ?? session() } });
    if (query.includes("chatModels")) {
      calls.push("models");
      return json({ data: { chatModels: [{ id: "openai:gpt-4o-mini", displayName: "OpenAI: GPT-4o-mini", provider: "openai", isDefault: true, available: true, unavailableUntilUtc: null }], budget: budget() } });
    }
    if (query.trim().startsWith("{ budget")) {
      calls.push("budget");
      return json({ data: { budget: budget() } });
    }
    throw new Error(`unexpected query: ${query}`);
  });

  const { act, createElement } = await import("react");
  const { createRoot } = await import("react-dom/client");
  const { MemoryRouter, Route, Routes } = await import("react-router");
  const { default: DesignScreen } = await import("./DesignScreen.tsx");
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  const settle = (ms = 0) =>
    act(async () => {
      if (vi.isFakeTimers()) await vi.advanceTimersByTimeAsync(ms);
      else for (let i = 0; i < 6; i++) await new Promise((r) => setTimeout(r, 0));
    });
  const { useNavigate } = await import("react-router");
  // As App.tsx wires it: the screen's way back is the pick screen's route.
  const Screen = () => {
    const navigate = useNavigate();
    return createElement(DesignScreen, { onBack: () => navigate("/") });
  };
  await act(async () => {
    root.render(
      createElement(MemoryRouter, { initialEntries: ["/design/s1"] },
        createElement(Routes, null,
          createElement(Route, { path: "/", element: "the pick screen" }),
          createElement(Route, { path: "/design/:sessionId", element: createElement(Screen) }))),
    );
  });
  await settle();
  await settle();

  const all = (selector: string) => [...container.querySelectorAll<HTMLElement>(selector)];
  const rail = () => container.querySelector<HTMLElement>('section[aria-label], section') ?? container;
  const input = () => container.querySelector<HTMLInputElement>('input[aria-label="Message"]')!;
  const button = (label: string | RegExp) => all("button").find((b) => (typeof label === "string" ? b.textContent === label : label.test(b.textContent ?? "")))!;
  const meter = () => container.querySelector<HTMLElement>('[role="meter"]');
  return {
    container, calls, settle, all, rail, input, button, meter,
    click: (el: HTMLElement) => act(async () => el.click()),
    statuses: () => all('[role="status"]').map((s) => s.textContent),
    footer: () => (meter() ? [...meter()!.parentElement!.firstElementChild!.children].map((c) => c.textContent) : null),
    bar: () => meter()?.firstElementChild as HTMLElement | undefined,
    summaries: () => all("summary").map((s) => s.textContent!.replace(/\s+/g, " ").trim()),
    reads: () => calls.filter((c) => c === "budget").length,
    unmount: () => act(async () => root.unmount()),
  };
}

let page: Awaited<ReturnType<typeof mount>> | null = null;
const open = async (opts: Parameters<typeof mount>[0]) => (page = await mount(opts));

afterEach(async () => {
  await page?.unmount();
  page = null;
  document.body.innerHTML = "";
  vi.useRealTimers();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("Today's allowance under the designer's input", () => {
  it("normal: what is left, when it resets, the meter in the accent; the input on", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"], now: new Date("2026-10-11T17:00:00Z") });
    const { localTime } = await import("./allowance.ts");
    const p = await open({ gateway: true, budget: standard(0.98) });
    expect(p.footer()).toEqual(["$0.98 of $1.00 left today", `Resets ${localTime(new Date(resets))}`]);
    expect(p.meter()!.getAttribute("aria-valuenow")).toBe("98");
    expect(p.bar()!.className).toContain("bg-accent");
    expect(p.bar()!.style.width).toBe("98%");
    expect(p.statuses()).toEqual([]);
    expect(p.input().disabled).toBe(false);
    expect(p.container.textContent).not.toMatch(/tokens left today/);
  });

  it("running low: under 10 % left, the warning colour; still on", async () => {
    const p = await open({ gateway: true, budget: standard(0.06) });
    expect(p.footer()![0]).toBe("$0.06 of $1.00 left today");
    expect(p.meter()!.getAttribute("aria-valuenow")).toBe("6");
    expect(p.bar()!.className).toContain("bg-warning");
    expect(p.input().disabled).toBe(false);
  });

  it("used up: the notice, an empty meter, the input, Send, the chip and the license control off; back by themselves at the reset", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"], now: new Date("2026-10-11T17:00:00Z") });
    const { localTime } = await import("./allowance.ts");
    const at = localTime(new Date("2026-10-11T18:00:00Z"));
    let budget = standard(0, { resetsAtUtc: "2026-10-11T18:00:00Z" });
    const p = await open({ gateway: true, budget: () => budget });
    expect(p.statuses()).toEqual([`You've used today's $1.00. The designer comes back at ${at}.`]);
    expect(p.footer()).toEqual(["$0.00 of $1.00 left today", `Resets ${at}`]);
    expect(p.meter()!.getAttribute("aria-valuenow")).toBe("0");
    expect(p.input().disabled).toBe(true);
    expect(p.button("Send").getAttribute("aria-disabled")).toBe("true");
    expect(p.button(/^Try: /).getAttribute("aria-disabled")).toBe("true");
    for (const radio of p.all('[role="radio"]')) expect((radio as HTMLButtonElement).disabled).toBe(true);
    await p.click(p.button(/^Try: /));
    expect(p.calls).not.toContain("chat");
    // The reset time passes: the balance is read again and everything is back.
    budget = standard(1, { resetsAtUtc: "2026-10-12T18:00:00Z" });
    await p.settle(60 * 60 * 1000 + 300);
    expect(p.statuses()).toEqual([]);
    expect(p.footer()![0]).toBe("$1.00 of $1.00 left today");
    expect(p.input().disabled).toBe(false);
    expect(p.button(/^Try: /).getAttribute("aria-disabled")).toBeNull();
  });

  it("off: its own notice and line, no reset time, the input off, and no timer", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"], now: new Date("2026-10-11T17:00:00Z") });
    const p = await open({ gateway: true, budget: off });
    expect(p.statuses()).toEqual(["The designer isn't turned on for your account yet."]);
    expect(p.footer()).toEqual(["The designer is off for your account"]);
    expect(p.input().disabled).toBe(true);
    expect(p.button("Send").getAttribute("aria-disabled")).toBe("true");
    const reads = p.calls.length;
    await p.settle(26 * 60 * 60 * 1000);
    expect(p.calls.length).toBe(reads);
    expect(p.input().disabled).toBe(true);
  });

  it("no limit: no footer, and the input on", async () => {
    const p = await open({ gateway: true, budget: noLimit });
    expect(p.meter()).toBeNull();
    expect(p.statuses()).toEqual([]);
    expect(p.input().disabled).toBe(false);
  });

  it("a turn the site refuses as budget-exhausted: no error bubble; the used-up state with the problem's amount and time", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"], now: new Date("2026-10-11T17:00:00Z") });
    const { localTime } = await import("./allowance.ts");
    const p = await open({ gateway: true, budget: standard(0.0002), chats: [{ slug: "budget-exhausted", resetsAtUtc: resets, dailyAllowanceUsd: 1, usedTodayUsd: 1.0003 }] });
    expect(p.input().disabled).toBe(false);
    await p.click(p.button(/^Try: /));
    await p.settle();
    expect(p.all('[role="alert"]')).toEqual([]);
    expect(p.statuses()).toEqual([`You've used today's $1.00. The designer comes back at ${localTime(new Date(resets))}.`]);
    expect(p.footer()![0]).toBe("$0.00 of $1.00 left today");
    expect(p.input().disabled).toBe(true);
  });

  it("a turn the site refuses as chat-off: the off state", async () => {
    const p = await open({ gateway: true, budget: standard(0.5), chats: [{ slug: "chat-off", level: "off" }] });
    await p.click(p.button(/^Try: /));
    await p.settle();
    expect(p.all('[role="alert"]')).toEqual([]);
    expect(p.statuses()).toEqual(["The designer isn't turned on for your account yet."]);
    expect(p.footer()).toEqual(["The designer is off for your account"]);
    expect(p.input().disabled).toBe(true);
  });

  it("after a turn the footer shows the balance that came with it", async () => {
    const painted = session({ budget: standard(0.9384), versions: [version(1)], messages: [answer("Painted."), answer("Painted.", { version: 1, image, usage: { inputTokens: 1500, outputTokens: 200, costUsd: 0.0416 } })] });
    const p = await open({ gateway: true, budget: standard(0.98), chats: [painted] });
    expect(p.footer()![0]).toBe("$0.98 of $1.00 left today");
    await p.click(p.button(/^Try: /));
    await p.settle();
    expect(p.footer()![0]).toBe("$0.93 of $1.00 left today");
    expect(p.summaries().at(-1)).toBe("Version 1 · Details · 1 image · 1,980 tokens · $0.0416");
  });

  it("the allowance is read again when the tab gets the focus back, and when the page is shown again", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"], now: new Date("2026-10-11T17:00:00Z") });
    let budget: Budget = standard(0);
    const p = await open({ gateway: true, budget: () => budget });
    expect(p.input().disabled).toBe(true);
    const before = p.reads();
    budget = { level: "generous", dailyUsd: 2, remainingUsd: 0.96, unlimited: false, resetsAtUtc: resets };
    vi.setSystemTime(new Date("2026-10-11T17:00:05Z"));
    window.dispatchEvent(new Event("focus"));
    await p.settle();
    expect(p.reads()).toBe(before + 1);
    expect(p.footer()![0]).toBe("$0.96 of $2.00 left today");
    expect(p.input().disabled).toBe(false);
    expect(p.statuses()).toEqual([]);
    budget = off;
    vi.setSystemTime(new Date("2026-10-11T17:00:10Z"));
    document.dispatchEvent(new Event("visibilitychange"));
    await p.settle();
    expect(p.reads()).toBe(before + 2);
    expect(p.footer()).toEqual(["The designer is off for your account"]);
  });
});

describe("The design screen's top bar", () => {
  it("names the book and has Change book in the same group, which goes back to the pick screen; the tab reads the title", async () => {
    document.title = "Book Cover · AgentBullpen.AI";
    const p = await open({ gateway: false, budget: local });
    const header = p.container.querySelector("header")!;
    const group = header.firstElementChild!.firstElementChild!;
    expect([...group.children].map((c) => c.textContent)).toEqual(["AgentBullpen.AI", "/ Book Cover · Treasure Island", "Change book"]);
    expect(group.children[2].tagName).toBe("BUTTON");
    expect(header.textContent).toContain("Standalone · no sign-in");
    // The old way back, at the bottom of the chat panel, is gone.
    expect(p.container.textContent).not.toContain("Pick a different book");
    expect(p.all("button").filter((b) => /book/i.test(b.textContent ?? "")).map((b) => b.textContent)).toEqual(["Change book"]);
    expect(document.title).toBe("Treasure Island · Book Cover");
    await p.click(p.button("Change book"));
    expect(p.container.textContent).toBe("the pick screen");
    expect(document.title).toBe("Book Cover · AgentBullpen.AI");
  });

  it("the author's own title is the one shown", async () => {
    const p = await open({ gateway: false, budget: local, session: session({ title: "The Salt Road", author: "Kim Evans" }) });
    expect(p.container.querySelector("header")!.textContent).toContain("/ Book Cover · The Salt Road");
    expect(document.title).toBe("The Salt Road · Book Cover");
  });

  it("behind the site: the same, before Other demos and the member's menu", async () => {
    const p = await open({ gateway: true });
    const header = p.container.querySelector("header")!;
    expect([...header.firstElementChild!.firstElementChild!.children].map((c) => c.textContent)).toEqual(["AgentBullpen.AI", "/ Book Cover · Treasure Island", "Change book"]);
    expect(header.textContent).not.toContain("Standalone");
    await p.click(p.button("Change book"));
    expect(p.container.textContent).toBe("the pick screen");
  });

  it("the button stays at every width: only the name, Other demos and the standalone note give way", async () => {
    const p = await open({ gateway: false, budget: local });
    const header = p.container.querySelector("header")!;
    const [, name, change] = [...header.firstElementChild!.firstElementChild!.children];
    expect(name.className).toMatch(/max-md:hidden/);
    expect(name.className).toMatch(/truncate/);
    expect(change.className).not.toMatch(/hidden/);
    expect(header.lastElementChild!.className).toMatch(/max-sm:hidden/);
  });
});

describe("The cost at the end of a turn's Details line", () => {
  const history = session({
    versions: [version(1), version(2)],
    messages: [
      answer("Hello."),
      answer("Painted.", { version: 2, image, license: "interpretive", usage: { inputTokens: 1500, outputTokens: 200, costUsd: 0.0412 } }),
      answer("Playfair now.", { typography, usage: { inputTokens: 8900, outputTokens: 82, costUsd: 0.00091 } }),
      answer("A tiny one.", { usage: { inputTokens: 10, outputTokens: 5, costUsd: 0.000003 } }),
    ],
  });

  it("a painted turn, a typography turn and a talking turn: tokens, then the cost to 4 decimals", async () => {
    const p = await open({ gateway: true, session: history });
    expect(p.summaries()).toEqual([
      "Details · 4,106 tokens",
      "Version 2 · Details · 1 image · 1,980 tokens · $0.0412",
      "Typography · Details · 8,982 tokens · $0.0009",
      "Details · 15 tokens · <$0.0001",
    ]);
    expect(p.container.textContent).not.toContain("cost unknown");
    expect(p.container.textContent).toContain("Cost: $0.0412 (chat, concepts and the image)");
  });

  it("no cost known: tokens only", async () => {
    const none = session({ versions: [version(1)], messages: [answer("Painted.", { version: 1, image, usage: { inputTokens: 1500, outputTokens: 200 } })] });
    const p = await open({ gateway: false, session: none, budget: local });
    expect(p.summaries()).toEqual(["Version 1 · Details · 1 image · 1,980 tokens"]);
    expect(p.container.textContent).not.toMatch(/\$|cost/i);
  });
});

describe("Standalone", () => {
  it("no footer, no notice, and nothing is read when the tab gets the focus back", async () => {
    const p = await open({ gateway: false, budget: local });
    expect(p.meter()).toBeNull();
    expect(p.statuses()).toEqual([]);
    expect(p.input().disabled).toBe(false);
    const before = [...p.calls];
    window.dispatchEvent(new Event("focus"));
    document.dispatchEvent(new Event("visibilitychange"));
    await p.settle();
    expect(p.calls).toEqual(before);
    expect(p.calls).toEqual(["models"]);   // the picker's list, from the api on this origin; never the site
  });

  it("a provider's own cost shows on the turn; still no footer", async () => {
    const turn = session({ budget: local, messages: [answer("Hello."), answer("Sure.", { usage: { inputTokens: 300, outputTokens: 40, costUsd: 0.00025 } })] });
    const p = await open({ gateway: false, budget: local, chats: [turn] });
    await p.click(p.button(/^Try: /));
    await p.settle();
    expect(p.summaries().at(-1)).toBe("Details · 340 tokens · $0.0003");
    expect(p.meter()).toBeNull();
  });
});

describe("A type change is a version: the strip, the Details line and the cover", () => {
  const book = { title: "Treasure Island", author: "Robert Louis Stevenson" };
  const bangers = { ...typography, font: "bangers", fontFamily: "Bangers" } as const;
  const script = { ...typography, font: "greatvibes", fontFamily: "Great Vibes", titleCaps: false } as const;
  const three = () =>
    session({
      versions: [version(1), version(2, { kind: "type", from: 1, typography: bangers }), version(3, { kind: "type", from: 2, typography: script })],
      messages: [
        answer("Painted.", { version: 1, license: "interpretive", image }),
        answer("Comic.", { version: 2, typography: bangers }),
        answer("Script.", { version: 3, typography: script }),
      ],
    });
  const thumbs = (p: { all: (s: string) => HTMLElement[] }) => p.all('button[aria-label^="Version "]');
  const family = (el: Element) => (el.querySelector("span[style*='font-family']") as HTMLElement).style.fontFamily;
  const cover = (p: { container: HTMLElement }) => p.container.querySelector<HTMLElement>('[aria-label^="Book cover"]')!;

  it("three versions of one plate are three thumbnails, each with its own type", async () => {
    const p = await open({ gateway: false, budget: local, session: three() });
    expect(thumbs(p).map((b) => b.getAttribute("aria-label"))).toEqual(["Version 1", "Version 2", "Version 3"]);
    expect(thumbs(p).map((b) => b.querySelector("img")!.getAttribute("src"))).toEqual(Array(3).fill(version(1).imageDataUrl));
    expect(thumbs(p).map(family)).toEqual([expect.stringContaining("Playfair Display"), expect.stringContaining("Bangers"), expect.stringContaining("Great Vibes")]);
    // The type on a thumbnail is the cover's, in the cover's own units, and is not read out twice.
    expect(thumbs(p)[1].style.containerType).toBe("size");
    expect(thumbs(p)[1].textContent).toContain(book.title.toUpperCase());
    expect(thumbs(p)[2].textContent).toContain(book.title); // the script face: as typed
    expect(thumbs(p).map((b) => b.querySelector("[aria-hidden='true']")!.textContent!.includes(book.author.toUpperCase()))).toEqual([true, true, false]);
  });

  it("a type turn's Details line names its version, with show when another is on screen", async () => {
    const p = await open({ gateway: false, budget: local, session: three() });
    expect(p.summaries()).toEqual([
      "Version 1 · show · Details · 1 image · 4,386 tokens",
      "Version 2 · show · Typography · Details · 4,106 tokens",
      "Version 3 · Typography · Details · 4,106 tokens", // the latest is the one on screen
    ]);
    expect(cover(p).getAttribute("aria-label")).toBe("Book cover, version 3");
    expect(family(cover(p))).toContain("Great Vibes");
  });

  it("selecting version 1 shows the original type", async () => {
    const p = await open({ gateway: false, budget: local, session: three() });
    await p.click(thumbs(p)[0]);
    expect(cover(p).getAttribute("aria-label")).toBe("Book cover, version 1");
    expect(family(cover(p))).toContain("Playfair Display");
    expect(thumbs(p).map((b) => b.getAttribute("aria-pressed"))).toEqual(["true", "false", "false"]);
    // show on the second answer moves to its version.
    await p.click(p.all("summary a")[0]);
    expect(cover(p).getAttribute("aria-label")).toBe("Book cover, version 2");
    expect(family(cover(p))).toContain("Bangers");
  });

  it("a type turn's new version is shown at once", async () => {
    const before = session({ versions: [version(1)], messages: [answer("Painted.", { version: 1, license: "interpretive", image })] });
    const after = session({
      versions: [version(1), version(2, { kind: "type", from: 1, typography: bangers })],
      messages: [...before.messages, { ...answer("", {}), role: "user", text: "A comic-book font." }, answer("Comic.", { version: 2, typography: bangers })],
    });
    const p = await open({ gateway: false, budget: local, session: before, chats: [after] });
    await p.click(p.button(/^Try: /));
    await p.settle();
    expect(cover(p).getAttribute("aria-label")).toBe("Book cover, version 2");
    expect(thumbs(p)).toHaveLength(2);
    expect(p.summaries().at(-1)).toBe("Version 2 · Typography · Details · 4,106 tokens");
  });

  it("asks for each version's kind and the version it was made from", async () => {
    const { session: load } = await import("./graphql.ts");
    let asked = "";
    vi.stubGlobal("fetch", async (_: string, init?: RequestInit) => {
      asked = (JSON.parse(String(init?.body)) as { query: string }).query;
      return new Response(JSON.stringify({ data: { session: null } }), { status: 200 });
    });
    await load("s1");
    expect(asked).toContain("versions { n kind from imageDataUrl");
  });
});

describe("A typography turn's Details: the face, what was asked, and the legibility", () => {
  const lines = (p: { container: HTMLElement }) => [...p.container.querySelectorAll("details li")].map((li) => li.textContent!.replace(/\s+/g, " ").trim());
  const turn = (extra: Partial<Message["typography"] & object>, text = "Set.") =>
    session({ versions: [version(1)], messages: [answer(text, { typography: { ...typography, fontFamily: "Playfair Display", ...extra } })] });

  it("a style asked for: the family and the author's words", async () => {
    const p = await open({ gateway: false, budget: local, session: turn({ font: "bangers", fontFamily: "Bangers", requestedFont: "comic book", substituted: false, legibility: { title: "Lc 78", author: "Lc 64", changed: "scrim raised to strong" } }) });
    expect(lines(p)).toEqual([
      "font Bangers · asked “comic book”",
      "title M top center caps · author bottom S · light · soft · no image",
      "Legibility: title Lc 78 · author Lc 64 · scrim raised to strong",
      "Chat: 4,000 in · 106 out · gpt-4o-mini",
    ]);
  });

  it("a named face that isn't in the library: substituted", async () => {
    const p = await open({ gateway: false, budget: local, session: turn({ font: "comicneue", fontFamily: "Comic Neue", requestedFont: "Comic Sans", substituted: true, legibility: { title: "Lc 101", author: "Lc 107", changed: null } }) });
    expect(lines(p).slice(0, 3)).toEqual([
      "font Comic Neue · asked “Comic Sans” · substituted: not an open font here",
      "title M top center caps · author bottom S · light · soft · no image",
      "Legibility: title Lc 101 · author Lc 107",
    ]);
  });

  it("nothing asked: no asked part", async () => {
    const p = await open({ gateway: false, budget: local, session: turn({ requestedFont: null, substituted: false, legibility: null }) });
    expect(lines(p)[0]).toBe("font Playfair Display");
    expect(p.container.textContent).not.toContain("Legibility");
  });
});
