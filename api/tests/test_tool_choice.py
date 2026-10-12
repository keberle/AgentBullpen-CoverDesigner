# Offline: code decides which tools a turn gets. A small intent call labels each author message, and the designer runs
# with only the tools that label allows, so a type request cannot paint. Also what the instructions, the tool descriptions
# and the tool results say. No model is called. Run: `uv run pytest` in api/.
from test_gateway import PLAN, FakeProxy, allowance, chat, code, content_of, proxy, start, token  # noqa: F401  (gateway mode is chosen at import there)

import json

import pytest

import concepts
import designer as designer_module
import intent
import main
import modes

PROBLEMS = "https://agentbullpen.ai/problems/"


def declared(request) -> list[str]:
    """The tools a chat request offers the model."""
    return sorted(t["function"]["name"] for t in json.loads(request.content).get("tools") or [])


def painted(tok: str) -> dict:
    """A session with one painted version."""
    s = start(tok)
    body = chat(s["id"], "Paint a cover.", tok)
    assert [v["n"] for v in body["data"]["chat"]["versions"]] == [1]
    return s


# --- The tools per intent ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("label,tools", [
    ("type", ["set_typography"]),
    ("picture", ["paint_cover", "set_typography"]),
    ("both", ["paint_cover", "set_typography"]),
    ("talk", []),
])
def test_the_designer_gets_only_the_tools_the_intent_allows(proxy, label, tools):
    tok = token()
    s = painted(tok)
    proxy.intent = label
    n = len(proxy.of("chat"))
    chat(s["id"], "Something the label decides.", tok)
    assert declared(proxy.of("chat")[n]) == tools


def test_a_type_turn_cannot_paint_even_when_the_designer_tries(proxy):
    tok = token()
    s = painted(tok)
    proxy.intent, proxy.eager = "type", True  # the designer would paint on anything, if it could
    before = (len(proxy.of("concept")), len(proxy.of("image")))
    body = chat(s["id"], "Please change to a more modern font too", tok)
    assert "errors" not in body, body
    assert [(v["n"], v["kind"]) for v in body["data"]["chat"]["versions"]] == [(1, "paint"), (2, "type")]  # a type version: no paint
    assert (len(proxy.of("concept")), len(proxy.of("image"))) == before  # no concept call, no image
    assert all("paint_cover" not in declared(r) for r in proxy.of("chat")[-1:])
    # The same message labelled as a picture change would have painted: the label is what decides.
    proxy.intent = "picture"
    body = chat(s["id"], "Please change to a more modern font too", tok)
    assert [(v["n"], v["kind"]) for v in body["data"]["chat"]["versions"]] == [(1, "paint"), (2, "type"), (3, "paint")]


def test_a_type_request_before_any_painting_gets_no_tool_and_is_told_why(proxy):
    tok = token()
    s = start(tok)
    proxy.intent = "type"
    chat(s["id"], "Use a serif font.", tok)
    request = proxy.of("chat")[-1]
    assert declared(request) == []
    said = content_of(json.loads(request.content)["messages"][-1])
    assert designer_module.NO_VERSION_YET in said and designer_module.NO_TOOL in said


@pytest.mark.parametrize("label,told", [("talk", True), ("type", False), ("picture", False), ("both", False)])
def test_a_turn_without_a_tool_is_told_it_has_none(proxy, label, told):
    tok = token()
    s = painted(tok)
    proxy.intent = label
    chat(s["id"], "Something the label decides.", tok)
    said = content_of(json.loads(proxy.of("chat")[-1].content)["messages"][-1])
    assert said.startswith(designer_module.license_line("interpretive") + "\n")
    assert (designer_module.NO_TOOL in said) == told
    if told:  # straight after the license line, before the author's words
        assert said.split("\n")[1] == designer_module.NO_TOOL
        assert designer_module.NO_TOOL == "[No tool this turn: don't say you painted or changed the type; answer in words only.]"


def test_a_failed_intent_call_keeps_every_tool_and_adds_no_line(proxy):
    tok = token()
    s = painted(tok)
    proxy.intent_answer = "not json"
    chat(s["id"], "Hello.", tok)
    assert designer_module.NO_TOOL not in content_of(json.loads(proxy.of("chat")[-1].content)["messages"][-1])


def test_the_opening_is_not_an_author_message_and_makes_no_intent_call(proxy):
    start(token())
    assert proxy.of("intent") == []
    assert declared(proxy.of("chat")[0]) == ["paint_cover", "set_typography"]


