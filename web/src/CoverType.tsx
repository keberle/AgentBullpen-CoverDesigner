import { useEffect, useState, type CSSProperties } from "react";
import { AUTHOR_REGION, browserMetrics, fit, forgetMeasurements, type Fitted } from "./coverFit.ts";
import type { Book, Typography } from "./graphql.ts";

// The cover's type (the typography mockup), drawn over the plate from a version's nine settings. The same numbers as
// the api's Pillow render (api/typography.py), so the download matches the screen. Sizes use container-query units
// of the cover (the surface is `container-type: size`): 1cqh = 1% of the cover height, 1cqw = 1% of its width.
//   padding 8% / 9% of the width (CSS `padding: 8% 9%`); title/author gap 4% of the padded box's height;
//   title S/M/L 0.04375/0.05625/0.075 H, line-height 1.05, tracking per face, in capitals when asked (never in a
//   script or blackletter face); a line that doesn't fit the padded box is set smaller (coverFit.ts);
//   author S/M 0.021875/0.028125 H, weight per face, uppercase with tracking 0.143em (as typed with none in a script
//   or blackletter face), line-height 1.2;
//   shadow 0 0.3125cqh 1.875cqh rgba(0,0,0,0.8); with `dark` text a light glow, 0 0 1.875cqh
//   rgba(255,255,255,0.55), and the scrims in white at the same alpha stops.

export const defaultTypography: Typography = {
  font: "opensans",
  titleSize: "M",
  titlePlace: "top",
  align: "center",
  titleCaps: false,
  authorPlace: "bottom",
  authorSize: "S",
  color: "light",
  scrim: "soft",
};

/** One face of the library: every one is under the SIL Open Font License, as in the api (api/typography.py FACES). */
type Face = {
  family: string; // the CSS family (the Fontsource name first)
  title: number; // the title's weight
  author: number; // 600 where the family has it, else its one weight
  tracking: string; // the title's letter-spacing
  /** false: the author line as typed, with no tracking (a script or blackletter face). Default: capitals, tracked out. */
  authorCaps?: boolean;
  /** false: the title is never in capitals either, whatever titleCaps says (the same faces). */
  caps?: boolean;
  /** Loads the face's stylesheet (and through it only the font files the page then uses). Open Sans is the app's own font. */
  load?: () => Promise<unknown>;
};
export const faces: Record<string, Face> = {
  oswald: { family: "'Oswald Variable', 'Oswald', sans-serif", title: 700, author: 600, tracking: "-0.014em", load: () => import("@fontsource-variable/oswald/wght.css") },
  anton: { family: "'Anton', sans-serif", title: 400, author: 400, tracking: "0", load: () => import("@fontsource/anton/400.css") },
  bebas: { family: "'Bebas Neue', sans-serif", title: 400, author: 400, tracking: "0", load: () => import("@fontsource/bebas-neue/400.css") },
  playfair: { family: "'Playfair Display Variable', 'Playfair Display', serif", title: 800, author: 600, tracking: "-0.014em", load: () => import("@fontsource-variable/playfair-display/wght.css") },
  cormorant: { family: "'Cormorant Garamond Variable', 'Cormorant Garamond', serif", title: 700, author: 600, tracking: "0", load: () => import("@fontsource-variable/cormorant-garamond/wght.css") },
  cinzel: { family: "'Cinzel Variable', 'Cinzel', serif", title: 800, author: 600, tracking: "0.028em", load: () => import("@fontsource-variable/cinzel/wght.css") },
  opensans: { family: "'Open Sans Variable', 'Open Sans', sans-serif", title: 800, author: 600, tracking: "-0.014em" },
  montserrat: { family: "'Montserrat Variable', 'Montserrat', sans-serif", title: 800, author: 600, tracking: "0", load: () => import("@fontsource-variable/montserrat/wght.css") },
  bangers: { family: "'Bangers', sans-serif", title: 400, author: 400, tracking: "0", load: () => import("@fontsource/bangers/400.css") },
  comicneue: { family: "'Comic Neue', sans-serif", title: 700, author: 700, tracking: "0", load: () => import("@fontsource/comic-neue/700.css") },
  creepster: { family: "'Creepster', serif", title: 400, author: 400, tracking: "0", load: () => import("@fontsource/creepster/400.css") },
  unifraktur: { family: "'UnifrakturMaguntia', serif", title: 400, author: 400, tracking: "0", authorCaps: false, caps: false, load: () => import("@fontsource/unifrakturmaguntia/400.css") },
  rye: { family: "'Rye', serif", title: 400, author: 400, tracking: "0", load: () => import("@fontsource/rye/400.css") },
  alfaslab: { family: "'Alfa Slab One', serif", title: 400, author: 400, tracking: "0", load: () => import("@fontsource/alfa-slab-one/400.css") },
  orbitron: { family: "'Orbitron Variable', 'Orbitron', sans-serif", title: 800, author: 600, tracking: "0", load: () => import("@fontsource-variable/orbitron/wght.css") },
  greatvibes: { family: "'Great Vibes', cursive", title: 400, author: 400, tracking: "0", authorCaps: false, caps: false, load: () => import("@fontsource/great-vibes/400.css") },
  courierprime: { family: "'Courier Prime', monospace", title: 700, author: 700, tracking: "0", load: () => import("@fontsource/courier-prime/700.css") },
};
const titleSizes: Record<string, number> = { S: 4.375, M: 5.625, L: 7.5 }; // cqh
const authorSizes: Record<string, number> = { S: 2.1875, M: 2.8125 }; // cqh
const colors: Record<string, string> = { light: "#FFFFFF", dark: "#111111", accent: "#F2C66D" };
// The mockup's gradients; `rgb` is black behind light/accent text, white behind dark text.
const scrims: Record<string, (rgb: string) => string> = {
  soft: (rgb) => `linear-gradient(rgba(${rgb},0.45), rgba(${rgb},0) 35%, rgba(${rgb},0) 70%, rgba(${rgb},0.5))`,
  strong: (rgb) => `linear-gradient(rgba(${rgb},0.7), rgba(${rgb},0) 40%, rgba(${rgb},0) 55%, rgba(${rgb},0.85))`,
};

