# Offline: the library of open-licence faces, a font asked for by name (the request kept, a substitution said), and the
# resolving of well-known names in code. No model is called. Run: `uv run pytest` in api/.
from test_gateway import FakeProxy, allowance, code, content_of, gql, proxy, start, token  # noqa: F401  (gateway mode is chosen at import there)

import base64
import io
import json

import pytest
from PIL import Image

import designer as designer_module
import main
import modes
import typography

PROBLEMS = "https://agentbullpen.ai/problems/"
TYPE = "typography { font fontFamily requestedFont substituted titleSize }"
FIELDS = f"messages {{ role text usage {{ inputTokens outputTokens costUsd }} {TYPE} }} versions {{ n {TYPE} }}"
IDS = ("oswald", "anton", "bebas", "playfair", "cormorant", "cinzel", "opensans", "montserrat", "bangers", "comicneue", "creepster",
       "unifraktur", "rye", "alfaslab", "orbitron", "greatvibes", "courierprime")


def chat(session_id: str, message: str, tok: str, headers: dict | None = None) -> dict:
    return gql(f"mutation($id: ID!, $m: String!) {{ chat(sessionId: $id, message: $m) {{ {FIELDS} }} }}",
               {"id": session_id, "m": message}, tok=tok, headers=headers).json()


def painted(tok: str) -> dict:
    s = start(tok)
    assert [v["n"] for v in chat(s["id"], "Paint a cover.", tok)["data"]["chat"]["versions"]] == [1]
    return s


def results(proxy) -> list[str]:
    """The tool results of the turn in progress, as the designer's last request carried them."""
    messages = json.loads(proxy.of("chat")[-1].content)["messages"]
    last = max(i for i, m in enumerate(messages) if m["role"] == "user")
    return [content_of(m) for m in messages[last + 1:] if m["role"] == "tool"]


def plate() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (200, 300), (30, 60, 110)).save(out, format="PNG")
    return out.getvalue()


# --- The library ---------------------------------------------------------------------------------------------------------

def test_the_library_has_seventeen_faces_each_with_its_file_and_its_open_font_licence():
    assert tuple(typography.FACES) == IDS and typography.ALLOWED["font"] == IDS
    for face in typography.FACES.values():
        ttf = typography.FONTS_DIR / face.file
        assert ttf.is_file(), face.file
        licence = (ttf.parent / "OFL.txt").read_text(encoding="utf-8")
        assert "SIL OPEN FONT LICENSE Version 1.1" in licence, face.family


def test_every_face_renders_a_cover_at_the_plates_size():
    for font in typography.FACES:
        png = typography.render(plate(), "Treasure Island", "Robert Louis Stevenson", typography.Typography(font=font, title_size="L"))
        assert Image.open(io.BytesIO(png)).size == (200, 300)


def test_title_weights_and_the_author_weight_rule():
    faces = typography.FACES
    assert {i: f.title_weight for i, f in faces.items()} == {
        "oswald": 700, "anton": 400, "bebas": 400, "playfair": 800, "cormorant": 700, "cinzel": 800, "opensans": 800, "montserrat": 800,
        "bangers": 400, "comicneue": 700, "creepster": 400, "unifraktur": 400, "rye": 400, "alfaslab": 400, "orbitron": 800,
        "greatvibes": 400, "courierprime": 700}
    # 600 where the family's file has it (a variable weight axis), else the weight of its one file.
    for i, f in faces.items():
        assert f.author_weight == (600 if "[" in f.file else f.title_weight), i
    # The four first faces keep their title tracking; the others have none.
    assert {i: f.title_tracking for i, f in faces.items() if f.title_tracking} == {"oswald": -0.014, "playfair": -0.014, "cinzel": 0.028, "opensans": -0.014}


def test_a_requested_name_is_matched_to_a_library_face_by_id_or_family():
    assert typography.named_face("Playfair Display") == "playfair" and typography.named_face("comic-neue") == "comicneue"
    assert typography.named_face("BANGERS") == "bangers" and typography.named_face("Alfa Slab One") == "alfaslab"
    for other in ("Comic Sans", "Futura", "comic book", "something spooky", ""):
        assert typography.named_face(other) is None


def test_the_tool_lists_every_face_with_its_tag_and_how_to_ask_by_name():
    tool = main.designer._typography_tool()
    for i, face in typography.FACES.items():
        assert f"{i} ({face.family}: {face.tag})" in tool.description
    assert "comicneue (Comic Neue: hand-lettered comic, Comic Sans-like)" in tool.description
    assert "Pick the closest face for what the author asked" in tool.description
    assert "When the author named a typeface that isn't in the library" in tool.description and "requested_font" in tool.description
    assert "oswald | playfair | cinzel | opensans" not in tool.description
    assert "say so in one sentence of your reply and name the face that was set" in designer_module.INSTRUCTIONS


