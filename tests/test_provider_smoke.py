import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agent import llm_client, orchestrator
from agent.ask_user import AskUserRequest
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


@pytest.mark.parametrize("provider_kind", ["gemini", "groq"])
def test_system_info_tool_call_returns_live_tool_output(provider_kind, monkeypatch):
    class Handler(BaseHTTPRequestHandler):
        requests = []

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert_payload_valid(provider_kind, payload)
            type(self).requests.append(payload)
            if len(type(self).requests) == 1:
                if provider_kind == "gemini":
                    body = {"candidates": [{"content": {"parts": [{
                        "functionCall": {"name": "system_info", "args": {}}
                    }]}}]}
                else:
                    body = {"choices": [{"message": {"content": None, "tool_calls": [{
                        "id": "sys-call", "type": "function",
                        "function": {"name": "system_info", "arguments": "{}"},
                    }]}, "finish_reason": "tool_calls"}]}
            else:
                body = ({"candidates": [{"content": {"parts": [{"text": "The CPU model is Test CPU."}]}}]}
                        if provider_kind == "gemini" else
                        {"choices": [{"message": {"content": "The CPU model is Test CPU."}, "finish_reason": "stop"}]})
            encoded = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *_args):
            pass

    Handler.requests = []
    handler = Handler
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        provider = (
            GeminiProvider("test", f"http://127.0.0.1:{server.server_port}")
            if provider_kind == "gemini"
            else OpenAICompatibleProvider("groq", "test", "model", f"http://127.0.0.1:{server.server_port}")
        )
        monkeypatch.setattr(llm_client, "default_router", lambda: type("Router", (), {"generate": provider.generate})())
        monkeypatch.setattr(orchestrator, "INTENT_ROUTER", type("Router", (), {
            "predict_with_confidence": lambda _self, _prompt: ("system_info", 0.5),
        })())
        monkeypatch.setattr(orchestrator.MEMORY, "prompt_context", lambda _prompt: "")
        monkeypatch.setattr(orchestrator.MEMORY, "record_message", lambda *_args: None)
        tools = [{"name": "system_info", "description": "Get current machine specs.", "risk": "safe",
                  "input_schema": {"type": "object", "properties": {}, "required": []}}]
        answer, history = orchestrator.run_agent(
            "What are this machine's specs?", [], tools,
            {"system_info": lambda: {"success": True, "cpu": {"model": "Test CPU"}}},
            sensitive=True,
        )
        assert answer == "The CPU model is Test CPU."
        assert json.loads(history[2]["content"][0]["content"]) == {
            "success": True, "cpu": {"model": "Test CPU"}
        }
        assert len(Handler.requests) == 2
    finally:
        server.shutdown()
        thread.join()


class _FileConversationHandler(BaseHTTPRequestHandler):
    provider = ""
    requests = []

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert_payload_valid(self.provider, payload)
        if self.provider == "gemini":
            assert all(item["role"] in {"user", "model"} for item in payload["contents"])
            parameters = payload["tools"][0]["functionDeclarations"][2]["parameters"]
            assert parameters["properties"]["filenames"]["items"] == {"type": "string"}
        else:
            assert all(item["role"] in {"system", "user", "assistant", "tool"} for item in payload["messages"])
            parameters = payload["tools"][2]["function"]["parameters"]
            assert parameters["properties"]["filenames"]["items"] == {"type": "string"}
        type(self).requests.append(payload)
        call_index = len(type(self).requests)
        if call_index == 1:
            if self.provider == "gemini":
                body = {"candidates": [{"content": {"parts": [{
                    "functionCall": {
                        "name": "ask_user",
                        "args": {"question": "What text should I write?"},
                        "id": "ask-call",
                    },
                    "thoughtSignature": "ask-signature",
                }]}}]}
            else:
                body = {"choices": [{"message": {"content": None, "tool_calls": [{
                    "id": "ask-call",
                    "type": "function",
                    "function": {"name": "ask_user", "arguments": '{"question":"What text should I write?"}'},
                }]}, "finish_reason": "tool_calls"}]}
        elif call_index == 2:
            if self.provider == "gemini":
                body = {"candidates": [{"content": {"parts": [{
                    "functionCall": {
                        "name": "create_text_file",
                        "args": {"path": "note.txt", "content": "hello"},
                        "id": "create-call",
                    },
                    "thoughtSignature": "create-signature",
                }]}}]}
            else:
                body = {"choices": [{"message": {"content": None, "tool_calls": [{
                    "id": "create-call",
                    "type": "function",
                    "function": {
                        "name": "create_text_file",
                        "arguments": '{"path":"note.txt","content":"hello"}',
                    },
                }]}, "finish_reason": "tool_calls"}]}
        else:
            if self.provider == "gemini":
                body = {"candidates": [{"content": {"parts": [{"text": "Created note.txt."}]}}]}
            else:
                body = {"choices": [{"message": {"content": "Created note.txt."}, "finish_reason": "stop"}]}
        encoded = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *args):
        pass


