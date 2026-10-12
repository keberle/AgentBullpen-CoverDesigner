# Offline: a type change is a version of its own on the same plate; a script or blackletter face is never in capitals;
# and a title or an author line always fits the padded box. No model is called. Run: `uv run pytest` in api/.
from test_gateway import FakeProxy, content_of, gql, proxy, start, token  # noqa: F401  (gateway mode is chosen at import there)

import io
import json

import pytest
from PIL import Image, ImageDraw

import main
import typography
from typography import Typography

TYPE = "typography { font titleCaps titleSize color }"
FIELDS = f"messages {{ role text version license {TYPE} image {{ model }} }} versions {{ n kind from imageDataUrl createdAt concept {TYPE} }}"
BOOKS = [(b.title, b.author) for b in main.BOOKS]
SIZES = ((387, 580), (1024, 1536))  # the cover on screen at its tallest, and the plate the download draws on


def chat(session_id: str, message: str, tok: str, version: int | None = None) -> dict:
    return gql(f"mutation($id: ID!, $m: String!, $v: Int) {{ chat(sessionId: $id, message: $m, version: $v) {{ {FIELDS} }} }}",
               {"id": session_id, "m": message, "v": version}, tok=tok).json()["data"]["chat"]


def painted(tok: str) -> dict:
    s = start(tok)
    assert [v["n"] for v in chat(s["id"], "Paint a cover.", tok)["versions"]] == [1]
    return s


# --- A type change makes a version --------------------------------------------------------------------------------------

def test_two_type_changes_after_one_paint_are_three_versions_of_one_plate(proxy):
    tok = token()
    s = painted(tok)
    images = len(proxy.of("image"))
    proxy.font_args = {"font": "bangers", "title_caps": True}
    second = chat(s["id"], "A comic-book font.", tok)
    proxy.font_args = {"font": "greatvibes"}
    body = chat(s["id"], "A more artistic font.", tok)
    v1, v2, v3 = body["versions"]
    assert [(v["n"], v["kind"], v["from"]) for v in (v1, v2, v3)] == [(1, "paint", None), (2, "type", 1), (3, "type", 2)]
    assert v1["imageDataUrl"] == v2["imageDataUrl"] == v3["imageDataUrl"]
    assert [v["typography"]["font"] for v in (v1, v2, v3)] == ["opensans", "bangers", "greatvibes"]
    assert len(proxy.of("image")) == images  # no image call for either
    # The plate is shared in memory, not copied.
    stored = main.designer.sessions[s["id"]].versions
    assert stored[0].image_data_url is stored[1].image_data_url is stored[2].image_data_url
    assert stored[1].concept == stored[0].concept and stored[1].image is stored[0].image
    # Each answer names the version it made; only a paint has a license and an image.
    assert (second["messages"][-1]["version"], body["messages"][-1]["version"]) == (2, 3)
    assert body["messages"][-1]["license"] is None and body["messages"][-1]["image"] is None
    assert body["messages"][-1]["typography"]["font"] == "greatvibes"


def test_the_version_a_type_change_was_made_from_keeps_its_type(proxy):
    tok = token()
    s = painted(tok)
    proxy.font_args = {"font": "rye", "title_size": "L"}
    chat(s["id"], "A western font.", tok)
    proxy.font_args = {"color": "accent"}
    body = chat(s["id"], "A gold font.", tok, version=1)  # the author went back to version 1
    v1, v2, v3 = body["versions"]
    assert (v1["typography"]["font"], v1["typography"]["titleSize"], v1["typography"]["color"]) == ("opensans", "M", "light")
    assert (v2["typography"]["font"], v2["typography"]["titleSize"], v2["typography"]["color"]) == ("rye", "L", "light")
    assert (v3["from"], v3["typography"]["font"], v3["typography"]["color"]) == (1, "opensans", "accent")


def test_a_paint_after_type_versions_inherits_the_shown_versions_type(proxy):
    tok = token()
    s = painted(tok)
    proxy.font_args = {"font": "bangers"}
    chat(s["id"], "A comic-book font.", tok)
    proxy.font_args = {"font": "rye"}
    chat(s["id"], "A western font.", tok)
    body = chat(s["id"], "Paint a cover.", tok)  # nothing selected: the latest, version 3
    v4 = body["versions"][3]
    assert (v4["n"], v4["kind"], v4["from"], v4["typography"]["font"]) == (4, "paint", 3, "rye")
    v5 = chat(s["id"], "Paint a cover.", tok, version=2)["versions"][4]
    assert (v5["kind"], v5["from"], v5["typography"]["font"]) == ("paint", 2, "bangers")