# --- Asking by name ------------------------------------------------------------------------------------------------------

def test_a_style_request_sets_the_face_and_keeps_the_authors_words(proxy):
    tok = token()
    s = painted(tok)
    proxy.font_args = {"font": "bangers", "requested_font": "comic book", "requested_as": "style", "title_caps": True}
    body = chat(s["id"], "I want a comic-book style font for the title.", tok)["data"]["chat"]
    said = body["messages"][-1]["typography"]
    assert (said["font"], said["fontFamily"], said["requestedFont"], said["substituted"]) == ("bangers", "Bangers", "comic book", False)
    assert [v["typography"]["requestedFont"] for v in body["versions"]] == [None, "comic book"]  # the type change is version 2
    (result,) = results(proxy)
    assert result.startswith("Made version 2, version 1's picture with the type changed: font bangers (Bangers), title_caps true.")
    assert "open-licence fonts here" not in result


def test_a_named_face_that_isnt_in_the_library_is_said_as_a_substitution(proxy):
    tok = token()
    s = painted(tok)
    proxy.font_args = {"font": "comicneue", "requested_font": "Comic Sans", "requested_as": "name"}
    body = chat(s["id"], "Use the Comic Sans font for the title.", tok)["data"]["chat"]
    said = body["messages"][-1]["typography"]
    assert (said["font"], said["fontFamily"], said["requestedFont"], said["substituted"]) == ("comicneue", "Comic Neue", "Comic Sans", True)
    assert [v["typography"]["substituted"] for v in body["versions"]] == [False, True]
    (result,) = results(proxy)
    assert "Comic Sans isn't one of the open-licence fonts here; set Comic Neue, the closest." in result


def test_a_named_face_that_is_in_the_library_is_the_face_set(proxy):
    tok = token()
    s = painted(tok)
    proxy.font_args = {"font": "comicneue", "requested_font": "Bangers", "requested_as": "name"}  # the designer picked another
    said = chat(s["id"], "Use the Bangers font.", tok)["data"]["chat"]["messages"][-1]["typography"]
    assert (said["font"], said["requestedFont"], said["substituted"]) == ("bangers", "Bangers", False)
    # A name with no font id: the face it names.
    proxy.font_args = {"requested_font": "Playfair Display"}
    said = chat(s["id"], "Use the Playfair Display font.", tok)["data"]["chat"]["messages"][-1]["typography"]
    assert (said["font"], said["substituted"]) == ("playfair", False)


def test_a_request_without_a_face_changes_nothing_and_asks_for_one(proxy):
    tok = token()
    s = painted(tok)
    proxy.font_args = {"requested_font": "Papyrus", "requested_as": "name"}
    body = chat(s["id"], "Use the Papyrus font.", tok)["data"]["chat"]
    assert body["messages"][-1]["typography"] is None and body["versions"][0]["typography"]["font"] == "opensans"
    (result,) = results(proxy)
    assert "Nothing was changed." in result and "closest library id" in result


@pytest.mark.parametrize("asked,face", [
    ("Comic Sans", "comicneue"), ("Comic Sans MS", "comicneue"),
    ("Futura", "montserrat"), ("ITC Avant Garde Gothic", "montserrat"), ("Gotham", "montserrat"),
    ("Impact", "anton"),
    ("Helvetica", "opensans"), ("Helvetica Neue", "opensans"), ("Arial", "opensans"),
    ("Times New Roman", "cormorant"), ("Adobe Garamond", "cormorant"), ("Caslon", "cormorant"),
    ("Bodoni", "playfair"), ("Didot", "playfair"),
    ("Trajan", "cinzel"), ("trajan-pro", "cinzel"),
    ("Courier", "courierprime"), ("Courier New", "courierprime"),
    ("Franklin Gothic Heavy", "oswald"), ("DIN Condensed", "oswald"),
])
def test_a_well_known_closed_face_resolves_to_its_stand_in(asked, face):
    assert typography.stand_in(asked) == face
    assert typography.resolve_name(asked) == (face, True)