@pytest.mark.parametrize("provider_kind", ["gemini", "groq"])
def test_file_creation_conversation_uses_native_tool_result_roles(provider_kind, monkeypatch):
    handler = type(
        "FileConversationHandler",
        (_FileConversationHandler,),
        {"provider": provider_kind, "requests": []},
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    created = []
    try:
        if provider_kind == "gemini":
            provider = GeminiProvider("fake", f"http://127.0.0.1:{server.server_port}")
        else:
            provider = OpenAICompatibleProvider(
                "groq", "fake", "live-model", f"http://127.0.0.1:{server.server_port}"
            )
        monkeypatch.setattr(
            llm_client,
            "default_router",
            lambda: type("Router", (), {"generate": provider.generate})(),
        )
        tools = [
            {"name": "ask_user", "description": "Ask a question.", "risk": "safe", "input_schema": {
                "type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]
            }},
            {"name": "create_text_file", "description": "Create a workspace text file.", "risk": "safe", "input_schema": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            }},
            {"name": "compress_files", "description": "Create an archive.", "risk": "safe", "input_schema": {
                "type": "object",
                "properties": {"filenames": {"type": "array"}},
                "required": ["filenames"],
            }},
        ]

        def create_text_file(path, content):
            created.append((path, content))
            return {"success": True, "path": path}

        history = []
        with pytest.raises(AskUserRequest) as paused:
            orchestrator.run_agent(
                "Create note.txt in the workspace.",
                history,
                tools,
                {
                    "ask_user": lambda question: (_ for _ in ()).throw(AskUserRequest(question)),
                    "create_text_file": create_text_file,
                    "compress_files": lambda **_kwargs: {},
                },
                sensitive=True,
                session_id=f"file-{provider_kind}",
            )
        assert paused.value.question == "What text should I write?"
        answer, resumed_history = orchestrator.run_agent(
            "Create note.txt in the workspace.",
            paused.value.checkpoint,
            tools,
            {
                "ask_user": lambda question: (_ for _ in ()).throw(AskUserRequest(question)),
                "create_text_file": create_text_file,
                "compress_files": lambda **_kwargs: {},
            },
            sensitive=True,
            session_id=f"file-{provider_kind}",
            resume_tool_result={"tool_call_id": paused.value.tool_call_id, "answer": "hello"},
        )
        assert answer == "Created note.txt."
        assert created == [("note.txt", "hello")]
        assert len(handler.requests) == 3
        assert json.loads(json.dumps(resumed_history)) == resumed_history
        if provider_kind == "gemini":
            assert handler.requests[1]["contents"][2]["parts"][0]["functionResponse"]["name"] == "ask_user"
            assert handler.requests[1]["contents"][1]["parts"][0]["thoughtSignature"] == "ask-signature"
        else:
            tool_result = next(
                message for message in handler.requests[1]["messages"]
                if message["role"] == "tool"
            )
            assert tool_result == {
                "role": "tool", "tool_call_id": "ask-call", "content": '{"success": true, "answer": "hello"}'
            }
    finally:
        server.shutdown()
        thread.join()
