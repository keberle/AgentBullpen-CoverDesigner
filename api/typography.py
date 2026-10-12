# The cover's type: nine settings per version (the designer's set_typography tool makes a new version of the same
# plate with them changed; no paint), the library of faces a font request is resolved against, and the Pillow render behind
# GET /download/{session}/{n}: plate → scrim → title and author, baked in.
# The rendering values are the approved mockup's, as fractions
# of the cover height H (W = the cover width), and web/src/CoverType.tsx uses the same numbers:
#   padding: 8% (top/bottom) and 9% (left/right) of W (CSS `padding: 8% 9%` resolves both against the width);
#   gap between title and author on the same edge: 4% of the padded box's height;
#   title S/M/L 0.04375/0.05625/0.075 H, line-height 1.05, tracking per face (FACES), in capitals when asked (never
#   in a script or blackletter face); a line that doesn't fit the padded box is set smaller (Fitting, below);
#   author S/M 0.021875/0.028125 H, weight per face, uppercase with tracking 0.143 em (as typed with none in a script
#   or blackletter face), line-height 1.2;
#   text shadow 0.003125 H down, blur 0.01875 H (Gaussian sigma = blur / 2, as CSS), rgba(0,0,0,0.8);
#   with `dark` text a light glow instead (no offset, same blur, rgba(255,255,255,0.55)) and a white scrim
#   (the same alpha stops in white).
# Fonts are the OFL TTFs in fonts/<family>/ (google/fonts, with each family's OFL.txt): the variable file where the
# family has one, else the one static weight used.
import functools
import io
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONTS_DIR = Path(__file__).parent / "fonts"


@dataclass(frozen=True)
class Face:
    family: str  # the typeface's own name
    tag: str  # what it is for, in a few words: shown to the designer beside the id
    file: str  # under fonts/
    title_weight: int
    author_weight: int  # 600 where the family has it, else the weight of its one file
    title_tracking: float = 0.0  # em
    # The author line in capitals, tracked out. False for a script or a blackletter face, whose capitals are not made to
    # stand in a row: there the author is set as typed, with no tracking.
    author_caps: bool = True
    # False for the same faces: the title is never set in capitals either, whatever title_caps says.
    caps: bool = True


# The library: every face is SIL Open Font License 1.1. id → face. web/src/CoverType.tsx has the same list.
FACES = {
    "oswald": Face("Oswald", "condensed sans", "oswald/Oswald[wght].ttf", 700, 600, -0.014),
    "anton": Face("Anton", "poster, Impact-like", "anton/Anton-Regular.ttf", 400, 400),
    "bebas": Face("Bebas Neue", "tall capitals", "bebasneue/BebasNeue-Regular.ttf", 400, 400),
    "playfair": Face("Playfair Display", "classic serif", "playfairdisplay/PlayfairDisplay[wght].ttf", 800, 600, -0.014),
    "cormorant": Face("Cormorant Garamond", "old-style serif", "cormorantgaramond/CormorantGaramond[wght].ttf", 700, 600),
    "cinzel": Face("Cinzel", "inscriptional capitals", "cinzel/Cinzel[wght].ttf", 800, 600, 0.028),
    "opensans": Face("Open Sans", "plain sans", "opensans/OpenSans[wdth,wght].ttf", 800, 600, -0.014),
    "montserrat": Face("Montserrat", "geometric sans", "montserrat/Montserrat[wght].ttf", 800, 600),
    "bangers": Face("Bangers", "comic book", "bangers/Bangers-Regular.ttf", 400, 400),
    "comicneue": Face("Comic Neue", "hand-lettered comic, Comic Sans-like", "comicneue/ComicNeue-Bold.ttf", 700, 700),
    "creepster": Face("Creepster", "horror", "creepster/Creepster-Regular.ttf", 400, 400),
    "unifraktur": Face("UnifrakturMaguntia", "blackletter, gothic", "unifrakturmaguntia/UnifrakturMaguntia-Book.ttf", 400, 400, author_caps=False, caps=False),
    "rye": Face("Rye", "western", "rye/Rye-Regular.ttf", 400, 400),
    "alfaslab": Face("Alfa Slab One", "slab serif", "alfaslabone/AlfaSlabOne-Regular.ttf", 400, 400),
    "orbitron": Face("Orbitron", "sci-fi", "orbitron/Orbitron[wght].ttf", 800, 600),
    "greatvibes": Face("Great Vibes", "script, romance", "greatvibes/GreatVibes-Regular.ttf", 400, 400, author_caps=False, caps=False),
    "courierprime": Face("Courier Prime", "typewriter, noir", "courierprime/CourierPrime-Bold.ttf", 700, 700),
}
ALLOWED = {
    "font": tuple(FACES),
    "title_size": ("S", "M", "L"),
    "title_place": ("top", "middle", "bottom"),
    "align": ("left", "center", "right"),
    "title_caps": (True, False),
    "author_place": ("top", "bottom"),
    "author_size": ("S", "M"),
    "color": ("light", "dark", "accent"),
    "scrim": ("none", "soft", "strong"),
}
TITLE_SIZES = {"S": 0.04375, "M": 0.05625, "L": 0.075}
AUTHOR_SIZES = {"S": 0.021875, "M": 0.028125}
TITLE_LINE_HEIGHT = 1.05
AUTHOR_LINE_HEIGHT = 1.2
COLORS = {"light": (255, 255, 255), "dark": (17, 17, 17), "accent": (242, 198, 109)}
# (position 0..1, alpha) stops of the mockup's gradients, top to bottom: black, or white behind dark text.
SCRIMS = {
    "none": [],
    "soft": [(0, 0.45), (0.35, 0), (0.70, 0), (1, 0.5)],
    "strong": [(0, 0.7), (0.40, 0), (0.55, 0), (1, 0.85)],
}


