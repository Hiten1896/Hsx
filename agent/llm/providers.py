import json
import urllib.request
from typing import Any

from .types import EventStream, LLMEvent, LLMRequest


class ProviderError(RuntimeError):
    pass


class Provider:
    name = "provider"

    def generate(self, request: LLMRequest) -> EventStream:
        raise NotImplementedError


class AnthropicProvider(Provider):
    name = "anthropic"

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    def generate(self, request: LLMRequest) -> EventStream:
        import anthropic

        client = anthropic.Anthropic(api_key=self.api_key)
        response = client.messages.create(
            model=self.model,
            max_tokens=2048,
            system=request.system,
            messages=request.messages,
            tools=request.tools,
        )
        for block in response.content:
            if getattr(block, "type", "") == "text":
                yield LLMEvent(type="text_delta", text=block.text)
            elif getattr(block, "type", "") == "tool_use":
                yield LLMEvent(type="tool_call", tool_name=block.name, tool_input=block.input)
        yield LLMEvent(
            type="stop",
            stop_reason=getattr(response, "stop_reason", "") or "",
            usage={
                key: int(value)
                for key, value in getattr(getattr(response, "usage", None), "__dict__", {}).items()
                if isinstance(value, int)
            },
        )


class JSONHTTPProvider(Provider):
    endpoint = ""

    def __init__(self, model: str, endpoint: str, api_key: str = ""):
        self.model = model
        self.endpoint = endpoint
        self.api_key = api_key

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(
            self.endpoint, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read())
        except (OSError, json.JSONDecodeError) as error:
            raise ProviderError(f"{self.name} request failed: {error}") from error


class GeminiProvider(JSONHTTPProvider):
    name = "gemini"

    def generate(self, request: LLMRequest) -> EventStream:
        messages = ([{"role": "user", "content": request.system}] if request.system else []) + request.messages
        payload = {"contents": [{"role": message["role"], "parts": [{"text": message["content"]}]} for message in messages]}
        data = self._post(payload)
        text = data["candidates"][0]["content"]["parts"][0].get("text", "")
        yield LLMEvent(type="text_delta", text=text)
        yield LLMEvent(type="stop", stop_reason="stop")


class OllamaProvider(JSONHTTPProvider):
    name = "ollama"

    def generate(self, request: LLMRequest) -> EventStream:
        data = self._post({"model": self.model, "messages": request.messages, "stream": False})
        message = data.get("message", {})
        yield LLMEvent(type="text_delta", text=message.get("content", ""))
        yield LLMEvent(type="stop", stop_reason="stop")
