# A measured legibility check for the cover's type: the contrast of each line's ink against the pixels actually under
# it (the plate with its scrim), and a fix that raises the scrim or switches the text colour when a line reads badly.
# The same answer every time, and no model is asked.
#
# The contrast measure is APCA (the Accessible Perceptual Contrast Algorithm): the constants and the maths are those of
# the published reference, apca-w3 0.1.9 (github.com/Myndex/apca-w3, "0.0.98G-4g" constants). They are fitted values,
# not tunable ones: a nearby number is a different algorithm.
#
# The verdict follows WCAG failure technique F83 (a background image that gives the text too little contrast), made
# robust to outliers: |Lc| at every ink pixel, judged on the median (is the line wrong?) and on the 5th percentile plus
# the share under the minimum (does the line have a bad patch?). The text's shadow is not ink: it is a mitigation, and
# scoring it would bless a line that only reads because of its own shadow.
import io
import math
from dataclasses import dataclass, replace

from PIL import Image, ImageDraw

import typography
from typography import Typography

# --- APCA-W3 0.1.9 ------------------------------------------------------------------------------------------------------
MAIN_TRC = 2.4  # a plain power curve, not the piecewise sRGB one
S_R, S_G, S_B = 0.2126729, 0.7151522, 0.0721750
NORM_BG, NORM_TXT = 0.56, 0.57  # dark text on a lighter background
REV_TXT, REV_BG = 0.62, 0.65  # light text on a darker background
BLK_THRS, BLK_CLMP = 0.022, 1.414  # the soft clamp near black
SCALE = 1.14
LO_OFFSET = 0.027
DELTA_Y_MIN = 0.0005
LO_CLIP = 0.1

_CHANNEL = [(v / 255.0) ** MAIN_TRC for v in range(256)]


def _y(rgb) -> float:
    """APCA's screen luminance of an sRGB colour, soft-clamped near black."""
    y = S_R * _CHANNEL[rgb[0]] + S_G * _CHANNEL[rgb[1]] + S_B * _CHANNEL[rgb[2]]
    return y if y > BLK_THRS else y + (BLK_THRS - y) ** BLK_CLMP


def _lc(y_text: float, y_bg: float) -> float:
    if abs(y_bg - y_text) < DELTA_Y_MIN:
        return 0.0
    if y_bg > y_text:  # dark text on light
        sapc = (y_bg ** NORM_BG - y_text ** NORM_TXT) * SCALE
        out = 0.0 if sapc < LO_CLIP else sapc - LO_OFFSET
    else:  # light text on dark: negative by construction
        sapc = (y_bg ** REV_BG - y_text ** REV_TXT) * SCALE
        out = 0.0 if sapc > -LO_CLIP else sapc + LO_OFFSET
    return out * 100.0


def apca_lc(text_rgb, bg_rgb) -> float:
    """The signed APCA lightness contrast of text on a background: positive for dark text on light, negative for light
    text on dark, 0 for a pair too close to tell apart."""
    return _lc(_y(text_rgb), _y(bg_rgb))


def magnitude(text_rgb, bg_rgb) -> float:
    """|Lc|: the number a threshold is compared against (light on dark is negative)."""
    return abs(apca_lc(text_rgb, bg_rgb))


# --- The measurement ----------------------------------------------------------------------------------------------------
MINIMUM_LARGE_TEXT = 45.0  # APCA's documented minimum for large, heavy text: a cover's title and author line
INK_ALPHA = 128  # half coverage: the glyph's body and the inner half of its soft edge
MAX_SAMPLES = 20_000
MIN_SAMPLES = 16  # fewer ink pixels than this is a speck, not a line: nothing to judge
MAX_FRACTION_BELOW = 0.05
LINES = ("title", "author")


@dataclass(frozen=True)
class Finding:
    label: str  # "title" | "author"
    lc_p5: float  # the 5th percentile of |Lc| over the ink: the worst case with the noise taken off
    lc_median: float
    fraction_below: float  # the share of the ink under the minimum
    edge_density: float  # how busy the art under the ink is, 0..1. Reported only: there is no published threshold
    verdict: str  # "pass" | "warn" | "fail"
    samples: int = 0  # ink pixels measured; 0 = nothing was drawn


def _nearest_rank(ordered: list[float], p: float) -> float:
    return ordered[min(len(ordered) - 1, max(0, math.ceil(p * len(ordered)) - 1))]


