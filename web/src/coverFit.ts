// Fitting the cover's type: a line never leaves the padded box. The same algorithm, step for step, as the api's
// (api/typography.py, "Fitting"), each side measuring with its own renderer: here a canvas, there FreeType.
// The box's width is a maximum: the text wraps at word boundaries, and when the longest word is wider than the box, or
// the wrapped block taller than its region, the size steps down (never a horizontal squeeze, never letterspacing out),
// and the tracking, being in em, scales with it. Under the floor the tracking tightens to its minimum, and only then
// may a word break. A line is as wide as its ink: a glyph that overhangs its advance (an f, a script's swash) counts.

export const MIN_SCALE = 0.6; // of the setting's size
export const SCALE_STEP = 0.05;
export const MIN_TRACKING = -0.05; // em
export const TRACKING_STEP = 0.01;
export const AUTHOR_REGION = 0.25; // of the padded box's height
export const INK_GUARD = 0.05; // em: room kept over ink that reaches a block's edge, for a renderer's rounding of the baseline

/** Where a string draws from its origin on the baseline, in em: `left` is 0 or less (ink before the origin), `right`
 * the advance or more (ink past it). */
export type Ink = { advance: number; left: number; right: number; above: number; below: number };
/** One face at one weight, in em. `ink` is measured with no tracking; the fit adds it. */
export type Metrics = { ascent: number; descent: number; ink: (s: string) => Ink };

export type FittedLine = {
  text: string;
  /** How far the line moves from where its alignment puts it so that overhanging ink stays inside the box, in em. */
  shift: number;
  /** false when the line ends inside a broken word. */
  wordEnd: boolean;
};
export type Fitted = {
  /** The size as a share of the setting's: 1, or smaller in steps down to the floor. */
  scale: number;
  tracking: number; // em
  lines: FittedLine[];
  /** Ink above the first line box and under the last, in em: the block grows by it. */
  overTop: number;
  overBottom: number;
  /** The block's height, in the unit `size` was given in. */
  height: number;
  fitsWidth: boolean;
  /** A word had to be broken: the last resort. */
  broken: boolean;
};

type Align = "left" | "center" | "right";

function block(text: string, m: Metrics, px: number, tracking: number, lineHeight: number, width: number, align: Align, breakWords: boolean, scale: number): Fitted {
  const w = width / px; // the box, in em
  const ink = (s: string): Ink => {
    const i = m.ink(s);
    const advance = i.advance + tracking * s.length;
    return { advance, left: Math.min(0, i.left), right: Math.max(advance, i.right + tracking * (s.length - 1)), above: i.above, below: i.below };
  };
  const extent = (s: string) => ink(s).right - ink(s).left;
  const lines: { text: string; wordEnd: boolean }[] = [];
  let line = "";
  let broken = false;
  for (const word of text.split(/\s+/).filter(Boolean)) {
    const trial = line ? `${line} ${word}` : word;
    if (line && extent(trial) > w) {
      lines.push({ text: line, wordEnd: true });
      line = word;
    } else line = trial;
    // The last resort: a word wider than the box breaks where it stops fitting.
    while (breakWords && line.length > 1 && extent(line) > w) {
      let n = line.length - 1;
      while (n > 1 && extent(line.slice(0, n)) > w) n--;
      lines.push({ text: line.slice(0, n), wordEnd: false });
      line = line.slice(n);
      broken = true;
    }
  }
  if (line) lines.push({ text: line, wordEnd: true });
  // The browser centres the font's ascent+descent in the line box; the baseline follows.
  const baseline = (lineHeight - (m.ascent + m.descent)) / 2 + m.ascent;
  const overTop = lines.length ? Math.max(0, ink(lines[0].text).above + INK_GUARD - baseline) : 0;
  const overBottom = lines.length ? Math.max(0, ink(lines[lines.length - 1].text).below + INK_GUARD - (lineHeight - baseline)) : 0;
  return {
    scale,
    tracking,
    lines: lines.map((l) => {
      // Aligned by its advance, as the browser does; then moved just far enough that overhanging ink stays inside.
      const i = ink(l.text);
      const x = align === "left" ? 0 : align === "right" ? w - i.advance : (w - i.advance) / 2;
      const shift = x + i.left < 0 ? -(x + i.left) : Math.min(0, w - i.right - x);
      return { ...l, shift };
    }),
    overTop,
    overBottom,
    height: (overTop + lineHeight * lines.length + overBottom) * px,
    fitsWidth: lines.every((l) => extent(l.text) <= w),
    broken,
  };
}

/** The block for `text` that fits `width` and `maxHeight` (in the unit of `size`): at the setting's size when it fits
 * there, else smaller a step at a time to the floor, then with tighter tracking, then (still too wide) with the long
 * word broken. */
export function fit(text: string, m: Metrics, size: number, tracking: number, lineHeight: number, width: number, maxHeight: number, align: Align = "center"): Fitted {
  const at = (scale: number, tr: number, breakWords = false) => block(text, m, size * scale, tr, lineHeight, width, align, breakWords, scale);
  const fits = (b: Fitted) => b.fitsWidth && b.height <= maxHeight;
  const steps = Math.round((1 - MIN_SCALE) / SCALE_STEP);
  let b = at(1, tracking);
  for (let k = 1; k <= steps; k++) {
    if (fits(b)) return b;
    b = at(1 - SCALE_STEP * k, tracking);
  }
  let tr = tracking;
  while (!fits(b) && tr > MIN_TRACKING + 1e-9) {
    tr = Math.max(MIN_TRACKING, Math.round((tr - TRACKING_STEP) * 1e6) / 1e6);
    b = at(MIN_SCALE, tr);
  }
  return b.fitsWidth ? b : at(MIN_SCALE, tr, true);
}

// --- Measuring with the browser ---------------------------------------------------------------------------------------

const REFERENCE = 100; // px: text metrics scale with the size, so one measurement serves every size
let context: CanvasRenderingContext2D | null | undefined;
const measured = new Map<string, Ink>();

/** Forget what was measured: a web font has loaded, and the same string now draws in another face. */
export function forgetMeasurements(): void {
  measured.clear();
}

/** The browser's metrics for `weight` of `family` (a CSS family list), or null where text can't be measured (no canvas). */
export function browserMetrics(family: string, weight: number): Metrics | null {
  if (typeof TextMetrics === "undefined" || !("fontBoundingBoxAscent" in TextMetrics.prototype)) return null;
  if (context === undefined) context = document.createElement("canvas").getContext("2d");
  const ctx = context;
  if (!ctx) return null;
  const font = `${weight} ${REFERENCE}px ${family}`;
  const measure = (s: string) => {
    ctx.font = font;
    ctx.textAlign = "left";
    ctx.textBaseline = "alphabetic";
    return ctx.measureText(s);
  };
  const box = measure("x");
  return {
    ascent: box.fontBoundingBoxAscent / REFERENCE,
    descent: box.fontBoundingBoxDescent / REFERENCE,
    ink: (s) => {
      const key = `${font}|${s}`;
      let ink = measured.get(key);
      if (!ink) {
        const t = measure(s);
        ink = {
          advance: t.width / REFERENCE,
          left: -t.actualBoundingBoxLeft / REFERENCE,
          right: t.actualBoundingBoxRight / REFERENCE,
          above: t.actualBoundingBoxAscent / REFERENCE,
          below: t.actualBoundingBoxDescent / REFERENCE,
        };
        measured.set(key, ink);
      }
      return ink;
    },
  };
}
