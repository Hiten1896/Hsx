from agent.llm.providers import OpenAICompatibleProvider
from agent.llm.types import LLMRequest


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
