import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { tokensCss } from "./tokens-css.ts";
import { colors, fonts } from "./tokens.ts";

describe("tokens.css", () => {
  it("is in sync with tokens.ts (run `npm run tokens` if this fails)", () => {
    const committed = readFileSync(new URL("./tokens.css", import.meta.url), "utf8").replace(/\r\n/g, "\n");
    expect(committed).toBe(tokensCss());
  });
});

describe("approved values", () => {
  // The approved values; a change here is a design decision, not a refactor.
  const brief: Record<string, string> = {
    page: "#000000",
    panel: "#1F1F1F",
    fg: "#FFFFFF",
    "fg-muted": "rgba(255,255,255,0.6)",
    divider: "rgba(255,255,255,0.12)",
    outline: "rgba(255,255,255,0.2)",
    accent: "#60A5FA",
    "on-accent": "#000000",
  };

  it("every approved color is present with its exact value", () => {
    for (const [id, value] of Object.entries(brief)) expect(colors.find((c) => c.id === id)?.value, id).toBe(value);
  });

  it("any other color is marked as coming from the mockups", () => {
    for (const c of colors.filter((c) => !(c.id in brief))) expect(c.origin, c.id).toBe("mockups");
  });

  it("Open Sans is approved at 400/600/700/800; the mono is still a candidate", () => {
    expect(fonts.find((f) => f.id === "sans")).toMatchObject({ status: "approved", weights: [400, 600, 700, 800] });
    expect(fonts.find((f) => f.id === "mono")?.status).toBe("candidate");
  });
});