def test_the_intent_call_gets_the_last_reply_the_message_verbatim_and_whether_a_version_exists(proxy):
    tok = token()
    s = start(tok)
    chat(s["id"], 'Make it "bolder", too', tok)
    first = json.loads(proxy.of("intent")[0].content)
    said = first["messages"][0]["content"]
    said = said if isinstance(said, str) else json.dumps(said)
    assert intent.PROMPT in said
    assert "A painted version of the cover exists: no" in said
    assert "Hello. What mood do you want?" in said  # the designer's previous reply
    assert 'Make it "bolder", too' in said or 'Make it \\"bolder\\", too' in said
    assert first["model"] == "gpt-4o-mini" and first["max_completion_tokens"] == intent.MAX_TOKENS  # the designer's own chat model; a small answer
    assert first["response_format"]["json_schema"]["name"] == "Intent"
    assert first["response_format"]["json_schema"]["schema"]["properties"]["intent"]["enum"] == ["type", "picture", "both", "talk"]
    assert "tools" not in first
    # Through the proxy like any other call, with the member's token and the conversation id.
    r = proxy.of("intent")[0]
    assert str(r.url).endswith("/llm/openai/v1/chat/completions") and r.headers["authorization"] == f"Bearer {tok}"
    assert r.headers[modes.CONVERSATION_HEADER] == s["id"]
    chat(s["id"], "Paint a cover.", tok)
    chat(s["id"], "Thanks.", tok)
    assert "A painted version of the cover exists: yes" in json.dumps(json.loads(proxy.of("intent")[-1].content))
    # The previous reply is cut to its last 600 characters.
    assert intent.brief("#" * 700 + "END", "m", True).count("#") == intent.PREVIOUS_REPLY_CHARS - 3


def test_the_intent_runs_on_the_model_the_turn_runs_on(proxy):
    tok = token()
    s = start(tok)
    chat(s["id"], "Hello there.", tok, model="openai:gpt-6-luna")
    assert json.loads(proxy.of("intent")[-1].content)["model"] == "gpt-6-luna"
    chat(s["id"], "Hello there.", tok, model="anthropic:claude-haiku-5-5")
    assert len(proxy.of("anthropic-intent")) == 1 and len(proxy.of("anthropic")) == 1


# --- When the intent call fails -----------------------------------------------------------------------------------------

def test_invalid_output_twice_falls_back_to_every_tool(proxy, caplog):
    tok = token()
    s = painted(tok)
    proxy.intent_answer = "not json"
    n = len(proxy.of("intent"))
    with caplog.at_level("WARNING"):
        body = chat(s["id"], "Hello.", tok)
    assert "errors" not in body, body
    assert len(proxy.of("intent")) == n + 2  # asked twice
    assert declared(proxy.of("chat")[-1]) == ["paint_cover", "set_typography"]
    assert "designer.intent=failed" in caplog.text


def test_an_error_or_a_timeout_on_the_intent_call_falls_back_to_every_tool(proxy):
    import httpx2 as httpx

    tok = token()
    s = painted(tok)
    proxy.refuse["intent"] = (500, {"type": PROBLEMS + "upstream-error", "status": 502})
    body = chat(s["id"], "Hello.", tok)
    assert "errors" not in body, body
    assert declared(proxy.of("chat")[-1]) == ["paint_cover", "set_typography"]
    proxy.refuse = {}
    proxy.raise_for["intent"] = httpx.ReadTimeout("slow")
    body = chat(s["id"], "Hello.", tok)
    assert "errors" not in body, body
    assert declared(proxy.of("chat")[-1]) == ["paint_cover", "set_typography"]


@pytest.mark.parametrize("status,slug,expected", [
    (403, "budget-exhausted", "BUDGET_EXHAUSTED"),
    (403, "chat-off", "CHAT_OFF"),
    (401, "unauthorized", "UNAUTHORIZED"),
    (429, "rate-limited", "RATE_LIMITED"),
    (503, "provider-unavailable", "PROVIDER_UNAVAILABLE"),
])
def test_a_refusal_on_the_intent_call_ends_the_turn(proxy, status, slug, expected):
    tok = token()
    s = painted(tok)
    proxy.refuse["intent"] = (status, {"type": PROBLEMS + slug, "status": status})
    chats, intents = len(proxy.of("chat")), len(proxy.of("intent"))
    assert code(chat(s["id"], "Try a different font.", tok)) == expected
    assert len(proxy.of("intent")) == intents + 1  # sent once
    assert len(proxy.of("chat")) == chats  # the designer was never called