def _measure(background: Image.Image, mask: Image.Image, rgb: tuple[int, int, int], label: str) -> Finding:
    """One line: `mask` is its glyphs alone (L, the background's size), `background` what they sit on (RGB)."""
    box = mask.point(lambda v: 255 if v >= INK_ALPHA else 0).getbbox()
    if box is None:
        return Finding(label, 0.0, 0.0, 0.0, 0.0, "pass")
    left, top, right, bottom = box
    area = (right - left) * (bottom - top)
    stride = 1 if area <= MAX_SAMPLES else max(1, math.ceil(math.sqrt(area / MAX_SAMPLES)))
    ink, art = mask.load(), background.load()
    w, h = background.size
    y_text = _y(rgb)
    contrasts: dict[tuple[int, int, int], float] = {}

    def luma(x: int, y: int) -> float:
        r, g, b = art[min(w - 1, max(0, x)), min(h - 1, max(0, y))]
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    samples, edges = [], 0.0
    for y in range(top, bottom, stride):
        for x in range(left, right, stride):
            if ink[x, y] < INK_ALPHA:
                continue
            under = art[x, y]
            if under not in contrasts:
                contrasts[under] = abs(_lc(y_text, _y(under)))
            samples.append(contrasts[under])
            gx = (luma(x + 1, y - 1) + 2 * luma(x + 1, y) + luma(x + 1, y + 1)) - (luma(x - 1, y - 1) + 2 * luma(x - 1, y) + luma(x - 1, y + 1))
            gy = (luma(x - 1, y + 1) + 2 * luma(x, y + 1) + luma(x + 1, y + 1)) - (luma(x - 1, y - 1) + 2 * luma(x, y - 1) + luma(x + 1, y - 1))
            edges += math.hypot(gx, gy)
    if len(samples) < MIN_SAMPLES:
        return Finding(label, 0.0, 0.0, 0.0, 0.0, "pass")
    samples.sort()
    p5, median = _nearest_rank(samples, 0.05), _nearest_rank(samples, 0.50)
    below = sum(1 for s in samples if s < MINIMUM_LARGE_TEXT) / len(samples)
    verdict = "fail" if median < MINIMUM_LARGE_TEXT else "warn" if p5 < MINIMUM_LARGE_TEXT or below > MAX_FRACTION_BELOW else "pass"
    return Finding(label, p5, median, below, min(1.0, edges / len(samples) / (255.0 * 4.0)), verdict, len(samples))


def _plate(plate) -> Image.Image:
    return plate if isinstance(plate, Image.Image) else Image.open(io.BytesIO(plate))


def measure(plate, title: str, author: str, t: Typography, line: str) -> Finding:
    """One line ("title" or "author") of the cover as the download draws it, against the plate with `t`'s scrim."""
    return check(plate, title, author, t)[LINES.index(line)]


def check(plate, title: str, author: str, t: Typography) -> tuple[Finding, Finding]:
    """(the title's finding, the author's). `plate`: PNG bytes or an image."""
    plate = _plate(plate)
    background = typography.background(plate, t).convert("RGB")
    layout = typography.layout(plate.size, title, author, t)
    found = []
    for label in LINES:
        mask = Image.new("L", plate.size, 0)
        draw = ImageDraw.Draw(mask)
        for name, block, top in layout.placed:
            if name == label:
                block.draw(draw, top, layout.left, layout.width, t.align, 255)
        found.append(_measure(background, mask, typography.COLORS[t.color], label))
    return found[0], found[1]


# --- The fix ------------------------------------------------------------------------------------------------------------
SCRIMS = ("none", "soft", "strong")
OTHER_COLOR = {"light": "dark", "dark": "light", "accent": "light"}


@dataclass(frozen=True)
class Legibility:
    """What the check found on a version, after any fix."""

    title: Finding
    author: Finding
    changes: tuple[str, ...] = ()  # what the fix did, for the designer: "raised the scrim to strong", "switched the text to light"
    low: bool = False  # a line still doesn't pass

    @property
    def measured(self) -> bool:
        return bool(self.title.samples or self.author.samples)

    def line(self) -> str:
        """The line the designer reads at the end of a tool result."""
        said = f"Legibility: title {self.title.verdict} (Lc {self.title.lc_median:.0f}), author {self.author.verdict} (Lc {self.author.lc_median:.0f})"
        return said + "".join(f"; {c}" for c in self.changes) + ("; still low — tell the author" if self.low else "") + "."

    @property
    def changed(self) -> str | None:
        """The same, for the Details line: "scrim raised to strong", "text switched to light", "still low"."""
        said = [c.replace("raised the scrim", "scrim raised").replace("switched the text", "text switched") for c in self.changes]
        return ", ".join(said + (["still low"] if self.low else [])) or None


def _rank(found: tuple[Finding, Finding]) -> tuple:
    """Smaller is better: fewer failing lines, then fewer warned ones, then the higher of the two lines' weaker median."""
    verdicts = [f.verdict for f in found]
    return verdicts.count("fail"), verdicts.count("warn"), -min(f.lc_median for f in found)


def candidates(t: Typography, locked: frozenset[str] = frozenset()) -> list[Typography]:
    """What the fix may try, in order: the scrim a step stronger at a time, then the other text colour with each scrim
    from the current one up. A locked field (the author set it in this call) never moves. Only colour and scrim change."""
    stronger = SCRIMS[SCRIMS.index(t.scrim) + 1:]
    tries = [] if "scrim" in locked else [replace(t, scrim=s) for s in stronger]
    if "color" not in locked:
        other = OTHER_COLOR[t.color]
        tries += [replace(t, color=other, scrim=s) for s in ([t.scrim] if "scrim" in locked else [t.scrim, *stronger])]
    return tries


def fix(plate, title: str, author: str, t: Typography, locked: frozenset[str] = frozenset()) -> tuple[Typography, Legibility]:
    """(the type to keep, what was found on it). Unchanged when both lines pass; else the first candidate on which both
    pass; else the best-measured one, marked low."""
    plate = _plate(plate)
    plate.load()
    found = check(plate, title, author, t)
    best, best_found = t, found
    if any(f.verdict != "pass" for f in found) and (found[0].samples or found[1].samples):
        for candidate in candidates(t, locked):
            tried = check(plate, title, author, candidate)
            if _rank(tried) < _rank(best_found):
                best, best_found = candidate, tried
            if all(f.verdict == "pass" for f in tried):
                best, best_found = candidate, tried
                break
    changes = []
    if best.color != t.color:
        changes.append(f"switched the text to {best.color}")
    if best.scrim != t.scrim:
        changes.append(f"raised the scrim to {best.scrim}")
    return best, Legibility(best_found[0], best_found[1], tuple(changes), any(f.verdict != "pass" for f in best_found))
