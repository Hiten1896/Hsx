import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

from .types import EventStream, LLMEvent, LLMRequest, to_plain_data


class ProviderError(RuntimeError):
    pass


class Provider:
    name = "provider"

    def generate(self, request: LLMRequest) -> EventStream:
        raise NotImplementedError


def _shape_error(provider: str, path: str, key: str, detail: str = "") -> ValueError:
    suffix = f": {detail}" if detail else ""
    return ValueError(f"{provider} payload invalid at {path}; offending key {key!r}{suffix}")


def _require_keys(provider: str, value: dict, path: str, required: set[str], allowed: set[str]) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise _shape_error(provider, path, sorted(unknown)[0])
    missing = required - set(value)
    if missing:
        raise _shape_error(provider, path, sorted(missing)[0], "required key missing")


def _validate_gemini_payload(payload: dict[str, Any]) -> None:
    provider = "gemini"
    _require_keys(provider, payload, "$", {"contents"}, {"contents", "systemInstruction", "tools"})
    if not isinstance(payload["contents"], list):
        raise _shape_error(provider, "contents", "contents", "must be a list")
    for index, content in enumerate(payload["contents"]):
        path = f"contents[{index}]"
        if not isinstance(content, dict):
            raise _shape_error(provider, path, "content", "must be an object")
        _require_keys(provider, content, path, {"role", "parts"}, {"role", "parts"})
        if content["role"] not in {"user", "model"}:
            raise _shape_error(provider, f"{path}.role", "role", "must be user or model")
        if not isinstance(content["parts"], list):
            raise _shape_error(provider, f"{path}.parts", "parts", "must be a list")
        for part_index, part in enumerate(content["parts"]):
            part_path = f"{path}.parts[{part_index}]"
            if not isinstance(part, dict):
                raise _shape_error(provider, part_path, "part", "must contain exactly one native part")
            unknown = set(part) - {"text", "functionCall", "functionResponse"}
            if unknown:
                raise _shape_error(provider, part_path, sorted(unknown)[0])
            if len(part) != 1:
                raise _shape_error(provider, part_path, "part", "must contain exactly one native part")
            kind = next(iter(part))
            if kind == "text":
                if not isinstance(part["text"], str):
                    raise _shape_error(provider, part_path, "text", "must be a string")
            elif kind == "functionCall":
                function = part[kind]
                if not isinstance(function, dict):
                    raise _shape_error(provider, part_path, kind, "must be an object")
                _require_keys(provider, function, f"{part_path}.functionCall", {"name", "args"}, {"name", "args"})
                if not isinstance(function["name"], str) or not isinstance(function["args"], dict):
                    raise _shape_error(provider, part_path, kind, "invalid name or args")
            elif kind == "functionResponse":
                function = part[kind]
                if not isinstance(function, dict):
                    raise _shape_error(provider, part_path, kind, "must be an object")
                _require_keys(provider, function, f"{part_path}.functionResponse", {"name", "response"}, {"name", "response"})
                if not isinstance(function["name"], str) or not isinstance(function["response"], dict):
                    raise _shape_error(provider, part_path, kind, "invalid name or response")
            else:
                raise _shape_error(provider, part_path, kind)
    if "systemInstruction" in payload:
        instruction = payload["systemInstruction"]
        _require_keys(provider, instruction, "systemInstruction", {"parts"}, {"parts"})
        if not isinstance(instruction["parts"], list):
            raise _shape_error(provider, "systemInstruction.parts", "parts", "must be a list")
        for index, part in enumerate(instruction["parts"]):
            _require_keys(provider, part, f"systemInstruction.parts[{index}]", {"text"}, {"text"})
    if "tools" in payload:
        if not isinstance(payload["tools"], list):
            raise _shape_error(provider, "tools", "tools", "must be a list")
        for index, tool in enumerate(payload["tools"]):
            path = f"tools[{index}]"
            _require_keys(provider, tool, path, {"functionDeclarations"}, {"functionDeclarations"})
            if not isinstance(tool["functionDeclarations"], list):
                raise _shape_error(provider, f"{path}.functionDeclarations", "functionDeclarations", "must be a list")
            for declaration_index, declaration in enumerate(tool["functionDeclarations"]):
                declaration_path = f"{path}.functionDeclarations[{declaration_index}]"
                _require_keys(provider, declaration, declaration_path, {"name", "description", "parameters"}, {"name", "description", "parameters"})
                if not isinstance(declaration["parameters"], dict):
                    raise _shape_error(provider, declaration_path, "parameters", "must be an object")


