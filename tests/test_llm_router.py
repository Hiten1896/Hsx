import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agent.llm.providers import GeminiProvider, JSONHTTPProvider, OllamaProvider, OpenAICompatibleProvider, ProviderError
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


def test_router_falls_back_to_next_provider(caplog):
    events = list(LLMRouter([FailingProvider(), WorkingProvider()]).generate(LLMRequest([])))
    assert [event.text for event in events if event.type == "text_delta"] == ["fallback answer"]
    assert "LLM provider primary failed: primary unavailable" in caplog.text


def test_llm_client_forwards_text_deltas(monkeypatch):
    from agent import llm_client

    monkeypatch.setattr(llm_client, "default_router", lambda: LLMRouter([WorkingProvider()]))
    streamed = []

    response = llm_client.call_llm([], [], sensitive=True, on_text_delta=streamed.append)

    assert streamed == ["fallback answer"]
    assert "".join(block.text for block in response.content) == "fallback answer"


def test_llm_client_propagates_provider_failure_instead_of_using_local_fallback(monkeypatch):
    from agent import llm_client

    monkeypatch.setattr(llm_client, "default_router", lambda: LLMRouter([FailingProvider()]))

    with pytest.raises(ProviderError, match="primary unavailable"):
        llm_client.call_llm([], [], sensitive=True)


def test_default_router_discovers_configured_providers(monkeypatch):
    from agent.llm.router import default_router

    for key in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GROK_API_KEY", "XAI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test")
    monkeypatch.setenv("XAI_API_KEY", "xai-test")
    monkeypatch.delenv("HSX_PROVIDER", raising=False)
    monkeypatch.delenv("HSX_FALLBACKS", raising=False)

    router = default_router()

    assert [provider.name for provider in router.providers] == ["gemini", "grok"]
    assert "gemini-test" not in router.providers[0].endpoint
    assert "?key=" not in router.providers[0].endpoint
    assert router.providers[1].api_key == "xai-test"


def test_default_router_requires_a_valid_api_key(monkeypatch):
    from agent.llm.router import default_router

    for key in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GROK_API_KEY", "XAI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(key, raising=False)

    try:
        default_router()
    except RuntimeError as error:
        assert str(error) == "No valid API key found in .env. Please configure at least one provider key."
    else:
        raise AssertionError("default_router should reject an empty provider configuration")


def test_json_http_provider_sends_user_agent():
    received = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.update(self.headers)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"ok": true}')

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        provider = JSONHTTPProvider("test", f"http://127.0.0.1:{server.server_port}", "secret")
        assert provider._post({}) == {"ok": True}
        assert received["User-Agent"] == "Hsx/1.0 (+https://github.com/Hiten1896/Hsx)"
    finally:
        server.shutdown()
        thread.join()


def test_gemini_sends_key_header_and_maps_roles_and_system_instruction():
    received = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received["path"] = self.path
            received["headers"] = dict(self.headers)
            received["payload"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}')

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        provider = GeminiProvider("test", f"http://127.0.0.1:{server.server_port}/generateContent", "secret-key")
        events = list(provider.generate(LLMRequest(
            [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
            system="be concise",
        )))
        assert events[0].text == "ok"
        assert received["path"] == "/generateContent"
        assert received["headers"].get("X-Goog-Api-Key") == "secret-key"
        assert "authorization" not in {key.lower() for key in received["headers"]}
        assert "secret-key" not in received["path"]
        assert received["payload"] == {
            "contents": [
                {"role": "user", "parts": [{"text": "hi"}]},
                {"role": "model", "parts": [{"text": "hello"}]},
            ],
            "systemInstruction": {"parts": [{"text": "be concise"}]},
        }
    finally:
        server.shutdown()
        thread.join()


@pytest.mark.parametrize("provider_kind", ["gemini", "groq", "ollama"])
def test_provider_completes_tool_call_loop_with_local_http_server(provider_kind):
    received = []
    responses = {
        "gemini": [
            {"candidates": [{"content": {"parts": [{"functionCall": {"name": "search", "args": {"query": "news"}}}]}}]},
            {"candidates": [{"content": {"parts": [{"text": "Here is the result."}]}}]},
        ],
        "groq": [
            {"choices": [{"message": {"tool_calls": [{
                "id": "call-1",
                "type": "function",
                "function": {"name": "search", "arguments": '{"query":"news"}'},
            }]}, "finish_reason": "tool_calls"}]},
            {"choices": [{"message": {"content": "Here is the result."}, "finish_reason": "stop"}]},
        ],
        "ollama": [
            {"message": {"tool_calls": [{
                "id": "call-1",
                "function": {"name": "search", "arguments": {"query": "news"}},
            }]}},
            {"message": {"content": "Here is the result."}},
        ],
    }

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            body = json.dumps(responses[provider_kind][len(received) - 1]).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        endpoint = f"http://127.0.0.1:{server.server_port}"
        if provider_kind == "gemini":
            provider = GeminiProvider("test", endpoint, "secret")
        elif provider_kind == "ollama":
            provider = OllamaProvider("test", endpoint)
        else:
            provider = OpenAICompatibleProvider("groq", "secret", "test", endpoint)

        history = [{"role": "user", "content": "Find recent news."}]
        first_events = list(provider.generate(LLMRequest(history, tools=[{
            "name": "search",
            "description": "Search the web",
            "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}},
        }])))
        tool_call = next(event for event in first_events if event.type == "tool_call")
        history.append({
            "role": "assistant",
            "content": [{
                "type": "tool_use",
                "id": tool_call.tool_call_id,
                "name": tool_call.tool_name,
                "input": tool_call.tool_input,
            }],
        })
        history.append({
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": tool_call.tool_call_id,
                "content": '{"results":["A news story"]}',
            }],
        })
        final_events = list(provider.generate(LLMRequest(history, tools=[{
            "name": "search",
            "description": "Search the web",
            "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}},
        }])))

        assert [event.text for event in final_events if event.type == "text_delta"] == ["Here is the result."]
        assert len(received) == 2
        json.dumps(history)
        if provider_kind == "gemini":
            contents = received[1]["contents"]
            assert contents[1] == {
                "role": "model",
                "parts": [{"functionCall": {"name": "search", "args": {"query": "news"}}}],
            }
            assert contents[2] == {
                "role": "function",
                "parts": [{"functionResponse": {"name": "search", "response": {"results": ["A news story"]}}}],
            }
        else:
            messages = received[1]["messages"]
            assistant = next(message for message in messages if message.get("tool_calls"))
            tool_result = next(message for message in messages if message["role"] == "tool")
            assert assistant["tool_calls"][0] == {
                "id": "call-1",
                "type": "function",
                "function": {"name": "search", "arguments": '{"query": "news"}'},
            }
            assert tool_result == {
                "role": "tool",
                "tool_call_id": "call-1",
                "content": '{"results":["A news story"]}',
            }
            if provider_kind == "ollama":
                assert received[1]["stream"] is False
    finally:
        server.shutdown()
        thread.join()


def test_provider_error_includes_status_body_and_redacts_api_key():
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(403)
            self.end_headers()
            self.wfile.write(b'{"error":"bad secret-key and Bearer secret-key"}')

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        provider = JSONHTTPProvider("test", f"http://127.0.0.1:{server.server_port}", "secret-key")
        with pytest.raises(ProviderError) as raised:
            provider._post({})
        message = str(raised.value)
        assert "status 403" in message
        assert "response body:" in message
        assert "secret-key" not in message
        assert "[REDACTED]" in message
    finally:
        server.shutdown()
        thread.join()
