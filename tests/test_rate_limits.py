import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agent import orchestrator
from agent.llm.context import compact_messages, flatten_tool_blocks, has_tool_blocks
from agent.llm.providers import OpenAICompatibleProvider, ProviderError
from agent.llm.ratelimit import (
    ProviderCooldowns,
    RateBudget,
    cooldown_for_error,
    parse_duration,
    parse_retry_after,
)
from agent.llm.router import LLMRouter
from agent.llm.tool_select import NEED_TOOLS_SENTINEL, SentinelGuard, select_tools
from agent.llm.types import LLMEvent, LLMRequest
from agent.llm_client import NormalizedBlock, NormalizedResponse


# ---------------------------------------------------------------- parsing and cooldown rules
def test_parse_duration_formats():
    assert parse_duration("2m59.56s") == pytest.approx(179.56)
    assert parse_duration("7.66s") == pytest.approx(7.66)
    assert parse_duration("500ms") == pytest.approx(0.5)
    assert parse_duration("30") == 30.0
    assert parse_duration("1h2m") == 3720.0
    assert parse_duration("soon") is None


def test_parse_retry_after_sources():
    assert parse_retry_after({"Retry-After": "12"}) == 12.0
    groq_tokens = {"x-ratelimit-remaining-requests": "5", "x-ratelimit-reset-tokens": "7.66s", "x-ratelimit-reset-requests": "14m"}
    assert parse_retry_after(groq_tokens) == pytest.approx(7.66)
    groq_daily = {"x-ratelimit-remaining-requests": "0", "x-ratelimit-reset-requests": "14m24s", "x-ratelimit-reset-tokens": "1s"}
    assert parse_retry_after(groq_daily) == pytest.approx(864.0)
    assert parse_retry_after({}, '{"error": {"details": [{"retryDelay": "38s"}]}}') == 38.0
    assert parse_retry_after({}, "Rate limit reached. Please try again in 7.66s.") == pytest.approx(7.66)
    assert parse_retry_after({}, "nothing useful") is None


def test_cooldown_for_error_classes():
    assert cooldown_for_error(ProviderError("x", status=429, retry_after=45)) == (45.0, "rate limited")
    seconds, _ = cooldown_for_error(ProviderError("quota exceeded per day", status=429))
    assert seconds == 3600.0
    assert cooldown_for_error(ProviderError("x", status=429))[0] == 60.0
    assert cooldown_for_error(ProviderError("x", status=401))[0] == 300.0
    assert cooldown_for_error(ProviderError("x", status=503))[0] == 30.0
    assert cooldown_for_error(ValueError("bad payload"))[0] == 0.0


def test_provider_cooldowns_expire():
    now = [100.0]
    cooldowns = ProviderCooldowns(clock=lambda: now[0])
    cooldowns.mark("groq", 30, "rate limited")
    assert cooldowns.remaining("groq") == 30
    now[0] = 131.0
    assert cooldowns.remaining("groq") == 0.0
    assert cooldowns.snapshot() == {}


def test_rate_budget_window_and_oversized_request():
    now = [0.0]
    budget = RateBudget.from_env("groq:rpm=2,tpm=1000", )
    budget._clock = lambda: now[0]
    assert budget.wait_needed("groq", 400) == 0
    budget.record("groq", 400)
    budget.record("groq", 400)
    assert budget.wait_needed("groq", 100) == pytest.approx(60.0)  # rpm=2 reached
    assert budget.wait_needed("groq", 5000) == float("inf")  # can never fit
    now[0] = 61.0
    assert budget.wait_needed("groq", 400) == 0
    assert budget.wait_needed("gemini", 10**6) == 0  # no limit configured


# ---------------------------------------------------------------- router behaviour
class FakeProvider:
    def __init__(self, name, error=None, text="ok"):
        self.name, self.error, self.text, self.calls = name, error, text, 0

    def generate(self, request):
        self.calls += 1
        if self.error:
            raise self.error
        yield LLMEvent(type="text_delta", text=self.text)
        yield LLMEvent(type="stop", stop_reason="stop")


def _texts(router, request):
    return "".join(event.text for event in router.generate(request) if event.type == "text_delta")


def test_rate_limited_provider_is_skipped_until_its_cooldown_ends():
    now = [0.0]
    cooldowns = ProviderCooldowns(clock=lambda: now[0])
    limited = FakeProvider("gemini", ProviderError("slow down", status=429, retry_after=30))
    backup = FakeProvider("groq", text="from groq")
    router = LLMRouter([limited, backup], cooldowns=cooldowns, budget=RateBudget())
    request = LLMRequest([{"role": "user", "content": "hi"}])
    assert _texts(router, request) == "from groq"
    assert limited.calls == 1
    assert _texts(router, request) == "from groq"
    assert limited.calls == 1  # not retried while paused
    now[0] = 31.0
    limited.error = None
    limited.text = "gemini again"
    assert _texts(router, request) == "gemini again"