def _validate_openai_payload(payload: dict[str, Any]) -> None:
    provider = "groq"
    _require_keys(provider, payload, "$", {"model", "messages", "tools"}, {"model", "messages", "tools"})
    if not isinstance(payload["model"], str) or not payload["model"]:
        raise _shape_error(provider, "model", "model", "must be a non-empty string")
    if not isinstance(payload["messages"], list):
        raise _shape_error(provider, "messages", "messages", "must be a list")
    if not isinstance(payload["tools"], list):
        raise _shape_error(provider, "tools", "tools", "must be a list")
    for index, message in enumerate(payload["messages"]):
        path = f"messages[{index}]"
        if not isinstance(message, dict) or "role" not in message:
            raise _shape_error(provider, path, "role", "required key missing")
        role = message["role"]
        if role in {"system", "user"}:
            _require_keys(provider, message, path, {"role", "content"}, {"role", "content"})
            if not isinstance(message["content"], str):
                raise _shape_error(provider, f"{path}.content", "content", "must be a string")
        elif role == "assistant":
            _require_keys(provider, message, path, {"role", "content"}, {"role", "content", "tool_calls"})
            if message["content"] is not None and not isinstance(message["content"], str):
                raise _shape_error(provider, f"{path}.content", "content", "must be a string or null")
            for call_index, call in enumerate(message.get("tool_calls", [])):
                call_path = f"{path}.tool_calls[{call_index}]"
                _require_keys(provider, call, call_path, {"id", "type", "function"}, {"id", "type", "function"})
                if call["type"] != "function":
                    raise _shape_error(provider, f"{call_path}.type", "type", "must be function")
                function = call["function"]
                _require_keys(provider, function, f"{call_path}.function", {"name", "arguments"}, {"name", "arguments"})
                if not isinstance(function["arguments"], str):
                    raise _shape_error(provider, f"{call_path}.function.arguments", "arguments", "must be a JSON string")
                try:
                    json.loads(function["arguments"])
                except json.JSONDecodeError as error:
                    raise _shape_error(provider, f"{call_path}.function.arguments", "arguments", str(error)) from error
        elif role == "tool":
            _require_keys(provider, message, path, {"role", "tool_call_id", "content"}, {"role", "tool_call_id", "content"})
            if not isinstance(message["content"], str):
                raise _shape_error(provider, f"{path}.content", "content", "must be a string")
        else:
            raise _shape_error(provider, f"{path}.role", "role", "unsupported role")
    for index, tool in enumerate(payload["tools"]):
        path = f"tools[{index}]"
        _require_keys(provider, tool, path, {"type", "function"}, {"type", "function"})
        if tool["type"] != "function":
            raise _shape_error(provider, f"{path}.type", "type", "must be function")
        function = tool["function"]
        _require_keys(provider, function, f"{path}.function", {"name", "description", "parameters"}, {"name", "description", "parameters"})
        if not isinstance(function["parameters"], dict):
            raise _shape_error(provider, f"{path}.function.parameters", "parameters", "must be an object")


def assert_payload_valid(provider: str, payload: dict[str, Any]) -> None:
    """Validate a provider payload, reporting the exact failing path."""
    name = getattr(provider, "name", provider)
    if name == "gemini":
        _validate_gemini_payload(payload)
    elif name in {"groq", "grok", "openrouter", "openai", "openai_compatible"}:
        _validate_openai_payload(payload)
    else:
        try:
            json.dumps(payload)
        except TypeError as error:
            raise _shape_error(str(name), "$", "payload", str(error)) from error