# --- Cost and tokens ----------------------------------------------------------------------------------------------------

def test_the_intent_calls_cost_and_tokens_are_in_the_turns_totals(proxy):
    proxy.headers = {"intent": {modes.CALL_COST_HEADER: "0.000030"}, "chat": {modes.CALL_COST_HEADER: "0.000400"}}
    tok = token()
    s = start(tok, allowance())
    body = chat(s["id"], "Hello.", tok, headers=allowance(remaining="0.9800"))["data"]["chat"]
    assert body["messages"][-1]["usage"] == {"inputTokens": 200, "outputTokens": 40, "costUsd": pytest.approx(0.00043)}
    assert body["budget"]["remainingUsd"] == pytest.approx(0.98 - 0.00043)
    # An intent call that came without a cost leaves the turn with none: never a partial sum.
    proxy.headers = {"chat": {modes.CALL_COST_HEADER: "0.000400"}}
    body = chat(s["id"], "Hello.", tok, headers=allowance(remaining="0.9800"))["data"]["chat"]
    assert body["messages"][-1]["usage"]["costUsd"] is None


# --- The text the designer reads -----------------------------------------------------------------------------------------

def test_the_instructions_say_what_each_tool_is_for_and_to_describe_only_what_was_done():
    text = designer_module.INSTRUCTIONS
    first = text.split("\n\n")[0]
    assert first.endswith(
        "Describe only what your tool calls did in this turn: if you didn't call set_typography, don't say the type changed;"
        " if you didn't call paint_cover, don't say you painted.")
    assert "or any change to the picture itself (not the type)" in text
    assert "Never paint a new image just to change type." in text
    assert "and after a paint if the title would sit on a busy part of the image" in text
    assert "**Both**" not in text and "call both" not in text
    assert text in designer_module.FULL_INSTRUCTIONS


def test_each_tools_description_starts_with_what_it_is_for():
    tools = {t.name: t.description for t in (main.designer._paint_tool(), main.designer._typography_tool())}
    assert tools["paint_cover"].startswith(
        "Only for a change to the picture itself. Never for the font, lettering, title or author placement, text colour,"
        " capitals or the shadow behind the text — use set_typography for those.")
    assert tools["set_typography"].startswith("For every type request; it never paints.")


def test_a_paints_result_says_the_type_was_not_changed():
    plan = concepts.ConceptPlan(**PLAN)
    result = designer_module.painted_result(2, plan)
    assert "The type was not changed in this call." in result
    assert result.index("The type was not changed in this call.") < result.index("Tell the author what you painted")
    assert result.startswith('Painted version 2. Concept: "Idea 0"')


def test_the_tool_results_the_designer_reads_say_what_did_not_happen(proxy):
    tok = token()
    s = painted(tok)
    chat(s["id"], "Try a different font.", tok)
    results = [content_of(m) for m in json.loads(proxy.of("chat")[-1].content)["messages"] if m["role"] == "tool"]
    made, typed = results
    assert "Painted version 1" in made and "The type was not changed in this call." in made
    assert typed.startswith("Made version 2, version 1's picture with the type changed: font playfair (Playfair Display).") and typed.endswith("No picture was painted.")
    # The type request made no concept call and no image call of its own.
    assert len(proxy.of("concept")) == 1 and len(proxy.of("image")) == 1


def test_the_intent_prompt_names_the_four_labels_and_ends_with_its_examples():
    for part in ("- type:", "- picture:", "- both:", "- talk:", "A message is type only if it names one of these.",
                 "A request for a cover is picture even if an earlier message asked for the same thing.",
                 '"a more modern font" is type; "make it more modern" is picture.'):
        assert part in intent.PROMPT
    for line in ('"Please change to a more modern font too" -> type', '"Make the title bigger." -> type', '"Use dark text." -> type',
                 '"Make it more modern." -> picture', '"Futuristic cover, please" -> picture (also when a cover is already painted)',
                 '"Make the sky gold." -> picture', '"Make the title gold." -> type',
                 '"Lose the fleur-de-lis and use a serif title." -> both', '"What mood would suit this book?" -> talk'):
        assert line + "\n" in intent.PROMPT
    assert len(intent.EXAMPLES) == 9
    assert intent.PROMPT.endswith("Give the label and a reason of one short sentence.")
