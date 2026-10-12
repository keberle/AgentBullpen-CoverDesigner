// Each state the author sees, its text and whether the input stays on (TemporalGraph's chat tests, adapted).
import { describe, expect, it } from "vitest";
import { GraphQLError, type ChatModel, type Problem } from "./graphql.ts";
import { allowanceView, holdsOff } from "./allowance.ts";
import {
  failedText,
  fallbackModel,
  fallbackNote,
  modelOptionLabel,
  modelSwitchNote,
  paintNote,
  problemBase,
  problemOf,
  problemView,
  rateText,
  rateWaitText,
  restingText,
  signedOutText,
  startView,
} from "./problems.ts";

const p = (slug: string, ext: Partial<Problem> = {}): Problem => ({ type: problemBase + slug, ...ext });
const time = (d: Date) => d.toISOString().slice(11, 16); // the tests' clock words: "18:00"
const now = new Date("2026-10-09T17:00:00Z");
const view = (problem: Problem | null, opts: { gateway?: boolean; attempt?: number } = {}) =>
  problemView(problem, "the API's message", { gateway: opts.gateway ?? true, attempt: opts.attempt, now, time });

describe("what the author sees for each problem", () => {
  it("budget-exhausted: the day's allowance and when the designer comes back, the input off until then", () => {
    const v = view(p("budget-exhausted", { resetsAtUtc: "2026-10-10T00:00:00Z", dailyAllowanceUsd: 1, usedTodayUsd: 1.000412 }));
    expect(v.text).toBe("You've used today's $1.00. The designer comes back at 00:00.");
    expect([v.kind, v.disableInput, v.retry, v.dailyUsd]).toEqual(["budget", true, null, 1]);
    expect(v.until?.toISOString()).toBe("2026-10-10T00:00:00.000Z");
    // Without the amount or the time (an older site): "allowance", and the next UTC midnight.
    const bare = view(p("budget-exhausted"));
    expect(bare.text).toBe("You've used today's allowance. The designer comes back at 00:00.");
    expect(bare.until?.toISOString()).toBe("2026-10-10T00:00:00.000Z");
  });

  it("budget-exhausted: the input comes back by itself at the reset", () => {
    const budget = { level: "standard", dailyUsd: 1, remainingUsd: 0, unlimited: false, resetsAtUtc: "2026-10-10T00:00:00Z" };
    expect(holdsOff(allowanceView(budget, { now }), now)).toBe(true); // off now
    const later = new Date("2026-10-10T00:00:01Z");
    expect(holdsOff(allowanceView(budget, { now: later }), later)).toBe(false); // on after the reset
    expect(holdsOff(allowanceView({ ...budget, remainingUsd: 0.12 }, { now }), now)).toBe(false);
    expect(holdsOff(allowanceView({ dailyUsd: null, remainingUsd: null, unlimited: true }, { now }), now)).toBe(false);
  });

  it("chat-off: its own words, the input off, and nothing to wait for", () => {
    const v = view(p("chat-off", { level: "off" }));
    expect([v.kind, v.text, v.disableInput, v.retry, v.until]).toEqual(["chat-off", "The designer isn't turned on for your account yet.", true, null, undefined]);
    expect(problemOf(new GraphQLError("x", "CHAT_OFF"))?.type).toBe(problemBase + "chat-off");
  });

  it("a start the site refused for the allowance: used up with its amount and time, or off; never the failure panel", () => {
    const used = startView(p("budget-exhausted", { resetsAtUtc: "2026-10-10T00:00:00Z", dailyAllowanceUsd: 2 }), { gateway: true, now });
    expect(used).toEqual({ kind: "budget", until: new Date("2026-10-10T00:00:00Z"), dailyUsd: 2 });
    expect(startView(p("chat-off"), { gateway: true, now })).toEqual({ kind: "chat-off" });
  });

  it("rate-limited: One moment… and one automatic send after the wait (1–30 s), then the second-failure text", () => {
    const first = view(p("rate-limited", { retryAfterSeconds: 7 }));
    expect([first.text, first.retry, first.retryAfterMs, first.disableInput]).toEqual([rateWaitText, "auto", 7000, false]);
    expect(rateWaitText).toBe("One moment…");
    expect(view(p("rate-limited", { retryAfterSeconds: 600 })).retryAfterMs).toBe(30000);
    expect(view(p("rate-limited")).retryAfterMs).toBe(5000);
    const second = view(p("rate-limited"), { attempt: 1 });
    expect([second.text, second.retry, second.disableInput]).toEqual([rateText, null, false]);
    expect(rateText).toBe("Too many requests at once — try again in a minute.");
  });

  it("provider-unavailable: resting behind the gateway, the API's fix-it text standalone; Try again, input on", () => {
    const fixIt = "Your OpenRouter account can't serve requests right now (credits). Top it up, or switch to another provider in docs › Configuration.";
    const gateway = view(p("provider-unavailable", { reason: "credits", detail: "SITE TEXT" }));
    expect([gateway.text, gateway.retry, gateway.disableInput]).toEqual([restingText, "button", false]);
    expect(restingText).toBe("The designer is resting — the model service is unavailable right now. The site owner has been notified. Try again later.");
    const standalone = view(p("provider-unavailable", { reason: "credits", detail: fixIt }), { gateway: false });
    expect([standalone.text, standalone.retry]).toEqual([fixIt, "button"]);
  });

  it("upstream-error and timeout (the whole turn): That took too long or failed, with Try again", () => {
    for (const slug of ["upstream-error", "timeout"]) {
      const v = view(p(slug));
      expect([v.text, v.retry, v.disableInput]).toEqual([failedText, "button", false]);
    }
    expect(failedText).toBe("That took too long or failed — try again.");
  });

  it("model-not-allowed: a model state (the picker moves), input on", () => {
    const v = view(p("model-not-allowed", { model: "openai:gpt-6-luna" }));
    expect([v.kind, v.disableInput]).toEqual(["model", false]);
  });

  it("unauthorized: Your session ended — sign in again, input off", () => {
    const v = view(p("unauthorized"));
    expect([v.text, v.disableInput]).toEqual([signedOutText, true]);
    expect(signedOutText).toBe("Your session ended — sign in again");
  });

  it("internal-error: Something went wrong with the reference", () => {
    const v = view(p("internal-error", { traceId: "4bf92f3577b34da6a3ce929d0e0e4736" }));
    expect([v.text, v.disableInput, v.retry]).toEqual(["Something went wrong (ref 4bf92f3577b34da6a3ce929d0e0e4736).", false, null]);
  });

  it("anything else: the problem's detail, else the message", () => {
    expect(view(p("bad-request", { detail: "license must be one of: …" })).text).toBe("license must be one of: …");
    expect(view(null).text).toBe("the API's message");
  });

  it("an older API's error without a problem is read by its code", () => {
    expect(problemOf(new GraphQLError("x", "BUDGET_EXHAUSTED"))?.type).toBe(problemBase + "budget-exhausted");
    expect(problemOf(new GraphQLError("x", "MODEL_ERROR"))?.type).toBe(problemBase + "upstream-error");
    expect(problemOf(new GraphQLError("x", "UNKNOWN_SESSION"))).toBeNull();
    const own = p("timeout");
    expect(problemOf(new GraphQLError("x", "TIMEOUT", own))).toBe(own);
  });
});

