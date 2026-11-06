import io
import urllib.error

import pytest

from agent.llm.providers import GeminiProvider, ProviderError
from agent.llm.router import LLMRouter
from agent.llm.types import LLMRequest


def test_gemini_http_error_body_is_logged_but_not_shown(monkeypatch, caplog):
    def fail(_request, timeout):
        raise urllib.error.HTTPError(
            "https://example.test/generate",
            400,
            "Bad Request",
            {},
            io.BytesIO(b'{"message":"Role function rejected","api_key":"secret-value"}'),
        )

    monkeypatch.setattr("urllib.request.urlopen", fail)
    provider = GeminiProvider("gemini-test", "https://example.test/generate", "secret-value")

    with pytest.raises(ProviderError) as error:
        list(provider.generate(LLMRequest([{"role": "user", "content": "hello"}])))

    assert str(error.value) == "Gemini rejected the conversation format."
    assert "secret-value" not in caplog.text
    assert "Role function rejected" in caplog.text


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
