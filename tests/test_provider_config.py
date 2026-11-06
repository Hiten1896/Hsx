import pytest

from agent.llm.router import default_router
from agent.llm.providers import JSONHTTPProvider, OpenAICompatibleProvider
from agent.llm.types import LLMRequest


def test_provider_model_names_must_be_nonempty_and_whitespace_free():
    assert JSONHTTPProvider(" google/gemini-3.6-flash ", "https://example.test").model == "google/gemini-3.6-flash"
    with pytest.raises(ValueError, match="model name"):
        JSONHTTPProvider("  ", "https://example.test")
    with pytest.raises(ValueError, match="model name"):
        JSONHTTPProvider("invalid model id", "https://example.test")


def test_default_router_uses_live_groq_model_catalog(monkeypatch, capsys):
    for key in (
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "GROQ_API_KEY",
        "GROK_API_KEY",
        "XAI_API_KEY",
        "OPENROUTER_API_KEY",
        "OPENAI_API_KEY",
        "OLLAMA_MODEL",
    ):
        monkeypatch.setenv(key, "")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.0-flash")
    monkeypatch.setenv("GROQ_MODEL", "llama-3.1-8b-instant")
    monkeypatch.setenv("OPENROUTER_MODEL", "google/gemini-2.0-flash-exp:free")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("HSX_PROVIDER", "gemini")
    monkeypatch.setenv("HSX_FALLBACKS", "groq,openrouter")
    monkeypatch.setattr(
        "agent.llm.providers.GroqProvider._fetch_models",
        lambda self, force=False: ["live-chat", "whisper-transcriber"],
    )

    router = default_router()

    assert {provider.name: provider.model for provider in router.providers} == {
        "gemini": "gemini-3.6-flash",
        "groq": "live-chat",
        "openrouter": "google/gemini-3.6-flash",
    }
    assert capsys.readouterr().out.startswith("Groq model llama-3.1-8b-instant not found.")


def test_main_warms_groq_model_catalog_at_startup(monkeypatch):
    import main

    seen = []

    class CatalogProbe:
        def __init__(self, api_key, model):
            seen.append((api_key, model))

    monkeypatch.setattr(main, "GROQ_API_KEY", "groq-startup-key")
    monkeypatch.setattr(main, "GROQ_MODEL", "configured-model")
    monkeypatch.setattr(main, "GroqProvider", CatalogProbe)

    main._warm_groq_model_catalog()

    assert seen == [("groq-startup-key", "configured-model")]


def test_openai_compatible_provider_normalizes_text_and_tools(monkeypatch):
    provider = OpenAICompatibleProvider("groq", "test-key", "test-model", "https://example.test")
    monkeypatch.setattr(
        provider,
        "_post",
        lambda payload: {
            "choices": [{
                "message": {"content": "hello", "tool_calls": []},
                "finish_reason": "stop",
            }]
        },
    )
    events = list(provider.generate(LLMRequest([{"role": "user", "content": "hi"}])))
    assert events[0].type == "text_delta"
    assert events[0].text == "hello"
    assert events[-1].type == "stop"