/** The cover is 2:3, on screen and as a plate: its width as a share of its height. */
const ASPECT = 2 / 3;
const BOX_WIDTH = ASPECT * (1 - 2 * 0.09); // the padded box, as shares of the cover's height
const BOX_HEIGHT = 1 - 2 * 0.08 * ASPECT;
const GAP = 0.04 * BOX_HEIGHT;

/** One text element. Fitted (the browser can measure text): its lines as the fit broke them, at the size and tracking
 * the fit settled on. Not fitted: the text at the setting's size, wrapped by the browser. */
function Block({ text, fitted, size, tracking, style }: { text: string; fitted: Fitted | null; size: number; tracking: number; style: CSSProperties }) {
  if (!fitted) return <span style={{ ...style, fontSize: `${size * 100}cqh`, letterSpacing: `${tracking}em` }}>{text}</span>;
  return (
    <span
      style={{
        ...style,
        fontSize: `${size * fitted.scale * 100}cqh`,
        letterSpacing: `${fitted.tracking}em`,
        whiteSpace: "nowrap",
        paddingTop: `${fitted.overTop}em`,
        paddingBottom: `${fitted.overBottom}em`,
      }}
    >
      {fitted.lines.map((line, i) => (
        <span key={i} style={{ display: "block", position: "relative", left: line.shift ? `${line.shift}em` : undefined }}>
          {line.text}
          {line.wordEnd && i < fitted.lines.length - 1 ? " " : ""}
        </span>
      ))}
    </span>
  );
}

/** The scrim, then the title and author, as layers over the cover (its parent must be `relative` and `container-type: size`).
 * `scrim: false` leaves the scrim out (no plate yet); `faded` draws the type at 50% (painting). */
