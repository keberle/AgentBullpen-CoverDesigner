# Offline: the measured legibility check. APCA against the published reference's own test values, the verdicts on
# synthetic plates and on a real dark plate, the fix and its order, and what the designer and the web are told. No model
# is called. Run: `uv run pytest` in api/.
from test_gateway import FakeProxy, content_of, gql, proxy, start, token  # noqa: F401  (gateway mode is chosen at import there)

import io
import json
import random
from pathlib import Path

import pytest
from PIL import Image

import designer as designer_module
import legibility
import main
import typography
from typography import Typography

TITLE, AUTHOR = "Treasure Island", "Robert Louis Stevenson"
HOUND = Path(__file__).parent / "fixtures" / "hound-plate.jpg"  # a painted night moor: dark all over
W, H = 400, 600
TYPE = "typography { font color scrim titleSize legibility { title author changed } }"
FIELDS = f"messages {{ role text {TYPE} }} versions {{ n {TYPE} }}"


def rgb(hex3: str) -> tuple[int, int, int]:
    return tuple(int(c * 2, 16) for c in hex3.lstrip("#"))


def flat(colour) -> Image.Image:
    return Image.new("RGB", (W, H), colour)


def half() -> Image.Image:
    """White above, black below: the title at the top is on white, the author at the bottom on black."""
    plate = flat((255, 255, 255))
    plate.paste((0, 0, 0), (0, H // 2, W, H))
    return plate


def noisy() -> Image.Image:
    """Mid-grey with every pixel at its own level: busy under the ink."""
    r = random.Random(7)
    plate = Image.new("RGB", (W, H))
    plate.putdata([(v, v, v) for v in (r.randint(88, 168) for _ in range(W * H))])
    return plate


PLATES = {"black": flat((0, 0, 0)), "white": flat((255, 255, 255)), "half": half(), "noisy": noisy()}


def verdicts(plate, color: str, scrim: str) -> str:
    title, author = legibility.check(plate, TITLE, AUTHOR, Typography(color=color, scrim=scrim, title_size="L"))
    return f"{title.verdict}/{author.verdict}"


# --- APCA ----------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text,bg,lc", [
    # apca-w3 0.1.9, test/index.js: calcAPCA(text, background), to the reference's own precision.
    ("#888", "#fff", 63.056469930209424),
    ("#fff", "#888", -68.54146436644962),
    ("#000", "#aaa", 58.146262578561334),
    ("#aaa", "#000", -56.24113336839742),
    ("#123", "#def", 91.66830811481631),
    ("#def", "#123", -93.06770049484275),
    ("#123", "#444", 8.32326136957393),
    ("#444", "#123", -7.526878460278154),
])
def test_apca_gives_the_references_published_values(text, bg, lc):
    assert legibility.apca_lc(rgb(text), rgb(bg)) == pytest.approx(lc, abs=1e-12)
    assert legibility.magnitude(rgb(text), rgb(bg)) == pytest.approx(abs(lc), abs=1e-12)


def test_apca_at_the_ends_and_for_the_same_colour():
    # The reference's stated range: "a signed float -108.0 to 106.0 (approx)".
    assert legibility.apca_lc((0, 0, 0), (255, 255, 255)) == pytest.approx(106.04067321268862, abs=1e-12)
    assert legibility.apca_lc((255, 255, 255), (0, 0, 0)) == pytest.approx(-107.88473318309848, abs=1e-12)
    assert legibility.apca_lc((90, 90, 90), (90, 90, 90)) == 0.0
    assert legibility.apca_lc((90, 90, 90), (96, 96, 96)) == 0.0  # under the low clip: no useful contrast is 0, not a small number


def test_the_constants_are_the_references():
    assert (legibility.MAIN_TRC, legibility.S_R, legibility.S_G, legibility.S_B) == (2.4, 0.2126729, 0.7151522, 0.0721750)
    assert (legibility.NORM_BG, legibility.NORM_TXT, legibility.REV_TXT, legibility.REV_BG) == (0.56, 0.57, 0.62, 0.65)
    assert (legibility.BLK_THRS, legibility.BLK_CLMP, legibility.SCALE, legibility.LO_OFFSET) == (0.022, 1.414, 1.14, 0.027)
    assert (legibility.DELTA_Y_MIN, legibility.LO_CLIP, legibility.MINIMUM_LARGE_TEXT) == (0.0005, 0.1, 45.0)


