// What the author sees for each problem (docs/design/errors.md), the web side of RFC 9457 problems. Copied from
// AgentBullpen-TemporalGraph's web/src/chat.ts (problemView and its helpers), with "the designer" for "the
// characters" and the paint-failed note this demo adds. The API's GraphQL errors carry extensions.problem; an error
// from an older API without one is read by its code.
import { designerOffNotice, localTime, nextUtcMidnight, usedUpNotice } from "./allowance.ts";
import type { ChatModel, GraphQLError, Problem } from "./graphql.ts";
import { modelLabel } from "./gateway.ts";

export { localTime, nextUtcMidnight };

export const problemBase = "https://agentbullpen.ai/problems/";

/** The slug of a problem's type URI (`budget-exhausted`, …). */
export function problemSlug(p: Problem): string {
  return p.type.startsWith(problemBase) ? p.type.slice(problemBase.length) : p.type;
}

/** The error's problem; for an error without one (an older API), a problem from its code. */
export function problemOf(e: GraphQLError | undefined): Problem | null {
  if (!e) return null;
  if (e.problem) return e.problem;
  const slug: Record<string, string> = {
    BUDGET_EXHAUSTED: "budget-exhausted",
    CHAT_OFF: "chat-off",
    RATE_LIMITED: "rate-limited",
    UNKNOWN_MODEL: "model-not-allowed",
    PROVIDER_UNAVAILABLE: "provider-unavailable",
    MODEL_ERROR: "upstream-error",
    IMAGE_ERROR: "upstream-error",
    CONCEPT_ERROR: "concept-failed",
    TIMEOUT: "timeout",
    UNAUTHORIZED: "unauthorized",
  };
  return e.code && slug[e.code] ? { type: problemBase + slug[e.code], detail: e.message } : null;
}

export type ProblemView = {
  /** budget (today's allowance used up), chat-off (the account's level is off), the first rate limit (wait, then send
   * once more), the second, the model service down, a failed or slow turn, a model not allowed, signed out, an unexpected
   * error, or anything else (shown as the API wrote it). */
  kind: "budget" | "chat-off" | "rate-wait" | "rate" | "unavailable" | "failed" | "model" | "signed-out" | "internal" | "other";
  text: string;
  /** The input stays off: until `until` (budget), while the designer is off for the account, or until the author signs in again. */
  disableInput: boolean;
  /** "auto": send again by itself after `retryAfterMs`; "button": a Try again button; null: neither. */
  retry: "auto" | "button" | null;
  retryAfterMs?: number;
  until?: Date;
  /** budget: the day's allowance, when the problem says. */
  dailyUsd?: number;
};

export const restingText =
  "The designer is resting — the model service is unavailable right now. The site owner has been notified. Try again later.";
export const failedText = "That took too long or failed — try again.";
export const signedOutText = "Your session ended — sign in again";
export const rateWaitText = "One moment…";
export const rateText = "Too many requests at once — try again in a minute.";

/** The panel's state for a failed turn. `attempt`: 0 = the first send, 1 = the automatic second one. */
export function problemView(
  problem: Problem | null,
  message: string,
  opts: { gateway: boolean; attempt?: number; now?: Date; time?: (d: Date) => string },
): ProblemView {
  const now = opts.now ?? new Date();
  const time = opts.time ?? ((d: Date) => localTime(d, now));
  switch (problem ? problemSlug(problem) : null) {
    case "budget-exhausted": {
      // Shown as the used-up state (allowance.ts): the notice, the footer, everything off until the reset.
      const until = problem?.resetsAtUtc ? new Date(problem.resetsAtUtc) : nextUtcMidnight(now);
      const dailyUsd = typeof problem?.dailyAllowanceUsd === "number" ? problem.dailyAllowanceUsd : undefined;
      return { kind: "budget", text: usedUpNotice(dailyUsd ?? null, until, time), disableInput: true, retry: null, until, dailyUsd };
    }
    case "chat-off":
      // The account's allowance level is off. Nothing resets it, so no time and no timer.
      return { kind: "chat-off", text: designerOffNotice, disableInput: true, retry: null };
    case "rate-limited":
      if ((opts.attempt ?? 0) === 0) {
        const seconds = Math.min(Math.max(problem?.retryAfterSeconds ?? 5, 1), 30);
        return { kind: "rate-wait", text: rateWaitText, disableInput: false, retry: "auto", retryAfterMs: seconds * 1000 };
      }
      return { kind: "rate", text: rateText, disableInput: false, retry: null };
    case "provider-unavailable":
      // Standalone: the API's fix-it text (the person running it can fix it). Gateway: never the site's account.
      return {
        kind: "unavailable",
        text: opts.gateway ? restingText : (problem?.detail ?? "The model provider can't serve requests right now. See docs › Configuration."),
        disableInput: false,
        retry: "button",
      };
    case "upstream-error":
    case "timeout":
      return { kind: "failed", text: failedText, disableInput: false, retry: "button" };
    case "upstream-rejected":
      return { kind: "other", text: "The model provider rejected this request.", disableInput: false, retry: null };
    case "request-too-large":
      return { kind: "other", text: "That's too long to send.", disableInput: false, retry: null };
    case "model-not-allowed":
    case "provider-not-found":
      return { kind: "model", text: "That model isn't available right now.", disableInput: false, retry: null };
    case "unauthorized":
      return { kind: "signed-out", text: signedOutText, disableInput: true, retry: null };
    case "internal-error":
      return { kind: "internal", text: `Something went wrong (ref ${problem?.traceId ?? "unknown"}).`, disableInput: false, retry: null };
    default:
      return { kind: "other", text: problem?.detail ?? message, disableInput: false, retry: null };
  }
}