@dataclass(frozen=True)
class Typography:
    font: str = "opensans"
    title_size: str = "M"
    title_place: str = "top"
    align: str = "center"
    title_caps: bool = False
    author_place: str = "bottom"
    author_size: str = "S"
    color: str = "light"
    scrim: str = "soft"
    # The author's own words for the font ("comic book", "Comic Sans") when the face was set for them; None otherwise.
    requested_font: str | None = None
    # True when those words named a typeface that isn't in the library, so the closest face was set in its place.
    substituted: bool = False

    def summary(self) -> str:
        return ", ".join(f"{k}={v}" for k, v in asdict(self).items())


def _key(name: str) -> str:
    """A font name with case, spaces and punctuation folded away: "Playfair-Display" and "playfair display" are one."""
    return "".join(c for c in name.lower() if c.isalnum())


def named_face(requested: str) -> str | None:
    """The library id a requested font name is, by its id or its family name; None when it names no face here."""
    key = _key(requested)
    return next((i for i, face in FACES.items() if key and key in (i, _key(face.family))), None)


# Well-known typefaces that aren't open-licence, and the library face each one gets. A requested name that contains
# the key (case and separators folded) resolves to the face. Only ever applied to the font name the designer passes.
STAND_INS = (
    (("comicsans",), "comicneue"),
    (("futura", "avantgarde", "gotham"), "montserrat"),
    (("impact",), "anton"),
    (("helvetica", "arial"), "opensans"),
    (("timesnewroman", "garamond", "caslon"), "cormorant"),
    (("bodoni", "didot"), "playfair"),
    (("trajan",), "cinzel"),
    (("courier",), "courierprime"),
    (("franklingothic", "din"), "oswald"),
)


def stand_in(requested: str) -> str | None:
    """The library id that stands in for a named typeface that isn't open-licence; None when the name isn't a known one."""
    key = _key(requested)
    return next((face for keys, face in STAND_INS if any(k in key for k in keys)), None)


def resolve_name(requested: str) -> tuple[str | None, bool]:
    """A typeface asked for by name → (the face to set, whether it stands in for the one named). A library face is
    itself; a known closed face gets its stand-in; for any other name (None, True): the designer's own choice stands."""
    if face := named_face(requested):
        return face, False
    return stand_in(requested), True


def library() -> str:
    """The faces as the designer reads them: "bangers (Bangers: comic book); …"."""
    return "; ".join(f"{i} ({face.family}: {face.tag})" for i, face in FACES.items())


def _bool(value) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    return None