def test_a_turn_makes_one_version_however_often_the_type_tool_is_called(proxy):
    tok = token()
    s = painted(tok)
    proxy.font_calls = 2  # the designer calls set_typography twice before it answers
    body = chat(s["id"], "Another font.", tok)
    assert [(v["n"], v["kind"]) for v in body["versions"]] == [(1, "paint"), (2, "type")]
    # A paint and a type change in one turn: the type goes on the version just painted.
    proxy.font_calls, proxy.font_args = 2, {"font": "rye"}
    body = chat(s["id"], "Paint a cover in another font.", tok)
    assert [(v["n"], v["kind"], v["typography"]["font"]) for v in body["versions"]][2:] == [(3, "paint", "rye")]
    assert body["messages"][-1]["version"] == 3


def test_each_version_downloads_with_its_own_type(proxy):
    tok = token()
    proxy.plate = png((200, 300), (40, 60, 90))
    s = painted(tok)
    proxy.font_args = {"font": "bangers", "title_size": "L"}
    chat(s["id"], "A comic-book font.", tok)
    one, two = (download(s["id"], n, tok) for n in (1, 2))
    assert one != two and Image.open(io.BytesIO(one)).size == Image.open(io.BytesIO(two)).size == (200, 300)


def download(session_id: str, n: int, tok: str) -> bytes:
    from test_gateway import client
    r = client.get(f"/download/{session_id}/{n}", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200 and f"-v{n}.png" in r.headers["content-disposition"]
    return r.content


def png(size, colour) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, colour).save(out, format="PNG")
    return out.getvalue()


def test_the_tool_result_says_a_version_was_made(proxy):
    tok = token()
    s = painted(tok)
    chat(s["id"], "Another font.", tok)
    messages = json.loads(proxy.of("chat")[-1].content)["messages"]
    result = [content_of(m) for m in messages if m["role"] == "tool"][-1]
    assert result.startswith("Made version 2, version 1's picture with the type changed: font playfair (Playfair Display).")
    assert result.endswith("No picture was painted.")


# --- Script and blackletter faces are never in capitals -----------------------------------------------------------------

def test_only_the_script_and_the_blackletter_face_refuse_capitals():
    assert [i for i, f in typography.FACES.items() if not f.caps] == ["unifraktur", "greatvibes"]


@pytest.mark.parametrize("font", ["greatvibes", "unifraktur"])
def test_switching_to_one_of_them_turns_the_capitals_off(proxy, font):
    tok = token()
    s = painted(tok)
    proxy.font_args = {"font": "bangers", "title_caps": True}
    assert chat(s["id"], "A comic-book font.", tok)["versions"][-1]["typography"]["titleCaps"] is True
    proxy.font_args = {"font": font}
    body = chat(s["id"], "A more artistic font.", tok)
    assert body["versions"][-1]["typography"]["titleCaps"] is False and body["messages"][-1]["typography"]["titleCaps"] is False
    # Asking for capitals in such a face doesn't set them either.
    proxy.font_args = {"title_caps": True}
    assert chat(s["id"], "The font in capitals.", tok)["versions"][-1]["typography"]["titleCaps"] is False
    # Another face may have them again, when asked.
    proxy.font_args = {"font": "anton", "title_caps": True}
    assert chat(s["id"], "A poster font, capitals.", tok)["versions"][-1]["typography"]["titleCaps"] is True


@pytest.mark.parametrize("font", ["greatvibes", "unifraktur"])
def test_the_render_sets_the_title_as_typed_whatever_the_setting_says(font):
    title, author = BOOKS[0]
    as_typed = typography.layout((400, 600), title, author, Typography(font=font))
    capitals = typography.layout((400, 600), title, author, Typography(font=font, title_caps=True))  # a stored setting from before
    assert lines(capitals, "title") == lines(as_typed, "title") and "Hound" in " ".join(lines(as_typed, "title"))
    assert "HOUND" in " ".join(lines(typography.layout((400, 600), title, author, Typography(font="bangers", title_caps=True)), "title"))