def test_a_library_name_is_itself_and_an_unknown_name_resolves_to_nothing():
    assert typography.resolve_name("Courier Prime") == ("courierprime", False)  # the library's own face, not the Courier row
    assert typography.resolve_name("Cormorant Garamond") == ("cormorant", False)
    assert typography.resolve_name("montserrat") == ("montserrat", False)
    assert typography.resolve_name("Papyrus") == (None, True)
    assert [face for _, face in typography.STAND_INS] == ["comicneue", "montserrat", "anton", "opensans", "cormorant", "playfair", "cinzel", "courierprime", "oswald"]
    assert all(face in typography.FACES for _, face in typography.STAND_INS)


def test_a_named_closed_face_is_resolved_in_code_whatever_face_the_designer_picked(proxy):
    tok = token()
    s = painted(tok)
    proxy.font_args = {"font": "opensans", "requested_font": "Comic Sans", "requested_as": "name"}  # the designer matched "Sans"
    said = chat(s["id"], "Use the Comic Sans font for the title.", tok)["data"]["chat"]["messages"][-1]["typography"]
    assert (said["font"], said["fontFamily"], said["requestedFont"], said["substituted"]) == ("comicneue", "Comic Neue", "Comic Sans", True)
    assert "Comic Sans isn't one of the open-licence fonts here; set Comic Neue, the closest." in results(proxy)[0]
    # With no face from the designer at all, the stand-in is still set.
    proxy.font_args = {"requested_font": "Trajan", "requested_as": "name"}
    said = chat(s["id"], "Use the Trajan font.", tok)["data"]["chat"]["messages"][-1]["typography"]
    assert (said["font"], said["requestedFont"], said["substituted"]) == ("cinzel", "Trajan", True)
    # A name that is neither in the library nor in the table: the designer's own choice stands, and it is said.
    proxy.font_args = {"font": "rye", "requested_font": "Papyrus", "requested_as": "name"}
    said = chat(s["id"], "Use the Papyrus font.", tok)["data"]["chat"]["messages"][-1]["typography"]
    assert (said["font"], said["requestedFont"], said["substituted"]) == ("rye", "Papyrus", True)
    assert "Papyrus isn't one of the open-licence fonts here; set Rye, the closest." in results(proxy)[0]
    # The table is for names only: the same words given as a style leave the designer's choice alone.
    proxy.font_args = {"font": "bangers", "requested_font": "impact", "requested_as": "style"}
    said = chat(s["id"], "A font with impact.", tok)["data"]["chat"]["messages"][-1]["typography"]
    assert (said["font"], said["substituted"]) == ("bangers", False)


def test_the_author_line_is_as_typed_in_script_and_blackletter_faces(monkeypatch):
    assert [i for i, f in typography.FACES.items() if not f.author_caps] == ["unifraktur", "greatvibes"]
    drawn = []
    real = typography._Block

    def block(text, font, px, tracking, *rest):
        drawn.append((text, tracking))
        return real(text, font, px, tracking, *rest)

    monkeypatch.setattr(typography, "_Block", block)
    authors = []
    for font in ("greatvibes", "unifraktur", "bangers"):
        drawn.clear()
        typography.render(plate(), "Treasure Island", "Robert Louis Stevenson", typography.Typography(font=font))
        authors.append(drawn[0])  # the author line is fitted first, at the setting's size
    assert authors == [("Robert Louis Stevenson", 0.0), ("Robert Louis Stevenson", 0.0), ("ROBERT LOUIS STEVENSON", 0.143)]


def test_a_later_font_change_without_a_request_clears_it_and_other_changes_keep_it(proxy):
    tok = token()
    s = painted(tok)
    proxy.font_args = {"font": "comicneue", "requested_font": "Comic Sans", "requested_as": "name"}
    chat(s["id"], "Use the Comic Sans font.", tok)
    proxy.font_args = {"title_size": "L"}
    said = chat(s["id"], "Bigger font.", tok)["data"]["chat"]["messages"][-1]["typography"]
    assert (said["font"], said["requestedFont"], said["substituted"], said["titleSize"]) == ("comicneue", "Comic Sans", True, "L")
    proxy.font_args = {"font": "rye"}
    said = chat(s["id"], "Another font.", tok)["data"]["chat"]["messages"][-1]["typography"]
    assert (said["font"], said["requestedFont"], said["substituted"]) == ("rye", None, False)


def test_an_unknown_font_id_changes_nothing(proxy):
    tok = token()
    s = painted(tok)
    proxy.font_args = {"font": "comicsans"}
    body = chat(s["id"], "A comic font.", tok)["data"]["chat"]
    assert body["messages"][-1]["typography"] is None
    assert "Invalid font 'comicsans'" in results(proxy)[0] and "bangers" in results(proxy)[0]
