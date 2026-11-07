import io
import json
import urllib.error

import pytest

from agent.llm.providers import GeminiProvider, ProviderError
from agent.llm.router import LLMRouter
from agent.llm.types import LLMRequest


def test_gemini_http_error_body_and_field_path_are_logged_redacted(monkeypatch, caplog, tmp_path):
    from agent.llm import providers

    error_log = tmp_path / "provider-errors.jsonl"
    monkeypatch.setattr(providers, "_PROVIDER_ERROR_LOG", error_log)

    def fail(_request, timeout):
        raise urllib.error.HTTPError(
            "https://example.test/generate",
            400,
            "Bad Request",
            {},
            io.BytesIO(json.dumps({"error": {
                "message": "Invalid schema",
                "details": [{"fieldViolations": [{
                    "field": "tools[0].functionDeclarations[0].parameters.properties.camera_record.properties.duration.type",
                    "description": "Missing required type.",
                }]}],
                "api_key": "secret-value",
            }}).encode()),
        )

    monkeypatch.setattr("urllib.request.urlopen", fail)
    provider = GeminiProvider("gemini-test", "https://example.test/generate", "secret-value")

    payload = {"contents": [{"role": "user", "parts": [{"text": "hello"}]}], "tools": [{
        "functionDeclarations": [{
            "name": "camera_record", "description": "Capture video.",
            "parameters": {"type": "object", "properties": {"duration": {"type": "integer"}}},
        }]
    }]}
    with pytest.raises(ProviderError) as error:
        provider._post(payload)

    assert "Gemini rejected tool 'camera_record'" in str(error.value)
    assert "parameter 'duration' has no type" in str(error.value)
    assert "secret-value" not in caplog.text
    assert "Invalid schema" in caplog.text
    record = json.loads(error_log.read_text(encoding="utf-8"))
    assert record["violations"][0]["path"].endswith("properties.duration.type")
    assert "secret-value" not in record["body"]


def test_gemini_local_schema_failure_names_tool_and_parameter(monkeypatch, tmp_path):
    from agent.llm import providers

    monkeypatch.setattr(providers, "_PROVIDER_ERROR_LOG", tmp_path / "provider-errors.jsonl")
    provider = GeminiProvider("gemini-test", "https://example.test/generate")
    payload = {
        "contents": [{"role": "user", "parts": [{"text": "hello"}]}],
        "tools": [{"functionDeclarations": [{
            "name": "camera_record",
            "description": "Capture video.",
            "parameters": {"type": "object", "properties": {"x": {}}},
        }]}],
    }
    with pytest.raises(ProviderError, match="Gemini rejected tool 'camera_record': parameter 'x' has no type"):
        provider._post(payload)


def test_router_reports_provider_failures_in_plain_english():
    class FailedProvider:
        name = "gemini"
        model = "test"

        def generate(self, _request):
            raise ProviderError("Gemini rejected the conversation format.")
            yield

    with pytest.raises(ProviderError) as error:
        list(LLMRouter([FailedProvider()]).generate(LLMRequest([])))

    lines = str(error.value).splitlines()
    assert len(lines) <= 3
    assert lines[0].startswith("Provider failure:")
    assert not any(token in str(error.value) for token in ("{", "}", "Role 'function'"))


def test_gemini_503_retries_twice_then_router_falls_back(monkeypatch):
    from agent.llm.types import LLMEvent

    calls = []
    delays = []

    def unavailable(request, timeout):
        calls.append((request.full_url, timeout))
        raise urllib.error.HTTPError(
            request.full_url,
            503,
            "Service Unavailable",
            {},
            io.BytesIO(b'{"error":{"message":"temporary outage"}}'),
        )

    class WorkingGroq:
        name = "groq"
        model = "test-model"

        def generate(self, _request):
            yield LLMEvent(type="text_delta", text="Fallback answer.")
            yield LLMEvent(type="stop")

    monkeypatch.setattr("urllib.request.urlopen", unavailable)
    monkeypatch.setattr("agent.llm.providers.time.sleep", delays.append)
    request = LLMRequest([{"role": "user", "content": "hello"}])
    router = LLMRouter([GeminiProvider("test-model", "http://gemini.test"), WorkingGroq()])

    events = list(router.generate(request))

    assert [event.text for event in events if event.type == "text_delta"] == ["Fallback answer."]
    assert len(calls) == 3
    assert delays == [0.1, 0.2]
    assert router.last_result["status"] == "Fallback to Groq"