describe("notes under an answer", () => {
  it("the fallback note", () => {
    expect(fallbackNote({ requested: "Claude Haiku 5.5", used: "GPT-4o mini" })).toBe("Answered by GPT-4o mini — Claude Haiku 5.5 is unavailable right now.");
  });

  it("the paint-failed notes: Retry paint only where trying again could help", () => {
    expect(paintNote(p("provider-unavailable", { detail: "Your OpenAI account …" }), true)).toEqual({
      text: "The picture couldn't be painted — the image service is unavailable right now.",
      retry: false,
    });
    expect(paintNote(p("provider-unavailable", { detail: "Your OpenAI account can't serve requests right now (credits)." }), false).text).toBe(
      "The picture couldn't be painted — the image service is unavailable right now. Your OpenAI account can't serve requests right now (credits).",
    );
    for (const slug of ["upstream-error", "timeout", "concept-failed", "rate-limited"]) {
      expect(paintNote(p(slug), true)).toEqual({ text: "The picture couldn't be painted — that took too long or failed.", retry: true });
    }
    expect(paintNote(p("content-policy"), true)).toEqual({ text: "The image service declined that picture. Try describing it differently.", retry: false });
  });
});

describe("unavailable models", () => {
  const m = (id: string, ext: Partial<ChatModel> = {}): ChatModel => ({ id, displayName: id.split(":")[1], provider: "openai", isDefault: false, ...ext });

  it("greys out an unavailable model with its time, and moves to an available default", () => {
    const luna = m("openai:gpt-6-luna", { available: false, unavailableUntilUtc: "2026-10-09T18:15:00Z" });
    expect(modelOptionLabel(luna, time)).toBe("gpt-6-luna — unavailable until 18:15");
    expect(modelOptionLabel(m("openai:x", { available: false }), time)).toBe("x — unavailable");
    expect(modelOptionLabel(m("openai:gpt-4o-mini"), time)).toBe("gpt-4o-mini");
    const list = [m("openai:gpt-4o-mini", { isDefault: true, available: false }), luna, m("anthropic:claude-haiku-5-5")];
    expect(fallbackModel(list)?.id).toBe("anthropic:claude-haiku-5-5");
    expect(fallbackModel([luna])).toBeNull();
    expect(modelSwitchNote("GPT-6 Luna", "GPT-4o mini")).toBe("GPT-6 Luna is unavailable right now — switched to GPT-4o mini.");
  });
});
