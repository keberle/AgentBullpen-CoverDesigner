// A turn's cost and today's allowance in dollars (AgentBullpen-TemporalGraph's chat tests for the same helpers, adapted:
// "the designer" for "chat", and the cost at the end of a turn's Details line).
import { renderToStaticMarkup } from "react-dom/server";
import { createElement } from "react";
import { describe, expect, it } from "vitest";
import { AllowanceFooter, AllowanceNotice } from "./AllowanceFooter.tsx";
import {
  allowanceView,
  costText,
  designerOffLine,
  designerOffNotice,
  dollars,
  holdsOff,
  leftText,
  onAttention,
  refusedBudget,
  tokensAndCost,
  usedUpNotice,
} from "./allowance.ts";
import type { Budget } from "./graphql.ts";

describe("Tokens and cost of a turn", () => {
  it("tokens with thousands separators, then the cost to 4 decimals", () => {
    expect(tokensAndCost(1980, { costUsd: 0.0412 })).toBe("1,980 tokens · $0.0412");
    expect(tokensAndCost(1915, { costUsd: 0.000412 })).toBe("1,915 tokens · $0.0004");
    expect(tokensAndCost(123456, { costUsd: 1.23456 })).toBe("123,456 tokens · $1.2346");
  });

  it("a cost under a hundredth of a cent is “<$0.0001”; nothing at all is $0.0000", () => {
    expect(costText(0.0000027)).toBe("<$0.0001");
    expect(costText(0.00009999)).toBe("<$0.0001");
    expect(costText(0.0001)).toBe("$0.0001");
    expect(costText(0)).toBe("$0.0000");
    expect(tokensAndCost(15, { costUsd: 0.000003 })).toBe("15 tokens · <$0.0001");
  });

  it("no cost from the API: tokens only, never an estimate", () => {
    expect(tokensAndCost(3412, {})).toBe("3,412 tokens");
    expect(tokensAndCost(3412, { costUsd: null })).toBe("3,412 tokens");
    expect(tokensAndCost(3412, null)).toBe("3,412 tokens");
    expect(tokensAndCost(1, { costUsd: null })).toBe("1 token");
  });
});