def lines(layout: typography.Layout, name: str) -> list[str]:
    return next(b.lines for n, b, _ in layout.placed if n == name)


# --- Titles always fit ---------------------------------------------------------------------------------------------------

def ink(size, title: str, author: str, t: Typography):
    """(the layout, the box of everything drawn) on a cover of this size."""
    layout = typography.layout(size, title, author, t)
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    for _, block, top in layout.placed:
        block.draw(draw, top, layout.left, layout.width, t.align, 255)
    return layout, mask.getbbox()


@pytest.mark.parametrize("font", list(typography.FACES))
def test_no_glyph_leaves_the_padded_box(font):
    for size in SIZES:
        W, H = size
        for title, author in BOOKS:
            for title_size in typography.TITLE_SIZES:
                for caps, place, align in ((False, "top", "center"), (True, "bottom", "left"), (True, "middle", "right")):
                    t = Typography(font=font, title_size=title_size, title_caps=caps, title_place=place, align=align, author_size="M")
                    layout, (left, top, right, bottom) = ink(size, title, author, t)
                    where = (font, size, title, title_size, caps, place)
                    assert left >= round(0.09 * W) - 1 and right <= round(0.91 * W) + 1, where
                    assert top >= round(0.08 * W) - 1 and bottom <= H - round(0.08 * W) + 1, where
                    assert not any(b.broken for _, b, _ in layout.placed), where  # no face needs a word broken


def test_a_title_whose_longest_word_is_too_wide_is_set_smaller_and_one_that_fits_is_not():
    title, author = BOOKS[0]
    size = (1024, 1536)
    wide = block(typography.layout(size, title, author, Typography(font="rye", title_size="L", title_caps=True)))
    assert wide.scale < 1 and wide.fits_width and wide.px == pytest.approx(0.075 * 1536 * wide.scale)
    assert wide.scale >= typography.MIN_SCALE
    easy = block(typography.layout(size, "Treasure Island", author, Typography(font="oswald", title_size="M")))
    assert easy.scale == 1 and easy.tracking == typography.FACES["oswald"].title_tracking


def block(layout: typography.Layout, name: str = "title"):
    return next(b for n, b, _ in layout.placed if n == name)


def test_under_the_floor_the_tracking_tightens_and_only_then_a_word_breaks():
    # One long word in a narrow box: size steps to the floor, tracking to its minimum, and then the word breaks.
    args = ("anton", 400, 100.0, 0.0, 1.05)
    roomy = typography.fit("Baskervilles", *args, 2000, 1000)
    assert (roomy.scale, roomy.tracking, roomy.broken) == (1.0, 0.0, False)
    natural = roomy.extent("Baskervilles")
    smaller = typography.fit("Baskervilles", *args, natural * 0.78, 1000)
    assert smaller.scale == pytest.approx(0.75) and smaller.tracking == 0.0 and not smaller.broken  # 0.80 would be too wide
    tighter = typography.fit("Baskervilles", *args, natural * 0.57, 1000)
    assert tighter.scale == pytest.approx(0.60) and typography.MIN_TRACKING <= tighter.tracking < 0 and not tighter.broken
    broken = typography.fit("Baskervilles", *args, natural * 0.3, 1000)
    assert broken.scale == pytest.approx(0.60) and broken.tracking == typography.MIN_TRACKING and broken.broken
    assert "".join(broken.lines) == "Baskervilles" and len(broken.lines) > 1 and broken.fits_width


def test_a_block_too_tall_for_its_region_is_set_smaller():
    args = ("opensans", 800, 100.0, 0.0, 1.05, 900)
    text = "The Hound of the Baskervilles"
    full = typography.fit(text, *args, 10_000)
    short = typography.fit(text, *args, full.height - 1)
    assert full.scale == 1 and short.scale < 1 and short.height <= full.height - 1


def test_the_author_line_fits_by_the_same_rule():
    for font in typography.FACES:
        layout = typography.layout((387, 580), "Treasure Island", "Robert Louis Stevenson and a Second Author", Typography(font=font, author_size="M"))
        author = block(layout, "author")
        assert author.fits_width and not author.broken, font
