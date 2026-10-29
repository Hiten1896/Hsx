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