# --- The measurement -----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("plate,color,expected", [
    # each: the title's verdict / the author's, at scrim none, soft and strong
    ("black", "light", ["pass/pass", "pass/pass", "pass/pass"]),
    ("black", "dark", ["fail/fail", "fail/fail", "fail/pass"]),  # the white scrim behind dark text is strongest at the bottom
    ("black", "accent", ["pass/pass", "pass/pass", "pass/pass"]),
    ("white", "light", ["fail/fail", "warn/pass", "pass/pass"]),
    ("white", "dark", ["pass/pass", "pass/pass", "pass/pass"]),
    ("white", "accent", ["fail/fail", "fail/fail", "fail/pass"]),
    ("half", "light", ["fail/pass", "warn/pass", "pass/pass"]),  # the title on white, the author on black
    ("half", "dark", ["pass/fail", "pass/fail", "pass/pass"]),
    ("half", "accent", ["fail/pass", "fail/pass", "fail/pass"]),
    ("noisy", "light", ["pass/pass", "pass/pass", "pass/pass"]),
    ("noisy", "dark", ["fail/fail", "warn/pass", "pass/pass"]),
    ("noisy", "accent", ["fail/fail", "warn/pass", "pass/pass"]),
])
def test_verdicts_on_synthetic_plates(plate, color, expected):
    assert [verdicts(PLATES[plate], color, scrim) for scrim in ("none", "soft", "strong")] == expected


