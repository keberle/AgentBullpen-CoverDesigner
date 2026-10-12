// A turn's cost and today's allowance in dollars, behind the AgentBullpen.AI site: the texts and the footer's states.
// Copied from AgentBullpen-TemporalGraph's web/src/chat.ts (costText, dollars, leftText, allowanceView, onAttention),
// with "the designer" where its texts say "chat". Standalone there is no allowance: allowanceView gives null.
import type { Budget, Usage } from "./graphql.ts";

const thousands = (n: number) => n.toLocaleString("en-US");

/** "3:00 PM" today; "Sat 3:00 PM" on another day. */
export function localTime(d: Date, now: Date = new Date()): string {
  const sameDay = d.toDateString() === now.toDateString();
  return d.toLocaleString([], sameDay ? { hour: "numeric", minute: "2-digit" } : { weekday: "short", hour: "numeric", minute: "2-digit" });
}

/** The next UTC midnight: when a daily allowance starts again if the API doesn't say. */
export function nextUtcMidnight(now: Date = new Date()): Date {
  return new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + 1));
}

/** A turn's cost: 4 decimals ("$0.0412"); "<$0.0001" for anything smaller that isn't nothing. */
export function costText(usd: number): string {
  return usd > 0 && usd < 0.0001 ? "<$0.0001" : `$${usd.toFixed(4)}`;
}

/**
 * The end of a turn's Details line: "1,980 tokens · $0.0412". Tokens always; the cost only when the API has one (gateway:
 * the site's, over the turn's chat, concept and image calls; standalone: the provider's own). It is never estimated here.
 */
export function tokensAndCost(tokens: number, usage: Pick<Usage, "costUsd"> | null | undefined): string {
  const text = `${thousands(tokens)} token${tokens === 1 ? "" : "s"}`;
  return usage?.costUsd == null ? text : `${text} · ${costText(usage.costUsd)}`;
}

/** An allowance amount: "$1.00". */
export function dollars(usd: number): string {
  return `$${usd.toFixed(2)}`;
}

/** What is left, in whole cents and never rounded up: "$0.98"; "<$0.01" for a remainder under a cent; "$0.00" for none. */
export function leftText(usd: number): string {
  if (usd <= 0) return dollars(0);
  return usd < 0.01 ? "<$0.01" : dollars(Math.floor(usd * 100 + 1e-9) / 100);
}

/** Under this share of the day's allowance the meter turns to the warning colour. */
export const lowShare = 0.1;

export const designerOffNotice = "The designer isn't turned on for your account yet.";
export const designerOffLine = "The designer is off for your account";

/** "You've used today's $1.00. The designer comes back at 6:00 PM." */
export function usedUpNotice(dailyUsd: number | null, until: Date, time: (d: Date) => string = localTime): string {
  return `You've used today's ${dailyUsd == null ? "allowance" : dollars(dailyUsd)}. The designer comes back at ${time(until)}.`;
}

export type AllowanceView = {
  /** normal, low (under 10 % left), used-up (nothing left until the reset), off (the designer isn't turned on for the account). */
  state: "normal" | "low" | "used-up" | "off";
  /** The footer's left side: "$0.98 of $1.00 left today", or "The designer is off for your account". */
  left: string;
  /** Its right side: "Resets 6:00 PM"; null when nothing resets (off). */
  reset: string | null;
  /** The meter, 0–100: the share of today's allowance left. */
  percent: number;
  /** The notice (used-up, off); null otherwise. */
  notice: string | null;
  /** The input, Send, the chip and the cards are off. */
  disableInput: boolean;
  /** used-up: when the allowance starts again (everything comes back by itself then). */
  until: Date | null;
};

/**
 * The allowance footer and notice, behind the site only. Null = nothing to show: standalone, no headers, or no limit (the
 * level "none"). The site's levels: off, light, standard, generous, custom, none.
 */
export function allowanceView(b: Budget | null | undefined, opts: { now?: Date; time?: (d: Date) => string } = {}): AllowanceView | null {
  if (!b || b.unlimited || b.level === "none") return null;
  if (b.level === "off") return { state: "off", left: designerOffLine, reset: null, percent: 0, notice: designerOffNotice, disableInput: true, until: null };
  if (b.remainingUsd == null) return null;
  const now = opts.now ?? new Date();
  const time = opts.time ?? ((d: Date) => localTime(d, now));
  const until = b.resetsAtUtc ? new Date(b.resetsAtUtc) : nextUtcMidnight(now);
  const reset = `Resets ${time(until)}`;
  const daily = b.dailyUsd;
  if (b.remainingUsd <= 0) {
    return {
      state: "used-up",
      left: daily == null ? `${dollars(0)} left today` : `${dollars(0)} of ${dollars(daily)} left today`,
      reset,
      percent: 0,
      notice: usedUpNotice(daily, until, time),
      disableInput: true,
      until,
    };
  }
  if (daily == null || daily <= 0) return null;
  const share = Math.min(1, b.remainingUsd / daily);
  return {
    state: share < lowShare ? "low" : "normal",
    left: `${leftText(b.remainingUsd)} of ${dollars(daily)} left today`,
    reset,
    // Something left never reads as an empty meter.
    percent: Math.max(1, Math.round(share * 100)),
    notice: null,
    disableInput: false,
    until: null,
  };
}

/**
 * The same view once its reset time has passed: a used-up allowance whose time is behind `now` no longer holds anything
 * off (the balance is read again then). Off never passes.
 */
export function holdsOff(view: AllowanceView | null, now: Date): boolean {
  if (!view?.disableInput) return false;
  return view.state === "off" || (view.until !== null && view.until.getTime() > now.getTime());
}

/** The allowance after a start or a turn the site refused: used up until the problem's reset time, or off. */
export function refusedBudget(kind: "budget" | "chat-off", was: Budget | null, until?: Date, dailyUsd?: number): Budget {
  if (kind === "chat-off") return { level: "off", dailyUsd: null, remainingUsd: 0, unlimited: false, resetsAtUtc: null };
  return { level: was?.level ?? null, dailyUsd: dailyUsd ?? was?.dailyUsd ?? null, remainingUsd: 0, unlimited: false, resetsAtUtc: until?.toISOString() ?? null };
}

/**
 * Calls back when the tab gets the author's attention again (the window focused, or the page shown after being hidden);
 * returns the function that stops listening. The allowance is re-read then, since it may have been changed on the site
 * meanwhile. At most once a second: focus and visibility often arrive together.
 */
export function onAttention(
  callback: () => void,
  win: Pick<EventTarget, "addEventListener" | "removeEventListener"> = window,
  doc: Pick<EventTarget, "addEventListener" | "removeEventListener"> & { visibilityState?: string } = document,
  clock: () => number = Date.now,
): () => void {
  let last = -Infinity;
  const attend = () => {
    if (clock() - last < 1000) return;
    last = clock();
    callback();
  };
  const shown = () => {
    if (doc.visibilityState !== "hidden") attend();
  };
  win.addEventListener("focus", attend);
  doc.addEventListener("visibilitychange", shown);
  return () => {
    win.removeEventListener("focus", attend);
    doc.removeEventListener("visibilitychange", shown);
  };
}