/** What a failed start becomes: the panel's reason, one automatic retry, the used-up or off state, or the sign-in page. */
export type StartView =
  | { kind: "failed"; reason: string }
  | { kind: "rate-wait"; retryAfterMs: number }
  | { kind: "budget"; until: Date; dailyUsd?: number }
  | { kind: "chat-off" }
  | { kind: "signed-out" };

export const startUnavailableText = "the model service is unavailable right now. Try again in a moment.";
export const startFailedText = "that took too long or failed.";
export const startRateText = "too many requests at once — try again in a minute.";

const lowerFirst = (s: string) => s.charAt(0).toLowerCase() + s.slice(1);

/**
 * A session that didn't start, for the pick screen and the own-idea form. The reason follows "The designer couldn't open
 * {title} — ", so it starts in lower case. `attempt`: 0 = the first start, 1 = the automatic second one after a rate limit.
 */
export function startView(problem: Problem | null, opts: { gateway: boolean; attempt?: number; now?: Date }): StartView {
  const view = problemView(problem, "", opts);
  switch (view.kind) {
    case "budget":
      return { kind: "budget", until: view.until!, dailyUsd: view.dailyUsd };
    case "chat-off":
      return { kind: "chat-off" };
    case "rate-wait":
      return { kind: "rate-wait", retryAfterMs: view.retryAfterMs! };
    case "rate":
      return { kind: "failed", reason: startRateText };
    case "unavailable":
      // Standalone: the API's fix-it text (top up, switch provider, set the key again). Gateway: never the site's account.
      return { kind: "failed", reason: opts.gateway ? startUnavailableText : lowerFirst(view.text) };
    case "failed":
      return { kind: "failed", reason: startFailedText };
    case "signed-out":
      return { kind: "signed-out" };
    default:
      return { kind: "failed", reason: `something went wrong (ref ${problem?.traceId ?? "unknown"}).` };
  }
}

/** The muted note under an answer whose paint failed; `retry` = show Retry paint. */
export function paintNote(problem: Problem, gateway: boolean): { text: string; retry: boolean } {
  switch (problemSlug(problem)) {
    case "provider-unavailable":
      // Standalone adds the fix-it text: the person running it is the one who can fix it.
      return {
        text: `The picture couldn't be painted — the image service is unavailable right now.${!gateway && problem.detail ? ` ${problem.detail}` : ""}`,
        retry: false,
      };
    case "content-policy":
      return { text: "The image service declined that picture. Try describing it differently.", retry: false };
    default:
      return { text: "The picture couldn't be painted — that took too long or failed.", retry: true };
  }
}

/** "Answered by GPT-4o mini — Claude Haiku 5.5 is unavailable right now." */
export function fallbackNote(f: { requested: string; used: string }): string {
  return `Answered by ${f.used} — ${f.requested} is unavailable right now.`;
}

/** The picker's option: the label, and for an unavailable model "— unavailable until 3:00 PM" (or "— unavailable"). */
export function modelOptionLabel(m: ChatModel, time: (d: Date) => string = (d) => localTime(d)): string {
  if (m.available !== false) return modelLabel(m);
  return `${modelLabel(m)} — unavailable${m.unavailableUntilUtc ? ` until ${time(new Date(m.unavailableUntilUtc))}` : ""}`;
}

/** The model the session moves to: the default if it is available, else the first available one. */
export function fallbackModel(models: ChatModel[]): ChatModel | null {
  return models.find((m) => m.isDefault && m.available !== false) ?? models.find((m) => m.available !== false) ?? null;
}

/** The note when the session moves off an unavailable model (the versions stay, unlike TemporalGraph's chat). */
export function modelSwitchNote(unavailable: string, to: string): string {
  return `${unavailable} is unavailable right now — switched to ${to}.`;
}