def test_a_finding_reports_the_median_the_fifth_percentile_the_share_below_and_the_busyness():
    title, author = legibility.check(PLATES["black"], TITLE, AUTHOR, Typography(color="light", scrim="none"))
    assert (title.label, author.label) == ("title", "author")
    assert title.lc_median == title.lc_p5 == pytest.approx(107.88473318309848)  # white ink on black, everywhere
    assert title.fraction_below == 0 and title.edge_density == 0 and title.verdict == "pass"
    assert 0 < title.samples <= legibility.MAX_SAMPLES and 0 < author.samples <= legibility.MAX_SAMPLES
    # A busy plate has edges under the ink; a flat one has none. Reported, never part of the verdict.
    busy, _ = legibility.check(PLATES["noisy"], TITLE, AUTHOR, Typography(color="light", scrim="none"))
    assert busy.edge_density > 0.05 and busy.verdict == "pass"
    # Half the title on white: the median passes, the fifth percentile doesn't.
    plate = flat((0, 0, 0))
    plate.paste((255, 255, 255), (0, 0, W // 3, H))
    patchy, _ = legibility.check(plate, TITLE, AUTHOR, Typography(color="light", scrim="none", title_size="L"))
    assert patchy.verdict == "warn" and patchy.lc_median > 100 and patchy.lc_p5 == 0 and 0.05 < patchy.fraction_below < 0.5
    assert legibility.measure(plate, TITLE, AUTHOR, Typography(color="light", scrim="none", title_size="L"), "title") == patchy


def test_the_ink_is_the_glyphs_and_never_the_shadow():
    # Light text on white has a dark shadow around it; were the shadow ink, the line would measure some contrast.
    title, author = legibility.check(PLATES["white"], TITLE, AUTHOR, Typography(color="light", scrim="none"))
    assert title.lc_median == author.lc_median == 0 and title.verdict == author.verdict == "fail"


def test_sampling_is_strided_to_the_cap_on_a_full_size_plate():
    plate = Image.new("RGB", (1024, 1536), (0, 0, 0))
    title, author = legibility.check(plate, TITLE, AUTHOR, Typography(color="light", title_size="L"))
    assert 1000 < title.samples <= legibility.MAX_SAMPLES and 500 < author.samples <= legibility.MAX_SAMPLES


def test_nearest_rank_percentiles_are_values_that_were_measured():
    values = [float(v) for v in range(1, 101)]
    assert legibility._nearest_rank(values, 0.05) == 5 and legibility._nearest_rank(values, 0.50) == 50
    assert legibility._nearest_rank([7.0], 0.05) == 7 and legibility._nearest_rank([1.0, 9.0], 0.50) == 1


def test_a_plate_too_small_to_hold_any_ink_is_not_measured():
    tiny = Image.new("RGB", (16, 24), (40, 80, 120))
    fixed, found = legibility.fix(tiny, TITLE, AUTHOR, Typography(color="dark", scrim="none"))
    assert fixed == Typography(color="dark", scrim="none") and not found.measured


# --- The fix -------------------------------------------------------------------------------------------------------------

def test_what_the_fix_may_try_and_in_what_order():
    def tried(t, locked=()):
        return [(c.color, c.scrim) for c in legibility.candidates(t, frozenset(locked))]

    assert tried(Typography(color="light", scrim="none")) == [("light", "soft"), ("light", "strong"), ("dark", "none"), ("dark", "soft"), ("dark", "strong")]
    assert tried(Typography(color="dark", scrim="soft")) == [("dark", "strong"), ("light", "soft"), ("light", "strong")]
    assert tried(Typography(color="accent", scrim="strong")) == [("light", "strong")]
    # A field the author set in this call never moves.
    assert tried(Typography(color="dark", scrim="none"), ["color"]) == [("dark", "soft"), ("dark", "strong")]
    assert tried(Typography(color="dark", scrim="none"), ["scrim"]) == [("light", "none")]
    assert tried(Typography(color="dark", scrim="none"), ["color", "scrim"]) == []
    # Nothing else is ever changed.
    t = Typography(font="rye", title_size="L", title_place="bottom", align="left", title_caps=True, author_place="top", author_size="M", color="light", scrim="none")
    for c in legibility.candidates(t):
        assert typography.replace(c, color=t.color, scrim=t.scrim) == t


@pytest.mark.parametrize("plate,color,to,changed", [
    ("black", "light", ("light", "none"), None),  # both pass: nothing to do
    ("white", "dark", ("dark", "none"), None),
    ("white", "light", ("light", "strong"), "scrim raised to strong"),  # soft only warns; strong passes
    ("half", "dark", ("dark", "strong"), "scrim raised to strong"),
    ("black", "dark", ("light", "none"), "text switched to light"),  # no scrim saves it; the other colour does
    ("white", "accent", ("light", "strong"), "text switched to light, scrim raised to strong"),
    ("noisy", "accent", ("accent", "strong"), "scrim raised to strong"),
])
def test_the_fix_keeps_the_first_choice_on_which_both_lines_pass(plate, color, to, changed):
    fixed, found = legibility.fix(PLATES[plate], TITLE, AUTHOR, Typography(color=color, scrim="none", title_size="L"))
    assert (fixed.color, fixed.scrim) == to and found.changed == changed and not found.low
    assert found.title.verdict == found.author.verdict == "pass"


def test_when_nothing_passes_the_best_measured_is_kept_and_it_is_said():
    # Accent on a half-white plate: no scrim lifts the title, and the author's colour is locked.
    t = Typography(color="accent", scrim="none", title_size="L")
    fixed, found = legibility.fix(PLATES["half"], TITLE, AUTHOR, t, frozenset({"color"}))
    assert (fixed.color, fixed.scrim) == ("accent", "strong") and found.low
    assert found.title.verdict == "fail" and found.changed == "scrim raised to strong, still low"
    assert found.line() == "Legibility: title fail (Lc 40), author pass (Lc 76); raised the scrim to strong; still low — tell the author."
    # With both fields locked nothing moves.
    fixed, found = legibility.fix(PLATES["half"], TITLE, AUTHOR, t, frozenset({"color", "scrim"}))
    assert fixed == t and found.low and found.changed == "still low"


def test_the_dark_title_on_the_real_dark_plate_fails_and_is_fixed_by_light_text():
    plate = Image.open(HOUND)
    t = Typography(color="dark", scrim="none", title_size="L")
    title, author = legibility.check(plate, "The Hound of the Baskervilles", "Arthur Conan Doyle", t)
    assert (title.verdict, author.verdict) == ("fail", "fail") and title.lc_median < 15
    fixed, found = legibility.fix(plate, "The Hound of the Baskervilles", "Arthur Conan Doyle", t)
    assert (fixed.color, fixed.scrim) == ("light", "none")
    assert found.line().endswith("; switched the text to light.") and found.line().startswith("Legibility: title pass (Lc ")
    assert typography.replace(fixed, color="dark") == t  # only the colour moved


# --- Through the designer --------------------------------------------------------------------------------------------------

def chat(session_id: str, message: str, tok: str) -> dict:
    return gql(f"mutation($id: ID!, $m: String!) {{ chat(sessionId: $id, message: $m) {{ {FIELDS} }} }}", {"id": session_id, "m": message}, tok=tok).json()


def on_the_hound(proxy, tok: str, **type_now) -> dict:
    """A session with one version on the dark plate, its type set to `type_now` as if from an earlier turn."""
    proxy.plate = HOUND.read_bytes()
    s = start(tok)
    assert [v["n"] for v in chat(s["id"], "Paint a cover.", tok)["data"]["chat"]["versions"]] == [1]
    version = main.designer.sessions[s["id"]].versions[0]
    version.typography = typography.replace(version.typography, **type_now)
    return s


def last_result(proxy) -> str:
    messages = json.loads(proxy.of("chat")[-1].content)["messages"]
    return [content_of(m) for m in messages if m["role"] == "tool"][-1]


def test_a_type_change_is_measured_and_a_bad_colour_from_before_is_fixed(proxy):
    tok = token()
    s = on_the_hound(proxy, tok, color="dark", scrim="none")
    proxy.font_args = {"title_size": "L"}  # "Make the title bigger.": the dark colour is not this call's
    body = chat(s["id"], "A bigger font.", tok)["data"]["chat"]
    said = body["messages"][-1]["typography"]
    assert (said["color"], said["scrim"], said["titleSize"]) == ("light", "none", "L")
    assert said["legibility"]["changed"] == "text switched to light"
    assert said["legibility"]["title"].startswith("Lc ") and int(said["legibility"]["title"][3:]) >= 45
    assert body["versions"][1]["typography"]["legibility"] == said["legibility"]  # the new version carries the same finding
    assert body["versions"][0]["typography"]["color"] == "dark"  # the version it was made from is as it was
    result = last_result(proxy)
    assert "; switched the text to light. Describe the change in one sentence. No picture was painted." in result
    assert result.index("Made version 2, version 1's picture with the type changed: title_size L.") < result.index("Legibility: title pass (Lc ")
    assert len(proxy.of("chat")) == 5  # the opening, the paint's two, this turn's two: the check calls no model


def test_a_colour_the_author_set_this_turn_is_not_overridden(proxy):
    tok = token()
    s = on_the_hound(proxy, tok)
    proxy.font_args = {"color": "dark"}
    body = chat(s["id"], "A dark font colour.", tok)["data"]["chat"]
    said = body["messages"][-1]["typography"]
    assert said["color"] == "dark" and said["scrim"] == "strong"  # the colour stays; only the scrim moved
    assert said["legibility"]["changed"] in ("scrim raised to strong", "scrim raised to strong, still low")
    assert "; raised the scrim to strong" in last_result(proxy) and "switched the text" not in last_result(proxy)


def test_a_scrim_the_author_set_this_turn_is_not_overridden_and_both_set_means_no_change(proxy):
    tok = token()
    s = on_the_hound(proxy, tok, color="dark", scrim="soft")
    proxy.font_args = {"scrim": "none"}
    said = chat(s["id"], "No shadow behind the font.", tok)["data"]["chat"]["messages"][-1]["typography"]
    assert (said["color"], said["scrim"], said["legibility"]["changed"]) == ("light", "none", "text switched to light")
    proxy.font_args = {"color": "dark", "scrim": "none"}
    said = chat(s["id"], "Dark font, no shadow.", tok)["data"]["chat"]["messages"][-1]["typography"]
    assert (said["color"], said["scrim"], said["legibility"]["changed"]) == ("dark", "none", "still low")
    assert last_result(proxy).count("; still low — tell the author.") == 1


def test_a_type_that_reads_well_is_left_alone_and_says_so(proxy):
    tok = token()
    s = on_the_hound(proxy, tok)
    proxy.font_args = {"font": "playfair"}
    said = chat(s["id"], "Another font.", tok)["data"]["chat"]["messages"][-1]["typography"]
    assert (said["color"], said["scrim"], said["legibility"]["changed"]) == ("light", "soft", None)
    result = last_result(proxy)
    assert "Legibility: title pass (Lc " in result and "author pass (Lc " in result and "; " not in result.split("Legibility:")[1].split(". ")[0]


def test_a_new_plate_inherits_the_type_and_is_measured_and_fixed_there(proxy):
    tok = token()
    s = on_the_hound(proxy, tok, color="dark", scrim="none", title_size="L")
    body = chat(s["id"], "Paint a cover.", tok)["data"]["chat"]
    v1, v2 = body["versions"]
    assert (v1["typography"]["color"], v1["typography"]["legibility"]["changed"]) == ("dark", None)  # the first plate was measured with the default type
    assert (v2["typography"]["color"], v2["typography"]["scrim"]) == ("light", "none")
    assert v2["typography"]["legibility"]["changed"] == "text switched to light"
    assert body["messages"][-1]["typography"]["legibility"]["changed"] == "text switched to light"
    result = last_result(proxy)
    assert "Painted version 2." in result and "; switched the text to light." in result and "The type was not changed in this call." not in result
    assert result.index("Legibility:") < result.index("Tell the author what you painted")


def test_a_paint_whose_type_reads_well_says_so_and_changes_nothing(proxy):
    tok = token()
    proxy.plate = HOUND.read_bytes()
    s = start(tok)
    body = chat(s["id"], "Paint a cover.", tok)["data"]["chat"]
    assert body["messages"][-1]["typography"] is None  # a paint turn, with nothing changed in the type
    assert body["versions"][0]["typography"]["legibility"]["changed"] is None
    result = last_result(proxy)
    assert "Legibility: title pass (Lc " in result and "The type was not changed in this call." in result


def test_a_plate_with_nothing_to_measure_gets_no_legibility(proxy):
    tok = token()
    s = start(tok)  # the stand-in's own plate: 16 × 24, too small to hold a glyph
    chat(s["id"], "Paint a cover.", tok)
    body = chat(s["id"], "Another font.", tok)["data"]["chat"]
    assert body["messages"][-1]["typography"]["legibility"] is None and body["versions"][-1]["typography"]["legibility"] is None
    assert "Legibility" not in last_result(proxy)


def test_the_instructions_say_to_pass_the_legibility_line_on_and_no_longer_speak_of_a_look():
    text = designer_module.INSTRUCTIONS
    assert "If the legibility line says something was changed or is still low, say so in one sentence." in text
    assert "look after set_typography" not in text and "a look at the cover" not in text
    assert not (Path(designer_module.__file__).parent / "look.py").exists()


def test_the_checked_cover_is_the_one_the_download_draws():
    # The check's background and layout are the render's own: drawing the type on the background gives the download.
    t = Typography(color="dark", scrim="strong", title_size="L")
    out = io.BytesIO()
    PLATES["noisy"].save(out, format="PNG")
    rendered = Image.open(io.BytesIO(typography.render(out.getvalue(), TITLE, AUTHOR, t))).convert("RGB")
    background = typography.background(PLATES["noisy"], t).convert("RGB")
    title, author = legibility.check(PLATES["noisy"], TITLE, AUTHOR, t)
    # Away from the type the two are the same picture.
    assert rendered.crop((0, H // 2 - 20, W, H // 2 + 20)).tobytes() == background.crop((0, H // 2 - 20, W, H // 2 + 20)).tobytes()
    assert title.samples and author.samples
