from collections.abc import Iterator

from agent.llm.panel import PanelRouter
from agent.llm.types import LLMEvent, LLMRequest


class DraftProvider:
    def __init__(self, name: str, text: str):
        self.name = name
        self.text = text

    def generate(self, request: LLMRequest) -> Iterator[LLMEvent]:
        yield LLMEvent(type="text_delta", text=self.text)


def test_panel_debates_two_drafts_with_judge():
    class Judge(DraftProvider):
        def generate(self, request: LLMRequest) -> Iterator[LLMEvent]:
            assert "AGENT 1" in request.messages[0]["content"]
            assert "AGENT 2" in request.messages[0]["content"]
            yield LLMEvent(type="text_delta", text="polished answer")

    events = list(PanelRouter(
        [DraftProvider("gemini", "draft one"), DraftProvider("groq", "draft two")],
        Judge("openrouter", ""),
    ).generate(LLMRequest([{"role": "user", "content": "question"}])))
    assert events[0].text == "polished answer"
    assert events[-1].stop_reason == "panel_complete"


def test_panel_reports_provider_success_and_latency():
    outcomes = []
    panel = PanelRouter(
        [DraftProvider("gemini", "draft one"), DraftProvider("groq", "draft two")],
        DraftProvider("ollama", "final answer"),
    )
    panel.provider_result_callback = lambda name, succeeded, latency: outcomes.append(
        (name, succeeded, latency)
    )

    list(panel.generate(LLMRequest([{"role": "user", "content": "question"}])))

    assert {name for name, _, _ in outcomes} == {"gemini", "groq", "ollama"}
    assert all(succeeded and latency >= 0 for _, succeeded, latency in outcomes)
