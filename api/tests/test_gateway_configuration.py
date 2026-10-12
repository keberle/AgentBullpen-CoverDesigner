# Gateway configuration fails closed, offline: half-filled gateway settings stop the api at startup instead of running it
# standalone with no sign-in. Run: `uv run pytest` in api/.
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import images
import modes
from designer import Settings

API = Path(__file__).parent.parent
APPSETTINGS = API.parent / "src" / "CoverDesigner.AppHost" / "appsettings.json"
KEY = "test-signing-key-for-tests-only-0123456789"
ALL = (modes.GATEWAY_KEY, *modes.GATEWAY_ONLY)


@pytest.fixture
def env(monkeypatch):
    """The seven gateway settings unset; a test sets the ones it is about."""
    for name in ALL:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch.setenv


def choose() -> modes.Mode:
    return modes.choose(Settings.from_env(), images.ImageSettings.from_env())


def test_a_placeholder_key_is_refused(env):
    env(modes.GATEWAY_KEY, "<the site's signing key>")
    with pytest.raises(modes.GatewayConfigurationError) as e:
        choose()
    assert str(e.value) == "GATEWAY_SIGNING_KEY is still a <placeholder>. Set the key for gateway mode, or clear it for standalone."


@pytest.mark.parametrize("name", modes.GATEWAY_ONLY)
def test_an_empty_key_beside_another_gateway_setting_is_refused(env, name):
    env(modes.GATEWAY_KEY, "")
    env(name, "https://site.test/llm")
    with pytest.raises(modes.GatewayConfigurationError) as e:
        choose()
    assert str(e.value) == f"{name} is set but GATEWAY_SIGNING_KEY is empty. Set the key for gateway mode, or clear {name} for standalone."


def test_the_first_such_setting_is_named(env):
    env("GATEWAY_IMAGE_MODEL", "openai:some-image-model")
    env("GATEWAY_AUDIENCE", "covers")
    with pytest.raises(modes.GatewayConfigurationError, match="^GATEWAY_AUDIENCE is set"):
        choose()


def test_all_unset_is_standalone(env):
    assert choose().gateway is False


def test_placeholders_beside_an_empty_key_count_as_unset(env):
    for name in modes.GATEWAY_ONLY:
        env(name, "<optional>")
    assert choose().gateway is False


def test_the_shipped_appsettings_is_standalone(env):
    shipped = json.loads(APPSETTINGS.read_text(encoding="utf-8"))
    # The AppHost's mapping (AppHost.cs): each setting's value, or empty.
    env("GATEWAY_SIGNING_KEY", shipped["Auth"]["Gateway"]["SigningKey"])
    env("GATEWAY_ISSUER", shipped["Auth"]["Gateway"]["Issuer"])
    env("GATEWAY_AUDIENCE", shipped["Auth"]["Gateway"]["Audience"])
    env("GATEWAY_LLM_BASE_URL", shipped["Gateway"]["LlmBaseUrl"])
    env("GATEWAY_CHAT_MODEL", shipped["Gateway"]["ChatModel"])
    env("GATEWAY_CONCEPT_MODEL", shipped["Gateway"]["ConceptModel"])
    env("GATEWAY_IMAGE_MODEL", shipped["Gateway"]["ImageModel"])
    assert choose().gateway is False


def test_a_full_set_is_gateway_mode(env):
    env("GATEWAY_SIGNING_KEY", KEY)
    env("GATEWAY_ISSUER", "https://site.test")
    env("GATEWAY_AUDIENCE", "covers")
    env("GATEWAY_LLM_BASE_URL", "https://site.test/llm")
    env("GATEWAY_CHAT_MODEL", "openai:chat-model")
    env("GATEWAY_CONCEPT_MODEL", "openai:concept-model")
    env("GATEWAY_IMAGE_MODEL", "openai:image-model")
    mode = choose()
    assert mode.gateway is True
    assert mode.default_chat_model == "openai:chat-model"


def test_a_key_alone_is_gateway_mode_with_the_defaults(env):
    env("GATEWAY_SIGNING_KEY", KEY)
    assert choose().gateway is True


def start_the_api(**gateway: str) -> subprocess.CompletedProcess:
    """Imports main.py in a fresh interpreter, as Uvicorn does before it serves anything."""
    environ = {k: v for k, v in os.environ.items() if k not in ALL}
    return subprocess.run([sys.executable, "-c", "import main"], cwd=API, env={**environ, **gateway}, capture_output=True, text=True, timeout=120)


def test_the_api_does_not_start_with_a_placeholder_key():
    done = start_the_api(GATEWAY_SIGNING_KEY="<the site's signing key>")
    assert done.returncode == 1
    assert done.stderr.strip().splitlines()[-1] == (
        "The API did not start: GATEWAY_SIGNING_KEY is still a <placeholder>. Set the key for gateway mode, or clear it for standalone.")


def test_the_api_does_not_start_with_an_empty_key_beside_a_gateway_setting():
    done = start_the_api(GATEWAY_SIGNING_KEY="", GATEWAY_LLM_BASE_URL="https://site.test/llm")
    assert done.returncode == 1
    assert done.stderr.strip().splitlines()[-1] == (
        "The API did not start: GATEWAY_LLM_BASE_URL is set but GATEWAY_SIGNING_KEY is empty."
        " Set the key for gateway mode, or clear GATEWAY_LLM_BASE_URL for standalone.")


def test_the_api_starts_standalone_with_nothing_set():
    done = start_the_api()
    assert done.returncode == 0, done.stderr
