import { describe, expect, it } from "vitest";
import { MIN_SCALE, MIN_TRACKING, fit, type Metrics } from "./coverFit.ts";

// The fit, with a face whose every character is half an em wide: the same steps as the api's (api/typography.py), so
// the same cases as its tests. Widths and heights are in the unit of the size (here 100 = a 100px title).
const mono = (overhang: Record<string, number> = {}, rise = 0.7): Metrics => ({
  ascent: 0.8,
  descent: 0.2,
  ink: (s) => {
    const advance = 0.5 * s.length;
    const last = overhang[s.at(-1) ?? ""] ?? 0;
    const first = overhang[`^${s[0]}`] ?? 0;
    return { advance, left: -first, right: advance + last, above: rise, below: 0.1 };
  },
});
const word = "Baskervilles"; // 12 characters: 6 em

describe("Fitting a line to the padded box", () => {
  it("leaves a text that fits at the setting's size, wrapped at word boundaries", () => {
    const f = fit("The Hound of the Baskervilles", mono(), 100, 0, 1.05, 800, 1000);
    expect([f.scale, f.tracking, f.broken, f.fitsWidth]).toEqual([1, 0, false, true]);
    expect(f.lines.map((l) => l.text)).toEqual(["The Hound of the", "Baskervilles"]);
    expect(f.height).toBeCloseTo(2 * 105);
  });

  it("steps the size down until the longest word fits", () => {
    const f = fit(word, mono(), 100, 0, 1.05, 480, 1000); // 600 wide at full size: 0.80 fits exactly
    expect(f.scale).toBeCloseTo(0.8);
    expect([f.tracking, f.broken, f.fitsWidth]).toEqual([0, false, true]);
  });

  it("steps the size down when the wrapped block is too tall for its region", () => {
    const full = fit("The Hound of the Baskervilles", mono(), 100, 0, 1.05, 800, 1000);
    const short = fit("The Hound of the Baskervilles", mono(), 100, 0, 1.05, 800, full.height - 1);
    expect(short.scale).toBeLessThan(1);
    expect(short.height).toBeLessThanOrEqual(full.height - 1);
  });

  it("under the floor tightens the tracking, and only then breaks the word", () => {
    const tighter = fit(word, mono(), 100, 0, 1.05, 340, 1000); // 360 at the floor; 12 × 0.6 px per 0.01 em
    expect(tighter.scale).toBeCloseTo(MIN_SCALE);
    expect(tighter.tracking).toBeLessThan(0);
    expect(tighter.tracking).toBeGreaterThanOrEqual(MIN_TRACKING);
    expect(tighter.broken).toBe(false);
    const broken = fit(word, mono(), 100, 0, 1.05, 200, 1000);
    expect([broken.scale, broken.tracking, broken.broken, broken.fitsWidth]).toEqual([MIN_SCALE, MIN_TRACKING, true, true]);
    expect(broken.lines.map((l) => l.text).join("")).toBe(word);
    expect(broken.lines.slice(0, -1).every((l) => !l.wordEnd)).toBe(true);
  });

  it("counts ink that overhangs the advance, and moves such a line back inside the box", () => {
    const f = { f: 0.2 }; // an f whose hook runs 0.2 em past its advance
    expect(fit("of", mono(f), 100, 0, 1.05, 100, 1000).scale).toBeLessThan(1); // 1 em of advance, 1.2 em of ink
    const right = fit("of", mono(f), 100, 0, 1.05, 200, 1000, "right");
    expect(right.lines[0].shift).toBeCloseTo(-0.2);
    expect(fit("of", mono(f), 100, 0, 1.05, 200, 1000, "left").lines[0].shift).toBe(0);
    expect(fit("of", mono(f), 100, 0, 1.05, 200, 1000, "center").lines[0].shift).toBe(0); // room on both sides
    const left = fit("of", mono({ "^o": 0.1 }), 100, 0, 1.05, 200, 1000, "left");
    expect(left.lines[0].shift).toBeCloseTo(0.1);
  });

  it("grows the block by ink above its first line box", () => {
    // Baseline at (1.05 − 1) / 2 + 0.8 = 0.825 em; a swash rising 1 em is 0.175 em over, and 0.05 em is kept clear of it.
    const f = fit("Hound", mono({}, 1), 100, 0, 1.05, 800, 1000);
    expect(f.overTop).toBeCloseTo(0.225);
    expect(f.height).toBeCloseTo(105 + 22.5);
    expect(fit("Hound", mono(), 100, 0, 1.05, 800, 1000).overTop).toBe(0);
  });

  it("never letterspaces out and never goes under the floor", () => {
    for (const width of [100, 250, 400, 700, 2000]) {
      const f = fit("The Hound of the Baskervilles", mono(), 100, 0.028, 1.05, width, 400);
      expect(f.scale).toBeGreaterThanOrEqual(MIN_SCALE - 1e-9);
      expect(f.scale).toBeLessThanOrEqual(1);
      expect(f.tracking).toBeLessThanOrEqual(0.028);
    }
  });
});