def test_paused_providers_are_still_tried_as_a_last_resort():
    cooldowns = ProviderCooldowns()
    only = FakeProvider("gemini", text="still answers")
    cooldowns.mark("gemini", 300, "rate limited")
    router = LLMRouter([only], cooldowns=cooldowns, budget=RateBudget())
    assert _texts(router, LLMRequest([{"role": "user", "content": "hi"}])) == "still answers"


def test_over_budget_provider_goes_last():
    budget = RateBudget({"groq": {"tpm": 10}})
    groq, gemini = FakeProvider("groq", text="groq"), FakeProvider("gemini", text="gemini")
    router = LLMRouter([groq, gemini], cooldowns=ProviderCooldowns(), budget=budget)
    big = LLMRequest([{"role": "user", "content": "x" * 400}])
    assert _texts(router, big) == "gemini"
    assert groq.calls == 0


def test_streaming_provider_is_passed_through_live():
    release = threading.Event()
    seen = []

    class Streaming:
        name, streams = "streamer", True

        def generate(self, request):
            yield LLMEvent(type="text_delta", text="first ")
            assert release.wait(5)
            yield LLMEvent(type="text_delta", text="second")
            yield LLMEvent(type="stop", stop_reason="stop")

    router = LLMRouter([Streaming()], cooldowns=ProviderCooldowns(), budget=RateBudget())
    stream = router.generate(LLMRequest([{"role": "user", "content": "hi"}], stream=True))
    seen.append(next(stream).text)  # arrives before the provider has finished
    release.set()
    seen.extend(event.text for event in stream if event.type == "text_delta")
    assert seen == ["first ", "second"]


def test_mid_stream_failure_raises_instead_of_mixing_providers():
    class Broken:
        name, streams = "streamer", True

        def generate(self, request):
            yield LLMEvent(type="text_delta", text="partial")
            raise ProviderError("connection dropped", provider="streamer")

    backup = FakeProvider("groq", text="never used")
    router = LLMRouter([Broken(), backup], cooldowns=ProviderCooldowns(), budget=RateBudget())
    with pytest.raises(ProviderError, match="stopped mid-answer"):
        list(router.generate(LLMRequest([{"role": "user", "content": "hi"}], stream=True)))
    assert backup.calls == 0


# ---------------------------------------------------------------- provider 429 handling
def test_provider_429_carries_retry_after_from_the_response_headers():
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(429)
            self.send_header("Retry-After", "17")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        provider = OpenAICompatibleProvider("groq", "secret", "m", f"http://127.0.0.1:{server.server_port}")
        with pytest.raises(ProviderError) as caught:
            list(provider.generate(LLMRequest([{"role": "user", "content": "hi"}])))
    finally:
        server.shutdown()
    assert caught.value.status == 429 and caught.value.retry_after == 17.0


# ---------------------------------------------------------------- history compaction
def _tool_turn(index, size):
    return [
        {"role": "assistant", "content": [{"type": "tool_use", "id": f"t{index}", "name": "read_file", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"t{index}", "content": "x" * size}]},
    ]


def test_compaction_shortens_old_tool_results_but_not_recent_turns():
    history = [{"role": "user", "content": "first question"}, *_tool_turn(1, 20000)]
    history += [{"role": "assistant", "content": "answer"}, {"role": "user", "content": "q2"}, *_tool_turn(2, 20000)]
    compacted = compact_messages(history, keep_recent=2, max_tokens=10**9)
    assert len(compacted[2]["content"][0]["content"]) < 3000  # old result trimmed
    assert len(compacted[-1]["content"][0]["content"]) == 20000  # recent result untouched
    assert len(history[2]["content"][0]["content"]) == 20000  # stored history never mutated


def test_compaction_drops_whole_old_turns_without_splitting_tool_pairs():
    history = []
    for turn in range(6):
        history.append({"role": "user", "content": f"question {turn} " + "y" * 4000})
        history.extend(_tool_turn(turn, 100))
        history.append({"role": "assistant", "content": [{"type": "text", "text": "done"}]})
    compacted = compact_messages(history, max_tokens=2500)
    assert compacted[0]["role"] == "user"
    assert compacted[-1] == history[-1]
    assert "trimmed" in json.dumps(compacted[0])
    uses = {b["id"] for m in compacted if isinstance(m["content"], list) for b in m["content"] if b.get("type") == "tool_use"}
    results = {b["tool_use_id"] for m in compacted if isinstance(m["content"], list) for b in m["content"] if b.get("type") == "tool_result"}
    assert uses == results


def test_flatten_tool_blocks_removes_every_tool_reference():
    history = [{"role": "user", "content": "go"}, *_tool_turn(1, 50), {"role": "assistant", "content": [{"type": "text", "text": "ok"}]}]
    assert has_tool_blocks(history)
    flat = flatten_tool_blocks(history)
    assert not has_tool_blocks(flat)
    assert "read_file" in json.dumps(flat)


# ---------------------------------------------------------------- tool selection
def _tool(name, description, props=()):
    return {"name": name, "description": description,
            "input_schema": {"type": "object", "properties": {p: {"type": "string"} for p in props}, "required": []}}