def apply(current: Typography, **changes) -> tuple[Typography, dict]:
    """(the new settings, the fields that changed). Changes that are None are ignored. ValueError on an invalid value,
    with a one-line message listing the allowed values (nothing is applied then)."""
    given = {}
    for name, value in changes.items():
        if value is None:
            continue
        if name == "title_caps":
            v = _bool(value)
        else:
            v = value.strip() if isinstance(value, str) else value
            if name in ("title_size", "author_size") and isinstance(v, str):
                v = v.upper()
            elif isinstance(v, str):
                v = v.lower()
        if v is None or v not in ALLOWED[name]:
            allowed = ", ".join(str(a).lower() for a in ALLOWED[name])
            raise ValueError(f"Invalid {name} {value!r}: allowed values are {allowed}. Nothing was changed.")
        given[name] = v
    new = replace(current, **given)
    if not FACES[new.font].caps:
        new = replace(new, title_caps=False)  # a script or blackletter face is never in capitals: the setting says so
    changed = {k: v for k, v in given.items() if getattr(current, k) != v}
    return new, changed


# --- Render -------------------------------------------------------------------------------------------------------

def _font(font: str, weight: int, px: float) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(str(FONTS_DIR / FACES[font].file), px)
    try:
        axes = f.get_variation_axes()
    except OSError:  # a static file: its one weight
        return f
    f.set_variation_by_axes([weight if a["name"] == b"Weight" else a["default"] for a in axes])
    return f


# --- Fitting ------------------------------------------------------------------------------------------------------
# A line never leaves the padded box. The box's width is a maximum: the text wraps at word boundaries, and when the
# longest word is wider than the box, or the wrapped block taller than its region, the size steps down (never a
# horizontal squeeze, never letterspacing out), and the tracking, being in em, scales with it. Under the floor the
# tracking tightens to its minimum, and only then may a word break. web/src/coverFit.ts is the same, step for step.
MIN_SCALE = 0.60  # of the setting's size
SCALE_STEP = 0.05
MIN_TRACKING = -0.05  # em
TRACKING_STEP = 0.01
AUTHOR_REGION = 0.25  # of the padded box's height
INK_GUARD = 0.05  # em: room kept over ink that reaches a block's edge, for a renderer's rounding of the baseline
# Advances are measured at this size and scaled: at the size drawn FreeType rounds each glyph's advance to whole
# pixels, which at a small size is several per cent of a line. Scaled from a large size they are the font's own, as
# the browser's are, so both sides wrap a line at the same word.
REFERENCE = 1000.0  # px


@functools.lru_cache(maxsize=None)
def _reference(font: str, weight: int) -> ImageFont.FreeTypeFont:
    return _font(font, weight, REFERENCE)


