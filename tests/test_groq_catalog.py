import io
import json
import urllib.error

import pytest

from agent.llm.providers import GroqProvider, ProviderError
from agent.llm.types import LLMRequest


class _Response:
    status = 200

    def __init__(self, value):
        self.value = json.dumps(value).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.value


def test_groq_model_is_selected_from_live_catalog(monkeypatch, capsys):
    GroqProvider._catalog_cache.clear()
    GroqProvider._reported_missing.clear()
    seen = []

    def fake_urlopen(request, timeout):
        seen.append((request.full_url, request.get_header("Authorization")))
        return _Response({"data": [
            {"id": "z-chat"},
            {"id": "whisper-transcriber"},
            {"id": "retired-chat", "active": False},
            {"id": "a-chat"},
        ]})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    provider = GroqProvider("groq-test-key", "retired-model", models_url="https://test/models")

    assert provider.model == "a-chat"
    assert seen == [("https://test/models", "Bearer groq-test-key")]
    assert capsys.readouterr().out == (
        "Groq model retired-model not found. Available: a-chat, z-chat. "
        "Set GROQ_MODEL in .env.\n"
    )


def test_groq_requires_an_active_text_chat_model():
    assert GroqProvider._safe_default(["llama-text", "whisper-large", "retired-chat"]) == "llama-text"
    assert GroqProvider._safe_default(["whisper-large", "embedding-v2"]) == ""


def test_groq_explains_one_line_model_fix_when_catalog_has_no_chat_models(monkeypatch, capsys):
    GroqProvider._catalog_cache.clear()

    def fake_urlopen(_request, timeout):
        return _Response({"data": [
            {"id": "whisper-large", "active": True},
            {"id": "retired-chat", "active": False},
        ]})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with pytest.raises(ProviderError, match=r"Set GROQ_MODEL in \.env"):
        GroqProvider("no-chat-key", "old-model", models_url="https://test/models")
    assert capsys.readouterr().out.startswith("Groq has no active text chat model. Set GROQ_MODEL in .env")


def test_groq_refreshes_catalog_after_model_404_and_retries(monkeypatch, capsys):
    GroqProvider._catalog_cache.clear()
    GroqProvider._reported_missing.clear()
    calls = []

    def fake_urlopen(request, timeout):
        calls.append((request.get_method(), request.full_url))
        if request.get_method() == "GET":
            catalog = ["old-chat"] if calls.count(("GET", "https://test/models")) == 1 else ["new-chat"]
            return _Response({"data": [{"id": name} for name in catalog]})
        if len([call for call in calls if call[0] == "POST"]) == 1:
            raise urllib.error.HTTPError(
                request.full_url,
                404,
                "Not Found",
                {},
                io.BytesIO(b'{"error":{"code":"model_not_found"}}'),
            )
        return _Response({"choices": [{"message": {"content": "ready"}, "finish_reason": "stop"}]})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    provider = GroqProvider("groq-test-key", "old-chat", endpoint="https://test/chat", models_url="https://test/models")
    events = list(provider.generate(LLMRequest([{"role": "user", "content": "hello"}])))

    assert provider.model == "new-chat"
    assert any(event.text == "ready" for event in events)
    assert [call[0] for call in calls] == ["GET", "POST", "GET", "POST"]
    assert capsys.readouterr().out.endswith("Set GROQ_MODEL in .env.\n")