describe("Today's allowance under the input", () => {
  const now = new Date("2026-10-10T15:00:00Z");
  const time = () => "6:00 PM";
  const at = (remainingUsd: number | null, extra: Partial<Budget> = {}): Budget =>
    ({ level: "standard", dailyUsd: 1, remainingUsd, unlimited: false, resetsAtUtc: "2026-10-11T00:00:00Z", ...extra });
  const view = (b: Budget | null) => allowanceView(b, { now, time });
  const off: Budget = { level: "off", dailyUsd: null, remainingUsd: 0, unlimited: false, resetsAtUtc: null };

  it("normal: what is left of the day's allowance, when it resets, the meter in the accent", () => {
    expect(view(at(0.98))).toEqual({ state: "normal", left: "$0.98 of $1.00 left today", reset: "Resets 6:00 PM", percent: 98, notice: null, disableInput: false, until: null });
    expect(view(at(1))).toMatchObject({ state: "normal", left: "$1.00 of $1.00 left today", percent: 100 });
    expect(view(at(0.1))).toMatchObject({ state: "normal", percent: 10 }); // 10 % is not yet low
    const html = renderToStaticMarkup(createElement(AllowanceFooter, { view: view(at(0.98))! }));
    expect(html).toContain("$0.98 of $1.00 left today");
    expect(html).toContain("Resets 6:00 PM");
    expect(html).toMatch(/role="meter" aria-label="Today&#x27;s allowance left" aria-valuemin="0" aria-valuemax="100" aria-valuenow="98"/);
    expect(html).toMatch(/bg-accent" style="width:98%"/);
    expect(html).not.toContain("bg-warning");
  });

  it("running low: under 10 % left, the warning colour", () => {
    expect(view(at(0.06))).toMatchObject({ state: "low", left: "$0.06 of $1.00 left today", reset: "Resets 6:00 PM", percent: 6, notice: null, disableInput: false });
    expect(view(at(0.0999))).toMatchObject({ state: "low", left: "$0.09 of $1.00 left today" });
    expect(view(at(0.019, { level: "generous", dailyUsd: 2 }))).toMatchObject({ state: "low", left: "$0.01 of $2.00 left today", percent: 1 });
    const html = renderToStaticMarkup(createElement(AllowanceFooter, { view: view(at(0.06))! }));
    expect(html).toMatch(/bg-warning" style="width:6%"/);
    expect(html).not.toContain("bg-accent");
  });

  it("what is left is never rounded up, and a remainder under a cent is not shown as nothing", () => {
    expect(leftText(0.9994)).toBe("$0.99");
    expect(leftText(0.29)).toBe("$0.29"); // not $0.28: 0.29 * 100 is 28.999… in binary
    expect(leftText(0.004)).toBe("<$0.01");
    expect(leftText(0)).toBe("$0.00");
    expect(dollars(0.5)).toBe("$0.50");
    expect(view(at(0.004))).toMatchObject({ state: "low", left: "<$0.01 of $1.00 left today", percent: 1, disableInput: false });
  });

  it("used up: the notice with the day's allowance and the local reset time; everything off until then", () => {
    const v = view(at(0))!;
    expect(v).toMatchObject({ state: "used-up", left: "$0.00 of $1.00 left today", reset: "Resets 6:00 PM", percent: 0, disableInput: true });
    expect(v.notice).toBe("You've used today's $1.00. The designer comes back at 6:00 PM.");
    expect(v.until!.toISOString()).toBe("2026-10-11T00:00:00.000Z");
    expect(view(at(0, { level: "custom", dailyUsd: 0.01 }))!.notice).toBe("You've used today's $0.01. The designer comes back at 6:00 PM.");
    expect(usedUpNotice(null, new Date(), time)).toBe("You've used today's allowance. The designer comes back at 6:00 PM.");
    // Without the site's reset time: the next UTC midnight.
    expect(view(at(0, { resetsAtUtc: null }))!.until!.toISOString()).toBe("2026-10-11T00:00:00.000Z");
    const html = renderToStaticMarkup(createElement(AllowanceFooter, { view: v })) + renderToStaticMarkup(createElement(AllowanceNotice, { text: v.notice! }));
    expect(html).toContain("$0.00 of $1.00 left today");
    expect(html).toMatch(/aria-valuenow="0"/);
    expect(html).toMatch(/role="status"[^>]*>You&#x27;ve used today&#x27;s \$1\.00\. The designer comes back at 6:00 PM\./);
  });

  it("used up holds everything off until its reset time, then lets go by itself; off never does", () => {
    const v = view(at(0));
    expect(holdsOff(v, now)).toBe(true);
    expect(holdsOff(v, new Date("2026-10-10T23:59:59Z"))).toBe(true);
    expect(holdsOff(v, new Date("2026-10-11T00:00:01Z"))).toBe(false);
    expect(holdsOff(view(off), new Date("2030-01-01T00:00:00Z"))).toBe(true);
    expect(holdsOff(view(at(0.5)), now)).toBe(false);
    expect(holdsOff(null, now)).toBe(false);
  });

  it("off: its own notice and footer, no reset time, input off, nothing to wait for", () => {
    expect(view(off)).toEqual({
      state: "off",
      left: designerOffLine,
      reset: null,
      percent: 0,
      notice: "The designer isn't turned on for your account yet.",
      disableInput: true,
      until: null,
    });
    expect(designerOffLine).toBe("The designer is off for your account");
    expect(designerOffNotice).toBe("The designer isn't turned on for your account yet.");
    const html = renderToStaticMarkup(createElement(AllowanceFooter, { view: view(off)! }));
    expect(html).toContain("The designer is off for your account");
    expect(html).not.toContain("Resets");
  });

  it("no limit, standalone, or no headers: no footer", () => {
    expect(view({ level: "none", dailyUsd: null, remainingUsd: null, unlimited: true, resetsAtUtc: null })).toBeNull();
    expect(view({ level: null, dailyUsd: null, remainingUsd: null, unlimited: true })).toBeNull(); // standalone
    expect(view(null)).toBeNull();
    expect(allowanceView(undefined)).toBeNull();
  });

  it("the local time of the reset comes from the browser's clock by default", () => {
    expect(allowanceView(at(0.5), { now })!.reset).toMatch(/^Resets .*\d/);
  });

  it("a start or a turn the site refused: used up until the problem's time, or off", () => {
    const was = at(0.4, { level: "generous", dailyUsd: 2 });
    expect(refusedBudget("budget", was, new Date("2026-10-11T00:00:00Z"))).toEqual({ level: "generous", dailyUsd: 2, remainingUsd: 0, unlimited: false, resetsAtUtc: "2026-10-11T00:00:00.000Z" });
    expect(refusedBudget("budget", null, new Date("2026-10-11T00:00:00Z"), 1)).toMatchObject({ dailyUsd: 1, remainingUsd: 0 });
    expect(view(refusedBudget("budget", null, new Date("2026-10-11T00:00:00Z")))!.notice).toBe("You've used today's allowance. The designer comes back at 6:00 PM.");
    expect(view(refusedBudget("chat-off", was))).toMatchObject({ state: "off" });
  });
});

describe("The allowance is read again when the tab gets attention", () => {
  it("on window focus, and when the page is shown again; not when it is hidden; not after stop", () => {
    const win = new EventTarget();
    const doc = Object.assign(new EventTarget(), { visibilityState: "visible" });
    let reads = 0;
    let clock = 0;
    const stop = onAttention(() => reads++, win, doc, () => clock);
    win.dispatchEvent(new Event("focus"));
    expect(reads).toBe(1);
    clock += 5000;
    doc.visibilityState = "hidden";
    doc.dispatchEvent(new Event("visibilitychange"));
    expect(reads).toBe(1);
    doc.visibilityState = "visible";
    doc.dispatchEvent(new Event("visibilitychange"));
    expect(reads).toBe(2);
    clock += 5000;
    stop();
    win.dispatchEvent(new Event("focus"));
    doc.dispatchEvent(new Event("visibilitychange"));
    expect(reads).toBe(2);
  });

  it("focus and visibility arriving together are one read", () => {
    const win = new EventTarget();
    const doc = Object.assign(new EventTarget(), { visibilityState: "visible" });
    let reads = 0;
    let clock = 0;
    onAttention(() => reads++, win, doc, () => clock);
    win.dispatchEvent(new Event("focus"));
    doc.dispatchEvent(new Event("visibilitychange"));
    expect(reads).toBe(1);
    clock += 1000;
    win.dispatchEvent(new Event("focus"));
    expect(reads).toBe(2);
  });
});
