import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agent import llm_client, orchestrator
from agent.llm.providers import GeminiProvider, OpenAICompatibleProvider, assert_payload_valid


class _FakeProviderHandler(BaseHTTPRequestHandler):
    provider = ""
    requests = []

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert_payload_valid(self.provider, payload)
        type(self).requests.append(payload)
        if len(type(self).requests) == 1:
            if self.provider == "gemini":
                body = {"candidates": [{"content": {"parts": [{
                    "functionCall": {"name": "echo", "args": {"value": "one"}}
                }]}}]}
            else:
                body = {"choices": [{"message": {"content": None, "tool_calls": [{
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "echo", "arguments": '{"value": "one"}'},
                }]}, "finish_reason": "tool_calls"}]}
        elif self.provider == "gemini":
            body = {"candidates": [{"content": {"parts": [{"text": "done"}]}}]}
        else:
            body = {"choices": [{"message": {"content": "done"}, "finish_reason": "stop"}]}
        encoded = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *args):
        pass


@pytest.mark.parametrize("provider_kind", ["gemini", "groq"])
def test_real_orchestrator_four_turn_provider_smoke(provider_kind, monkeypatch):
    handler = type(
        "ProviderHandler",
        (_FakeProviderHandler,),
        {"provider": "gemini" if provider_kind == "gemini" else "groq", "requests": []},
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        if provider_kind == "gemini":
            provider = GeminiProvider("test", f"http://127.0.0.1:{server.server_port}")
        else:
            provider = OpenAICompatibleProvider("groq", "test", "model", f"http://127.0.0.1:{server.server_port}")
        monkeypatch.setattr(llm_client, "default_router", lambda: type("Router", (), {"generate": provider.generate})())
        history = []
        tools = [{"name": "echo", "description": "Echo a value.", "risk": "safe", "input_schema": {
            "type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]
        }}]
        answers = []
        for turn in range(4):
            answer, history = orchestrator.run_agent(
                f"turn {turn}", history, tools, {"echo": lambda value: {"echo": value}}, sensitive=True
            )
            answers.append(answer)
        assert answers == ["done"] * 4
        assert len(handler.requests) == 5
        json.dumps(history)
    finally:
        server.shutdown()
        thread.join()