export function CoverType({ book, typography: t, scrim = true, faded = false }: { book: Book; typography: Typography; scrim?: boolean; faded?: boolean }) {
  const face = faces[t.font] ?? faces.opensans;
  // Only the face on this cover is fetched, when it is first shown. Once it is there the text is measured again:
  // until then it was measured, and drawn, in the fallback font.
  const [, setLoaded] = useState(0);
  useEffect(() => {
    let live = true;
    Promise.resolve(face.load?.())
      .then(() => Promise.all([face.title, face.author].map((weight) => document.fonts?.load(`${weight} 100px ${face.family}`))))
      .then(() => {
        forgetMeasurements();
        if (live) setLoaded((n) => n + 1);
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [face]);
  const dark = t.color === "dark";
  const authorCaps = face.authorCaps ?? true;
  const titleCaps = t.titleCaps && (face.caps ?? true);
  const align = (["left", "right"].includes(t.align) ? t.align : "center") as "left" | "center" | "right";
  const text: CSSProperties = {
    display: "block",
    fontFamily: face.family,
    textAlign: align,
    textShadow: dark ? "0 0 1.875cqh rgba(255,255,255,0.55)" : "0 0.3125cqh 1.875cqh rgba(0,0,0,0.8)",
    color: colors[t.color] ?? colors.light,
    opacity: faded ? 0.5 : undefined,
    overflowWrap: "normal",
  };
  const titleSize = (titleSizes[t.titleSize] ?? 5.625) / 100;
  const authorSize = (authorSizes[t.authorSize] ?? 2.1875) / 100;
  const titleTracking = parseFloat(face.tracking);
  const authorTracking = authorCaps ? 0.143 : 0;
  const titleText = titleCaps ? book.title.toUpperCase() : book.title;
  const authorText = authorCaps ? book.author.toUpperCase() : book.author;
  // The author line first: the title's region is the padded box less the author (on both sides of a title in the middle).
  const titleMetrics = browserMetrics(face.family, face.title);
  const authorMetrics = browserMetrics(face.family, face.author);
  const fittedAuthor = authorMetrics && fit(authorText, authorMetrics, authorSize, authorTracking, 1.2, BOX_WIDTH, AUTHOR_REGION * BOX_HEIGHT, align);
  const region = BOX_HEIGHT - ((fittedAuthor?.height ?? 0) + GAP) * (t.titlePlace === "middle" ? 2 : 1);
  const fittedTitle = titleMetrics && fittedAuthor && fit(titleText, titleMetrics, titleSize, titleTracking, 1.05, BOX_WIDTH, region, align);
  const title = (
    <Block key="title" text={titleText} fitted={fittedTitle} size={titleSize} tracking={titleTracking} style={{ ...text, fontWeight: face.title, lineHeight: 1.05 }} />
  );
  const author = (
    <Block key="author" text={authorText} fitted={fittedAuthor} size={authorSize} tracking={authorTracking} style={{ ...text, fontWeight: face.author, lineHeight: 1.2 }} />
  );
  // Same edge: the title first. A middle title is centred in the padded box on its own layer.
  const top = [t.titlePlace === "top" && title, t.authorPlace === "top" && author].filter(Boolean);
  const bottom = [t.titlePlace === "bottom" && title, t.authorPlace === "bottom" && author].filter(Boolean);
  const box: CSSProperties = { position: "absolute", inset: 0, boxSizing: "border-box", padding: "8cqw 9cqw", display: "flex", flexDirection: "column" };
  const gap = "calc(0.04 * (100cqh - 16cqw))";
  return (
    <>
      {scrim && scrims[t.scrim] && <div className="absolute inset-0" style={{ background: scrims[t.scrim](dark ? "255,255,255" : "0,0,0") }} />}
      <div style={{ ...box, justifyContent: "space-between" }}>
        <div style={{ display: "flex", flexDirection: "column", gap }}>{top}</div>
        <div style={{ display: "flex", flexDirection: "column", gap }}>{bottom}</div>
      </div>
      {t.titlePlace === "middle" && <div style={{ ...box, justifyContent: "center" }}>{title}</div>}
    </>
  );
}