class _Block:
    """One text element at one size: its wrapped lines, laid out like the browser's text (tracking after every
    character). A line is as wide as its ink: a glyph that overhangs its advance (an f, a script's swash) counts."""

    def __init__(self, text: str, font: ImageFont.FreeTypeFont, px: float, tracking_em: float, line_height: float, width: float,
                 break_words: bool = False, reference: ImageFont.FreeTypeFont | None = None):
        self.font, self.px, self.spacing = font, px, tracking_em * px
        self.reference = reference  # the same face at REFERENCE px, for the advances; None = the sized font's own
        self.tracking = tracking_em
        self.line_box = line_height * px
        ascent, descent = font.getmetrics()
        # The browser centres the font's ascent+descent in the line box; the baseline follows.
        self.baseline = (self.line_box - (ascent + descent)) / 2 + ascent
        self._inks: dict[str, tuple[float, float, float, float]] = {}
        self.broken = False
        self.lines = self._wrap(text.split(), width, break_words)
        self.fits_width = all(self.extent(line) <= width for line in self.lines)
        # Ink above the first line box or under the last: the block grows by it, so the ink stays inside the block.
        guard = INK_GUARD * px
        self.over_top = max(0.0, self._ink(self.lines[0])[2] + guard - self.baseline) if self.lines else 0.0
        self.over_bottom = max(0.0, self._ink(self.lines[-1])[3] + guard - (self.line_box - self.baseline)) if self.lines else 0.0
        self.height = self.over_top + self.line_box * len(self.lines) + self.over_bottom

    def length(self, s: str) -> float:
        """The font's own advance of `s`, without tracking."""
        return self.reference.getlength(s) * self.px / REFERENCE if self.reference else self.font.getlength(s)

    def advance(self, s: str) -> float:
        return self.length(s) + self.spacing * len(s)

    def _ink(self, s: str) -> tuple[float, float, float, float]:
        """Where `s` draws, from its origin on the baseline: (left, right, above, below). Left is 0 or less (ink before
        the origin), right the advance or more (ink past it)."""
        if s not in self._inks:
            left, right, above, below = 0.0, self.advance(s), 0.0, 0.0
            for j, ch in enumerate(s):
                l, t, r, b = self.font.getbbox(ch, anchor="ls")
                if r <= l or b <= t:
                    continue
                x = self.length(s[:j]) + self.spacing * j
                left, right, above, below = min(left, x + l), max(right, x + r), max(above, -t), max(below, b)
            self._inks[s] = (left, right, above, below)
        return self._inks[s]

    def extent(self, s: str) -> float:
        """The width `s` needs: its advance, and any ink outside it."""
        left, right, _, _ = self._ink(s)
        return right - left

    def _wrap(self, words: list[str], width: float, break_words: bool) -> list[str]:
        lines, line = [], ""
        for word in words:
            trial = f"{line} {word}" if line else word
            if line and self.extent(trial) > width:
                lines.append(line)
                line = word
            else:
                line = trial
            # The last resort: a word wider than the box breaks where it stops fitting.
            while break_words and len(line) > 1 and self.extent(line) > width:
                n = next((i for i in range(len(line) - 1, 0, -1) if self.extent(line[:i]) <= width), 1)
                lines.append(line[:n])
                line = line[n:]
                self.broken = True
        return lines + ([line] if line else [])

    def origin(self, line: str, left: float, width: float, align: str) -> float:
        """Where the line starts: aligned by its advance, as the browser does, then moved just far enough that ink
        overhanging the advance stays inside the box."""
        x = left + (0 if align == "left" else width - self.advance(line) if align == "right" else (width - self.advance(line)) / 2)
        ink_left, ink_right, _, _ = self._ink(line)
        if x + ink_left < left:
            return left - ink_left
        return min(x, left + width - ink_right)

    def draw(self, draw: ImageDraw.ImageDraw, top: float, left: float, width: float, align: str, fill) -> None:
        for i, line in enumerate(self.lines):
            x = self.origin(line, left, width, align)
            baseline = top + self.over_top + i * self.line_box + self.baseline
            for j, ch in enumerate(line):
                # Cumulative widths keep the font's own advances; tracking goes after each character.
                draw.text((x + self.length(line[:j]) + self.spacing * j, baseline), ch, font=self.font, fill=fill, anchor="ls")


def fit(text: str, font: str, weight: int, px: float, tracking_em: float, line_height: float, width: float, max_height: float) -> _Block:
    """The block for `text` that fits `width` and `max_height`: at the setting's size when it fits there, else smaller a
    step at a time to the floor, then with tighter tracking, then (still too wide) with the long word broken."""

    def block(scale: float, tracking: float, break_words: bool = False) -> _Block:
        b = _Block(text, _font(font, weight, px * scale), px * scale, tracking, line_height, width, break_words, _reference(font, weight))
        b.scale = scale
        return b

    def fits(b: _Block) -> bool:
        return b.fits_width and b.height <= max_height

    steps = round((1 - MIN_SCALE) / SCALE_STEP)
    b = block(1.0, tracking_em)
    for k in range(1, steps + 1):
        if fits(b):
            return b
        b = block(1 - SCALE_STEP * k, tracking_em)
    tracking = tracking_em
    while not fits(b) and tracking > MIN_TRACKING + 1e-9:
        tracking = max(MIN_TRACKING, round(tracking - TRACKING_STEP, 6))
        b = block(MIN_SCALE, tracking)
    return b if b.fits_width else block(MIN_SCALE, tracking, True)


def _scrim(size: tuple[int, int], scrim: str, rgb: tuple[int, int, int]) -> Image.Image | None:
    stops = SCRIMS[scrim]
    if not stops:
        return None
    w, h = size
    column = Image.new("L", (1, h))
    for y in range(h):
        p = (y + 0.5) / h
        for (p0, a0), (p1, a1) in zip(stops, stops[1:]):
            if p0 <= p <= p1:
                a = a0 + (a1 - a0) * ((p - p0) / (p1 - p0) if p1 > p0 else 0)
                break
        column.putpixel((0, y), round(a * 255))
    layer = Image.new("RGBA", size, rgb + (255,))
    layer.putalpha(column.resize(size))
    return layer