class AnthropicProvider(Provider):
    name = "anthropic"

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    def generate(self, request: LLMRequest) -> EventStream:
        import anthropic

        if os.getenv("HSX_DEBUG") == "1":
            assert_payload_valid(self.name, {"messages": to_plain_data(request.messages), "tools": to_plain_data(request.tools)})
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
                yield LLMEvent(
                    type="tool_call",
                    tool_name=block.name,
                    tool_input=block.input,
                    tool_call_id=getattr(block, "id", ""),
                )
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

    def _request_headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Hsx/1.0 (+https://github.com/Hiten1896/Hsx)",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _error_detail(self, status: str, body: str) -> str:
        redacted = body
        if self.api_key:
            redacted = redacted.replace(self.api_key, "[REDACTED]")
        redacted = re.sub(r"(?i)(bearer\s+)[^\s,\"']+", r"\1[REDACTED]", redacted)
        redacted = re.sub(r"(?i)([?&]key=)[^&\s]+", r"\1[REDACTED]", redacted)
        return f"status {status}; response body: {redacted[:200]}"

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        if os.getenv("HSX_DEBUG") == "1":
            assert_payload_valid(self.name, payload)
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=self._request_headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                status = getattr(response, "status", 200)
                body = response.read().decode("utf-8", errors="replace")
                try:
                    return json.loads(body)
                except json.JSONDecodeError as error:
                    detail = self._error_detail(str(status), body)
                    raise ProviderError(f"{self.name} request failed: {detail}") from error
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            detail = self._error_detail(str(error.code), body)
            raise ProviderError(f"{self.name} request failed: {detail}") from error
        except OSError as error:
            raise ProviderError(f"{self.name} request failed: {self._error_detail('unknown', str(error))}") from error


def _gemini_response_value(content: Any) -> dict[str, Any]:
    if isinstance(content, dict):
        return content
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            return parsed
    return {"content": content}


def to_gemini_payload(request: LLMRequest) -> dict[str, Any]:
    messages = to_plain_data(request.messages)
    tool_names: dict[str, str] = {}
    contents = []
    for message in messages:
        parts = []
        raw_content = message.get("content", "")
        blocks = raw_content if isinstance(raw_content, list) else [{"type": "text", "text": raw_content}]
        for block in blocks:
            block_type = block.get("type")
            if block_type == "text":
                parts.append({"text": block.get("text", "")})
            elif block_type == "tool_use":
                tool_names[block.get("id", "")] = block.get("name", "")
                parts.append({
                    "functionCall": {
                        "name": block.get("name", ""),
                        "args": block.get("input", {}),
                    }
                })
            elif block_type == "tool_result":
                parts.append({
                    "functionResponse": {
                        "name": tool_names.get(block.get("tool_use_id", ""), block.get("name", "")),
                        "response": _gemini_response_value(block.get("content", "")),
                    }
                })
        contents.append({
            "role": "model" if message.get("role") == "assistant" else "user",
            "parts": parts,
        })

    payload: dict[str, Any] = {"contents": contents}
    if request.system:
        payload["systemInstruction"] = {"parts": [{"text": request.system}]}
    if request.tools:
        payload["tools"] = [{
            "functionDeclarations": [{
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema", {}),
            } for tool in request.tools]
        }]
    return payload


def _openai_tool_result_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    return json.dumps(content)


def to_openai_payload(request: LLMRequest, model: str = "") -> dict[str, Any]:
    messages = to_plain_data(request.messages)
    converted_messages = []
    for message in messages:
        role = message.get("role", "user")
        raw_content = message.get("content", "")
        if not isinstance(raw_content, list):
            converted_messages.append({"role": role, "content": raw_content})
            continue

        text_parts = []
        tool_calls = []
        tool_results = []
        for block in raw_content:
            block_type = block.get("type")
            if block_type == "text":
                text_parts.append(block.get("text", ""))
            elif block_type == "tool_use":
                tool_calls.append({
                    "id": block.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": block.get("name", ""),
                        "arguments": json.dumps(block.get("input", {})),
                    },
                })
            elif block_type == "tool_result":
                tool_results.append({
                    "role": "tool",
                    "tool_call_id": block.get("tool_use_id", ""),
                    "content": _openai_tool_result_content(block.get("content", "")),
                })

        if tool_results:
            if text_parts:
                converted_messages.append({"role": "user", "content": "".join(text_parts)})
            converted_messages.extend(tool_results)
        else:
            converted_message: dict[str, Any] = {
                "role": "assistant" if role == "assistant" else role,
                "content": "".join(text_parts) if text_parts else None,
            }
            if tool_calls:
                converted_message["tool_calls"] = tool_calls
            converted_messages.append(converted_message)

    if request.system:
        converted_messages.insert(0, {"role": "system", "content": request.system})
    tools = [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema", {}),
            },
        }
        for tool in request.tools
    ]
    return {"model": model, "messages": converted_messages, "tools": tools}


class GeminiProvider(JSONHTTPProvider):
    name = "gemini"

    def _request_headers(self) -> dict[str, str]:
        headers = super()._request_headers()
        headers.pop("Authorization", None)
        if self.api_key:
            headers["x-goog-api-key"] = self.api_key
        return headers

    def generate(self, request: LLMRequest) -> EventStream:
        data = self._post(to_gemini_payload(request))
        parts = data["candidates"][0]["content"].get("parts", [])
        for part in parts:
            if part.get("text"):
                yield LLMEvent(type="text_delta", text=part["text"])
            if part.get("functionCall"):
                function = part["functionCall"]
                yield LLMEvent(
                    type="tool_call",
                    tool_name=function.get("name", ""),
                    tool_input=function.get("args", {}),
                    tool_call_id=f"gemini-{function.get('name', '')}",
                )
        yield LLMEvent(type="stop", stop_reason="stop")


class OllamaProvider(JSONHTTPProvider):
    name = "ollama"

    def generate(self, request: LLMRequest) -> EventStream:
        data = self._post({"model": self.model, "messages": request.messages, "stream": False})
        message = data.get("message", {})
        yield LLMEvent(type="text_delta", text=message.get("content", ""))
        yield LLMEvent(type="stop", stop_reason="stop")


class OpenAICompatibleProvider(JSONHTTPProvider):
    """Adapter for Groq, OpenRouter, and other chat-completions-compatible APIs."""

    def __init__(self, name: str, api_key: str, model: str, endpoint: str):
        super().__init__(model, endpoint, api_key)
        self.name = name

    def generate(self, request: LLMRequest) -> EventStream:
        data = self._post(to_openai_payload(request, self.model))
        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})
        content = message.get("content") or ""
        if content:
            yield LLMEvent(type="text_delta", text=content)
        for tool_call in message.get("tool_calls", []):
            function = tool_call.get("function", {})
            try:
                tool_input = json.loads(function.get("arguments", "{}"))
            except json.JSONDecodeError as error:
                raise ProviderError(f"{self.name} returned invalid tool arguments: {error}") from error
            yield LLMEvent(
                type="tool_call",
                tool_name=function.get("name", ""),
                tool_input=tool_input,
                tool_call_id=tool_call.get("id", ""),
            )
        yield LLMEvent(type="stop", stop_reason=choice.get("finish_reason", "stop"))