TOOLS = [
    _tool("ask_user", "Ask the user a question with choices", ["question"]),
    _tool("list_files", "List files in the shared workspace folder", ["path"]),
    _tool("read_file", "Read the contents of a file", ["path"]),
    _tool("web_search", "Search the web for current news and facts", ["query"]),
    _tool("system_info", "CPU memory disk and operating system facts"),
    _tool("music_play", "Play a song or audio track", ["track"]),
    _tool("make_docx", "Create a Word document report", ["title", "body"]),
]


def test_plain_conversation_sends_no_tools():
    for text in ("hello there", "thanks, that makes sense", "why is the sky blue"):
        selection = select_tools(TOOLS, text, [])
        assert selection.tier == "chat" and selection.tools == [], text


def test_action_request_sends_only_relevant_tools():
    selection = select_tools(TOOLS, "list the files in my workspace", [])
    names = [t["name"] for t in selection.tools]
    assert selection.tier == "action"
    assert "list_files" in names and "ask_user" in names
    assert "music_play" not in names


def test_router_prediction_and_recent_tools_are_kept():
    selection = select_tools(TOOLS, "ok do it", [], predicted_tool="music_play", confidence=0.8)
    assert "music_play" in [t["name"] for t in selection.tools]
    history = [
        {"role": "user", "content": "read notes.txt"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "1", "name": "read_file", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "1", "content": "text"}]},
    ]
    follow_up = select_tools(TOOLS, "and then summarise it", history)
    assert follow_up.tier == "action" and "read_file" in [t["name"] for t in follow_up.tools]


def test_escalation_and_switch_send_everything(monkeypatch):
    assert select_tools(TOOLS, "hello", [], escalated=True).tools == TOOLS
    monkeypatch.setenv("HSX_TOOL_SELECTION", "0")
    assert select_tools(TOOLS, "hello", []).tools == TOOLS


def test_real_skills_shrink_tool_tokens_a_lot():
    from agent.skill_loader import load_skills

    tools, _ = load_skills("skills")
    full = len(json.dumps(tools)) // 4
    chat = select_tools(tools, "good morning, how are you?", [])
    action = select_tools(tools, "what is the disk usage on this machine?", [])
    assert chat.tools == []
    assert action.tier == "action" and len(json.dumps(action.tools)) // 4 < full * 0.5


def test_sentinel_guard_swallows_the_sentinel_but_streams_real_answers():
    out = []
    guard = SentinelGuard(out.append)
    for piece in ("[[NEED", "_TOOLS", "]]"):
        guard(piece)
    guard.finish()
    assert out == []
    out2 = []
    guard = SentinelGuard(out2.append)
    for piece in ("Sure", ", here", " you go"):
        guard(piece)
    assert "".join(out2) == "Sure, here you go"
    out3 = []
    guard = SentinelGuard(out3.append)
    guard("[[")
    guard.finish()
    assert out3 == ["[["]


# ---------------------------------------------------------------- orchestrator integration
class _Intent:
    def predict_with_confidence(self, prompt):
        return "", 0.0


def _agent(monkeypatch, responses):
    monkeypatch.setattr(orchestrator, "INTENT_ROUTER", _Intent())
    monkeypatch.setattr(orchestrator.MEMORY, "prompt_context", lambda prompt: "")
    monkeypatch.setattr(orchestrator.MEMORY, "record_message", lambda *args: None)
    monkeypatch.setattr(orchestrator.POLICY, "is_stopped", lambda: False)
    monkeypatch.setattr(orchestrator.POLICY, "decide", lambda *args: {"allowed": True})
    sent = []

    def fake_call_llm(messages, tools, *args, **kwargs):
        sent.append({"tools": [t["name"] for t in tools], "minimal": kwargs.get("minimal_system", False)})
        return NormalizedResponse([NormalizedBlock(type="text", text=responses[len(sent) - 1])])

    monkeypatch.setattr(orchestrator, "call_llm", fake_call_llm)
    return sent


def test_chat_prompt_goes_out_with_no_tools_and_short_prompt(monkeypatch):
    sent = _agent(monkeypatch, ["Hello! How can I help?"])
    deltas = []
    answer, _ = orchestrator.run_agent("hello there", [], TOOLS, {}, on_text_delta=deltas.append, track_intent=False)
    assert sent == [{"tools": [], "minimal": True}]
    assert answer == "Hello! How can I help?"
    assert "".join(deltas) in {"", "Hello! How can I help?"}


def test_need_tools_reply_retries_once_with_the_full_tool_set(monkeypatch):
    sent = _agent(monkeypatch, [NEED_TOOLS_SENTINEL, "Done."])
    answer, history = orchestrator.run_agent("tell me something", [], TOOLS, {}, track_intent=False)
    assert [s["minimal"] for s in sent] == [True, False]
    assert len(sent[1]["tools"]) == len(TOOLS)
    assert answer == "Done."
    assert NEED_TOOLS_SENTINEL not in json.dumps(history)