def background(plate: Image.Image, t: Typography) -> Image.Image:
    """What the type sits on: the plate with `t`'s scrim over it (RGBA)."""
    cover = plate.convert("RGBA")
    if (layer := _scrim(cover.size, t.scrim, (255, 255, 255) if t.color == "dark" else (0, 0, 0))) is not None:
        cover = Image.alpha_composite(cover, layer)
    return cover


@dataclass(frozen=True)
class Layout:
    placed: tuple  # ("title" | "author", its _Block, the block's top), in drawing order
    left: float  # the padded box
    width: float


def layout(size: tuple[int, int], title: str, author: str, t: Typography) -> Layout:
    """Where the title and the author go on a cover of this size: the one layout the download, and the legibility
    check's ink masks, are drawn from."""
    W, H = size
    pad_x, pad_y = 0.09 * W, 0.08 * W
    box_w, box_h = W - 2 * pad_x, H - 2 * pad_y
    gap = 0.04 * box_h
    face = FACES[t.font]
    title_px = TITLE_SIZES[t.title_size] * H
    author_px = AUTHOR_SIZES[t.author_size] * H
    author_block = fit(author.upper() if face.author_caps else author, t.font, face.author_weight, author_px,
                       0.143 if face.author_caps else 0.0, AUTHOR_LINE_HEIGHT, box_w, AUTHOR_REGION * box_h)
    # The title's region: the padded box less the author line (on both sides of a title in the middle).
    region = box_h - (author_block.height + gap) * (2 if t.title_place == "middle" else 1)
    title_block = fit(title.upper() if t.title_caps and face.caps else title, t.font, face.title_weight, title_px,
                      face.title_tracking, TITLE_LINE_HEIGHT, box_w, region)

    # Placement: elements on the same edge stack with the title first; a middle title is centred in the padded box.
    blocks = (("title", title_block, t.title_place), ("author", author_block, t.author_place))
    placed: list[tuple[str, _Block, float]] = []
    y = pad_y
    for name, b, place in blocks:
        if place == "top":
            placed.append((name, b, y))
            y += b.height + gap
    bottom_stack = [(name, b) for name, b, place in blocks if place == "bottom"]
    y = H - pad_y - sum(b.height for _, b in bottom_stack) - gap * (len(bottom_stack) - 1)
    for name, b in bottom_stack:
        placed.append((name, b, y))
        y += b.height + gap
    if t.title_place == "middle":
        placed.append(("title", title_block, pad_y + (box_h - title_block.height) / 2))
    return Layout(tuple(placed), pad_x, box_w)


def render(png: bytes, title: str, author: str, t: Typography) -> bytes:
    """The plate at its own size with the scrim, the title and the author drawn on it, as PNG bytes."""
    cover = background(Image.open(io.BytesIO(png)), t)
    W, H = cover.size
    dark = t.color == "dark"
    placed_at = layout(cover.size, title, author, t)
    placed, pad_x, box_w = [(b, top) for _, b, top in placed_at.placed], placed_at.left, placed_at.width

    # Shadow: the text's alpha, moved down, blurred, at 0.8 black (dark text: not moved, 0.55 white); then the text.
    mask = Image.new("L", cover.size, 0)
    mask_draw = ImageDraw.Draw(mask)
    for b, top in placed:
        b.draw(mask_draw, top + (0 if dark else 0.003125 * H), pad_x, box_w, t.align, 255)
    strength = 0.55 if dark else 0.8
    mask = mask.filter(ImageFilter.GaussianBlur(0.01875 * H / 2)).point(lambda v: round(v * strength))
    shadow = Image.new("RGBA", cover.size, (255, 255, 255, 255) if dark else (0, 0, 0, 255))
    shadow.putalpha(mask)
    cover = Image.alpha_composite(cover, shadow)
    draw = ImageDraw.Draw(cover)
    for b, top in placed:
        b.draw(draw, top, pad_x, box_w, t.align, COLORS[t.color] + (255,))

    out = io.BytesIO()
    cover.convert("RGB").save(out, format="PNG")
    return out.getvalue()
