from collections.abc import Iterator

from agent.llm.providers import ProviderError
from agent.llm.router import LLMRouter
from agent.llm.types import LLMEvent, LLMRequest


class FailingProvider:
    name = "primary"

    def generate(self, request: LLMRequest) -> Iterator[LLMEvent]:
        raise ProviderError("primary unavailable")


class WorkingProvider:
    name = "secondary"

    def generate(self, request: LLMRequest) -> Iterator[LLMEvent]:
        yield LLMEvent(type="text_delta", text="fallback answer")
        yield LLMEvent(type="stop", stop_reason="stop")


def test_router_falls_back_to_next_provider():
    events = list(LLMRouter([FailingProvider(), WorkingProvider()]).generate(LLMRequest([])))
    assert [event.text for event in events if event.type == "text_delta"] == ["fallback answer"]


def test_llm_client_forwards_text_deltas(monkeypatch):
    from agent import llm_client

    monkeypatch.setattr(llm_client, "default_router", lambda: LLMRouter([WorkingProvider()]))
    streamed = []

    response = llm_client.call_llm([], [], sensitive=True, on_text_delta=streamed.append)

    assert streamed == ["fallback answer"]
    assert "".join(block.text for block in response.content) == "fallback answer"


def test_default_router_discovers_gemini_grok_and_anthropic_keys(monkeypatch):
    from agent.llm.router import default_router

    for key in (
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "GROK_API_KEY",
        "XAI_API_KEY",
        "GROQ_API_KEY",
        "OPENROUTER_API_KEY",
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test")
    monkeypatch.setenv("XAI_API_KEY", "xai-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test")
    monkeypatch.delenv("HSX_PROVIDER", raising=False)
    monkeypatch.delenv("HSX_FALLBACKS", raising=False)

    router = default_router()

    assert [provider.name for provider in router.providers] == ["anthropic", "gemini", "grok"]
    assert router.providers[2].api_key == "xai-test"


def test_default_router_requires_at_least_one_api_key(monkeypatch):
    from agent.llm.router import default_router

    for key in (
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "GROK_API_KEY",
        "XAI_API_KEY",
        "GROQ_API_KEY",
        "OPENROUTER_API_KEY",
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    try:
        default_router()
    except RuntimeError as error:
        assert str(error) == "No valid API key found in .env. Please configure at least one provider key."
    else:
        raise AssertionError("default_router should reject an empty provider configuration")
