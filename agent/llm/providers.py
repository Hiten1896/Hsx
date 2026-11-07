import json
import base64
import binascii
import hashlib
import logging
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .types import EventStream, LLMEvent, LLMRequest, to_plain_data


class ProviderError(RuntimeError):
    def __init__(self, message: str, *, provider: str = "", status: int | None = None, detail: str = ""):
        super().__init__(message)
        self.provider = provider
        self.status = status
        self.detail = detail


LOGGER = logging.getLogger("hsx.llm.providers")
LEGACY_GEMINI_THOUGHT_SIGNATURE = "context_engineering_is_the_way_to_go"
_PROVIDER_ERROR_LOG = Path(__file__).resolve().parents[2] / "logs" / "provider-errors.jsonl"
_PROVIDER_ERROR_LOG_LOCK = threading.Lock()


def _gemini_violations(body: str) -> list[dict[str, str]]:
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return []
    error = parsed.get("error", {}) if isinstance(parsed, dict) else {}
    details = error.get("details", []) if isinstance(error, dict) else []
    violations = []
    for detail in details if isinstance(details, list) else []:
        entries = detail.get("fieldViolations", []) if isinstance(detail, dict) else []
        for item in entries if isinstance(entries, list) else []:
            if isinstance(item, dict) and item.get("field"):
                violations.append({
                    "path": str(item["field"]),
                    "description": str(item.get("description", "Invalid value.")),
                })
    if not violations and isinstance(error, dict) and error.get("message"):
        violations.append({"path": "$", "description": str(error["message"])})
    return violations


def _log_provider_error(record: dict[str, Any]) -> None:
    try:
        _PROVIDER_ERROR_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _PROVIDER_ERROR_LOG_LOCK, _PROVIDER_ERROR_LOG.open("a", encoding="utf-8") as log_file:
            log_file.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError as error:
        LOGGER.error("Could not write provider error log: %s", error)


def _gemini_user_error(payload: dict[str, Any], violations: list[dict[str, str]]) -> str:
    if violations:
        violation = violations[0]
        path = violation["path"]
        match = re.search(r"tools\[(\d+)\]\.function_?declarations\[(\d+)\]", path, re.IGNORECASE)
        if match:
            tool_index, declaration_index = map(int, match.groups())
            try:
                declaration = payload["tools"][tool_index]["functionDeclarations"][declaration_index]
                tool_name = declaration.get("name", "unknown")
            except (IndexError, KeyError, TypeError):
                tool_name = "unknown"
            parameter_matches = re.findall(r"\.properties\.([^.\[]+)", path)
            description = violation["description"].casefold()
            if parameter_matches and (path.casefold().endswith(".type") or "no type" in description):
                parameter = parameter_matches[-1]
                return f"Gemini rejected tool '{tool_name}': parameter '{parameter}' has no type."
            reason = violation["description"].strip().rstrip(".") or "invalid schema"
            return f"Gemini rejected tool '{tool_name}': {reason}."
    return "Gemini rejected the conversation format. Check its tool schemas and conversation history."


def _gemini_validation_violation(error: ValueError) -> dict[str, str]:
    message = str(error)
    match = re.search(r"invalid at (.*?); offending key '([^']+)'(?:\: (.*))?$", message)
    if not match:
        return {"path": message, "description": message}
    path, key, detail = match.groups()
    path = path.replace("functionDeclarations", "function_declarations")
    if key == "type":
        path = f"{path}.type"
        description = "No type was declared."
    else:
        description = detail or f"Invalid {key}."
    return {"path": path, "description": description}


class Provider:
    name = "provider"

    def generate(self, request: LLMRequest) -> EventStream:
        raise NotImplementedError


def _validated_model_name(model: str) -> str:
    normalized = model.strip() if isinstance(model, str) else ""
    if not normalized or any(character.isspace() for character in normalized):
        raise ValueError("Provider model name must be a non-empty identifier without whitespace.")
    return normalized


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


_GEMINI_SCHEMA_KEYS = {
    "type",
    "format",
    "title",
    "description",
    "nullable",
    "enum",
    "items",
    "properties",
    "required",
    "minItems",
    "maxItems",
    "minProperties",
    "maxProperties",
    "minLength",
    "maxLength",
    "pattern",
    "example",
    "anyOf",
    "default",
    "minimum",
    "maximum",
    "propertyOrdering",
}
_GEMINI_FUNCTION_NAME = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
_GEMINI_SCHEMA_TYPES = {"string", "number", "integer", "boolean", "array", "object"}
_OPENAI_SCHEMA_KEYS = {
    "type", "description", "title", "properties", "required", "items", "enum",
    "additionalProperties", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
    "minItems", "maxItems", "minLength", "maxLength", "pattern", "format", "nullable",
}


def _sanitize_gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
    sanitized = {key: value for key, value in schema.items() if key in _GEMINI_SCHEMA_KEYS}
    properties = sanitized.get("properties")
    if isinstance(properties, dict):
        sanitized["properties"] = {
            name: _sanitize_gemini_schema(value) if isinstance(value, dict) else {"type": "string"}
            for name, value in properties.items()
        }
    if isinstance(sanitized.get("items"), dict):
        sanitized["items"] = _sanitize_gemini_schema(sanitized["items"])
    if isinstance(sanitized.get("anyOf"), list):
        sanitized["anyOf"] = [
            _sanitize_gemini_schema(item) for item in sanitized["anyOf"] if isinstance(item, dict)
        ]
    if sanitized.get("type") == "array" and "items" not in sanitized:
        sanitized["items"] = {"type": "string"}
    return sanitized


def _validate_gemini_schema(provider: str, schema: dict[str, Any], path: str) -> None:
    if not isinstance(schema, dict):
        raise _shape_error(provider, path, "schema", "must be an object")
    unknown = set(schema) - _GEMINI_SCHEMA_KEYS
    if unknown:
        raise _shape_error(provider, path, sorted(unknown)[0])
    schema_type = schema.get("type")
    if not isinstance(schema_type, str) or schema_type not in _GEMINI_SCHEMA_TYPES:
        raise _shape_error(provider, path, "type", "required and must be a supported Gemini schema type")
    if "description" in schema and not isinstance(schema["description"], str):
        raise _shape_error(provider, f"{path}.description", "description", "must be a string")
    if "enum" in schema:
        if schema_type != "string" or not isinstance(schema["enum"], list) or any(
            not isinstance(value, str) for value in schema["enum"]
        ):
            raise _shape_error(provider, f"{path}.enum", "enum", "must contain only strings on string schemas")
    if schema.get("type") == "array" and "items" not in schema:
        raise _shape_error(provider, path, "items", "required for array schemas")
    if schema.get("type") == "object" and not isinstance(schema.get("properties"), dict):
        raise _shape_error(provider, path, "properties", "required for object schemas")
    properties = schema.get("properties")
    if properties is not None:
        if schema_type != "object":
            raise _shape_error(provider, path, "properties", "only valid for object schemas")
        if not isinstance(properties, dict):
            raise _shape_error(provider, f"{path}.properties", "properties", "must be an object")
        for name, property_schema in properties.items():
            if not isinstance(name, str) or not name:
                raise _shape_error(provider, f"{path}.properties", str(name), "property name must be non-empty")
            _validate_gemini_schema(provider, property_schema, f"{path}.properties.{name}")
    required = schema.get("required", [])
    if not isinstance(required, list) or any(not isinstance(name, str) for name in required):
        raise _shape_error(provider, f"{path}.required", "required", "must be a list of strings")
    if isinstance(properties, dict) and any(name not in properties for name in required):
        missing = next(name for name in required if name not in properties)
        raise _shape_error(provider, f"{path}.required", missing, "required property is not declared")
    if "items" in schema:
        if schema_type != "array":
            raise _shape_error(provider, path, "items", "only valid for array schemas")
        _validate_gemini_schema(provider, schema["items"], f"{path}.items")
    if "anyOf" in schema:
        if not isinstance(schema["anyOf"], list) or not schema["anyOf"]:
            raise _shape_error(provider, f"{path}.anyOf", "anyOf", "must be a non-empty schema list")
        for index, option in enumerate(schema["anyOf"]):
            _validate_gemini_schema(provider, option, f"{path}.anyOf[{index}]")


def _sanitize_openai_schema(schema: dict[str, Any]) -> dict[str, Any]:
    sanitized = {key: value for key, value in schema.items() if key in _OPENAI_SCHEMA_KEYS}
    properties = sanitized.get("properties")
    if isinstance(properties, dict):
        sanitized["properties"] = {
            name: _sanitize_openai_schema(value) if isinstance(value, dict) else {"type": "string"}
            for name, value in properties.items()
        }
    if isinstance(sanitized.get("items"), dict):
        sanitized["items"] = _sanitize_openai_schema(sanitized["items"])
    if sanitized.get("type") == "array" and not isinstance(sanitized.get("items"), dict):
        sanitized["items"] = {"type": "string"}
    return sanitized


def _validate_openai_schema(schema: dict[str, Any], path: str) -> None:
    if not isinstance(schema, dict):
        raise _shape_error("groq", path, "schema", "must be an object")
    unknown = set(schema) - _OPENAI_SCHEMA_KEYS
    if unknown:
        raise _shape_error("groq", path, sorted(unknown)[0])
    if schema.get("type") == "array" and "items" not in schema:
        raise _shape_error("groq", path, "items", "required for array schemas")
    properties = schema.get("properties")
    if properties is not None:
        if not isinstance(properties, dict):
            raise _shape_error("groq", f"{path}.properties", "properties", "must be an object")
        for name, child in properties.items():
            _validate_openai_schema(child, f"{path}.properties.{name}")
    if "items" in schema:
        _validate_openai_schema(schema["items"], f"{path}.items")


def _validate_gemini_payload(payload: dict[str, Any]) -> None:
    provider = "gemini"
    if not isinstance(payload, dict):
        raise _shape_error(provider, "$", "payload", "must be an object")
    _require_keys(provider, payload, "$", {"contents"}, {"contents", "systemInstruction", "tools"})
    if not isinstance(payload["contents"], list):
        raise _shape_error(provider, "contents", "contents", "must be a list")
    previous_role = None
    for index, content in enumerate(payload["contents"]):
        path = f"contents[{index}]"
        if not isinstance(content, dict):
            raise _shape_error(provider, path, "content", "must be an object")
        _require_keys(provider, content, path, {"role", "parts"}, {"role", "parts"})
        if content["role"] not in {"user", "model"}:
            raise _shape_error(provider, f"{path}.role", "role", "must be user or model")
        if content["role"] == previous_role:
            raise _shape_error(provider, f"{path}.role", "role", "conversation roles must alternate")
        previous_role = content["role"]
        if not isinstance(content["parts"], list):
            raise _shape_error(provider, f"{path}.parts", "parts", "must be a list")
        if not content["parts"]:
            raise _shape_error(provider, f"{path}.parts", "parts", "must not be empty")
        for part_index, part in enumerate(content["parts"]):
            part_path = f"{path}.parts[{part_index}]"
            if not isinstance(part, dict):
                raise _shape_error(provider, part_path, "part", "must contain exactly one native part")
            unknown = set(part) - {"text", "functionCall", "functionResponse", "inlineData", "thoughtSignature", "thought"}
            if unknown:
                raise _shape_error(provider, part_path, sorted(unknown)[0])
            if len(set(part) - {"thoughtSignature", "thought"}) != 1:
                raise _shape_error(provider, part_path, "part", "must contain exactly one native part")
            if "thoughtSignature" in part and not isinstance(part["thoughtSignature"], str):
                raise _shape_error(provider, part_path, "thoughtSignature", "must be an opaque string")
            if "thought" in part and not isinstance(part["thought"], bool):
                raise _shape_error(provider, part_path, "thought", "must be a boolean")
            kind = next(key for key in part if key not in {"thoughtSignature", "thought"})
            if kind == "text":
                if not isinstance(part["text"], str):
                    raise _shape_error(provider, part_path, "text", "must be a string")
            elif kind == "inlineData":
                image = part[kind]
                if not isinstance(image, dict):
                    raise _shape_error(provider, part_path, kind, "must be an object")
                _require_keys(
                    provider,
                    image,
                    f"{part_path}.inlineData",
                    {"mimeType", "data"},
                    {"mimeType", "data"},
                )
                if image["mimeType"] not in {"image/jpeg", "image/png"}:
                    raise _shape_error(provider, part_path, kind, "unsupported image mime type")
                if not isinstance(image["data"], str) or len(image["data"]) > 3_000_000:
                    raise _shape_error(provider, part_path, kind, "image data exceeds the 2 MiB limit")
                try:
                    decoded = base64.b64decode(image["data"], validate=True)
                except (binascii.Error, ValueError) as error:
                    raise _shape_error(provider, part_path, kind, "image data must be valid base64") from error
                if len(decoded) > 2 * 1024 * 1024:
                    raise _shape_error(provider, part_path, kind, "image data exceeds the 2 MiB limit")
            elif kind == "functionCall":
                function = part[kind]
                if not isinstance(function, dict):
                    raise _shape_error(provider, part_path, kind, "must be an object")
                _require_keys(provider, function, f"{part_path}.functionCall", {"name", "args"}, {"name", "args", "id"})
                if (
                    not isinstance(function["name"], str)
                    or not _GEMINI_FUNCTION_NAME.fullmatch(function["name"])
                    or not isinstance(function["args"], dict)
                ):
                    raise _shape_error(provider, part_path, kind, "invalid name or args")
            elif kind == "functionResponse":
                function = part[kind]
                if not isinstance(function, dict):
                    raise _shape_error(provider, part_path, kind, "must be an object")
                _require_keys(provider, function, f"{part_path}.functionResponse", {"name", "response"}, {"name", "response", "id"})
                if (
                    not isinstance(function["name"], str)
                    or not _GEMINI_FUNCTION_NAME.fullmatch(function["name"])
                    or not isinstance(function["response"], dict)
                ):
                    raise _shape_error(provider, part_path, kind, "invalid name or response")
            else:
                raise _shape_error(provider, part_path, kind)
    if "systemInstruction" in payload:
        instruction = payload["systemInstruction"]
        if not isinstance(instruction, dict):
            raise _shape_error(provider, "systemInstruction", "systemInstruction", "must be an object")
        _require_keys(provider, instruction, "systemInstruction", {"parts"}, {"parts"})
        if not isinstance(instruction["parts"], list):
            raise _shape_error(provider, "systemInstruction.parts", "parts", "must be a list")
        for index, part in enumerate(instruction["parts"]):
            if not isinstance(part, dict):
                raise _shape_error(provider, f"systemInstruction.parts[{index}]", "part", "must be an object")
            _require_keys(provider, part, f"systemInstruction.parts[{index}]", {"text"}, {"text"})
            if not isinstance(part["text"], str):
                raise _shape_error(provider, f"systemInstruction.parts[{index}]", "text", "must be a string")
    if "tools" in payload:
        if not isinstance(payload["tools"], list):
            raise _shape_error(provider, "tools", "tools", "must be a list")
        names = set()
        for index, tool in enumerate(payload["tools"]):
            path = f"tools[{index}]"
            if not isinstance(tool, dict):
                raise _shape_error(provider, path, "tool", "must be an object")
            _require_keys(provider, tool, path, {"functionDeclarations"}, {"functionDeclarations"})
            if not isinstance(tool["functionDeclarations"], list):
                raise _shape_error(provider, f"{path}.functionDeclarations", "functionDeclarations", "must be a list")
            for declaration_index, declaration in enumerate(tool["functionDeclarations"]):
                declaration_path = f"{path}.functionDeclarations[{declaration_index}]"
                if not isinstance(declaration, dict):
                    raise _shape_error(provider, declaration_path, "declaration", "must be an object")
                _require_keys(provider, declaration, declaration_path, {"name", "description", "parameters"}, {"name", "description", "parameters"})
                name = declaration["name"]
                if not isinstance(name, str) or not _GEMINI_FUNCTION_NAME.fullmatch(name):
                    raise _shape_error(provider, declaration_path, "name", "must match ^[a-zA-Z_][a-zA-Z0-9_]*$")
                if name in names:
                    raise _shape_error(provider, declaration_path, "name", f"duplicate function name {name!r}")
                names.add(name)
                if not isinstance(declaration["description"], str) or not declaration["description"].strip():
                    raise _shape_error(provider, declaration_path, "description", "must be non-empty")
                if not isinstance(declaration["parameters"], dict):
                    raise _shape_error(provider, declaration_path, "parameters", "must be an object")
                _validate_gemini_schema(provider, declaration["parameters"], f"{declaration_path}.parameters")


def _validate_openai_payload(payload: dict[str, Any]) -> None:
    provider = "groq"
    _require_keys(provider, payload, "$", {"model", "messages", "tools"}, {"model", "messages", "tools"})
    try:
        _validated_model_name(payload["model"])
    except ValueError as error:
        raise _shape_error(provider, "model", "model", str(error)) from error
    if not isinstance(payload["messages"], list):
        raise _shape_error(provider, "messages", "messages", "must be a list")
    if not isinstance(payload["tools"], list):
        raise _shape_error(provider, "tools", "tools", "must be a list")
    seen_tool_call_ids = set()
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
                if not isinstance(call["id"], str) or not call["id"]:
                    raise _shape_error(provider, f"{call_path}.id", "id", "must be a non-empty string")
                seen_tool_call_ids.add(call["id"])
        elif role == "tool":
            _require_keys(provider, message, path, {"role", "tool_call_id", "content"}, {"role", "tool_call_id", "content"})
            if not isinstance(message["content"], str):
                raise _shape_error(provider, f"{path}.content", "content", "must be a string")
            if not isinstance(message["tool_call_id"], str) or message["tool_call_id"] not in seen_tool_call_ids:
                raise _shape_error(provider, f"{path}.tool_call_id", "tool_call_id", "must match a previous assistant tool call")
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
        _validate_openai_schema(function["parameters"], f"{path}.function.parameters")


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
        self.model = _validated_model_name(model)

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
        self.model = _validated_model_name(model)
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
        redacted = self._redact_body(body)
        return f"status {status}; response body: {redacted[:200]}"

    def _redact_body(self, body: str) -> str:
        redacted = body
        if self.api_key:
            redacted = redacted.replace(self.api_key, "[REDACTED]")
        redacted = re.sub(r"(?i)(bearer\s+)[^\s,\"']+", r"\1[REDACTED]", redacted)
        redacted = re.sub(r"(?i)([?&]key=)[^&\s]+", r"\1[REDACTED]", redacted)
        try:
            parsed = json.loads(redacted)
        except json.JSONDecodeError:
            return redacted

        sensitive_keys = {"api_key", "apikey", "authorization", "password", "secret", "token"}

        def scrub(value: Any) -> Any:
            if isinstance(value, dict):
                return {
                    key: "[REDACTED]" if key.casefold() in sensitive_keys else scrub(child)
                    for key, child in value.items()
                }
            if isinstance(value, list):
                return [scrub(child) for child in value]
            return value

        return json.dumps(scrub(parsed), ensure_ascii=False)

    def _open_with_503_retry(self, request: urllib.request.Request):
        for retry in range(3):
            try:
                return urllib.request.urlopen(request, timeout=30)
            except urllib.error.HTTPError as error:
                if error.code != 503 or retry == 2:
                    raise
                error.close()
                delay = 0.1 * (retry + 1)
                LOGGER.warning("%s provider returned HTTP 503; retrying in %.1f seconds.", self.name, delay)
                time.sleep(delay)

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.name == "gemini" or os.getenv("HSX_DEBUG") == "1" or self.name in {
            "groq", "grok", "openrouter", "openai", "openai_compatible",
        }:
            try:
                assert_payload_valid(self.name, payload)
            except ValueError as error:
                if os.getenv("HSX_DEBUG") == "1":
                    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str), file=sys.stderr)
                    raise
                message = str(error)
                violations = [_gemini_validation_violation(error)]
                _log_provider_error({"provider": self.name, "status": None, "violations": violations})
                if self.name == "gemini":
                    raise ProviderError(
                        _gemini_user_error(payload, violations), provider=self.name, detail=message
                    ) from error
                raise
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=self._request_headers(),
            method="POST",
        )
        try:
            with self._open_with_503_retry(request) as response:
                status = getattr(response, "status", 200)
                body = response.read().decode("utf-8", errors="replace")
                try:
                    return json.loads(body)
                except json.JSONDecodeError as error:
                    detail = self._error_detail(str(status), body)
                    LOGGER.warning("%s provider returned invalid JSON: %s", self.name, self._redact_body(body))
                    message = (
                        f"{self.name.title()} returned an invalid response."
                        if os.getenv("HSX_DEBUG") != "1"
                        else f"{self.name} request failed: {detail}"
                    )
                    raise ProviderError(message, provider=self.name, detail=detail) from error
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            detail = self._error_detail(str(error.code), body)
            LOGGER.warning("%s provider HTTP %s response: %s", self.name, error.code, self._redact_body(body))
            if self.name == "gemini":
                violations = _gemini_violations(body)
                redacted_body = self._redact_body(body)
                _log_provider_error({
                    "provider": self.name,
                    "status": error.code,
                    "body": redacted_body,
                    "violations": violations,
                })
            if os.getenv("HSX_DEBUG") == "1":
                message = f"{self.name} request failed (HTTP {error.code}): {detail}"
            elif self.name == "gemini" and error.code == 400:
                violations = _gemini_violations(body)
                message = _gemini_user_error(payload, violations)
            elif self.name == "gemini" and error.code >= 500:
                message = f"Gemini returned HTTP {error.code}; trying the configured fallback provider."
            elif self.name == "groq" and error.code == 404:
                message = "Groq model not found."
            else:
                message = f"{self.name.title()} request failed (HTTP {error.code}); {detail}"
            raise ProviderError(message, provider=self.name, status=error.code, detail=detail) from error
        except OSError as error:
            detail = self._error_detail("unknown", str(error))
            LOGGER.warning("%s provider connection failed: %s", self.name, detail)
            message = (
                f"{self.name.title()} could not connect to its API."
                if os.getenv("HSX_DEBUG") != "1"
                else f"{self.name} request failed: {detail}"
            )
            raise ProviderError(message, provider=self.name, detail=detail) from error


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


def _gemini_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def _append_gemini_content(contents: list[dict[str, Any]], role: str, parts: list[dict[str, Any]]) -> None:
    if not parts:
        return
    if contents and contents[-1]["role"] == role:
        contents[-1]["parts"].extend(parts)
    else:
        contents.append({"role": role, "parts": parts})


def to_gemini_payload(request: LLMRequest) -> dict[str, Any]:
    messages = to_plain_data(request.messages)
    tool_names: dict[str, str] = {}
    tool_request_ids: dict[str, str | None] = {}
    legacy_calls: dict[str, str] = {}
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        raw = message.get("content", "")
        tool_blocks = [
            block for block in raw if isinstance(block, dict) and block.get("type") == "tool_use"
        ] if isinstance(raw, list) else []
        if tool_blocks and not any(
            (block.get("provider_meta") or {}).get("gemini_thought_signature") for block in tool_blocks
        ):
            for block in tool_blocks:
                legacy_calls[str(block.get("id", ""))] = str(block.get("name", "unknown_function"))
    contents = []
    system_text = [_gemini_text(request.system)] if request.system else []
    for message in messages:
        if not isinstance(message, dict):
            continue
        parts = []
        raw_content = message.get("content", "")
        source_role = message.get("role")
        if source_role in {"system", "developer", "instruction"}:
            if raw_content:
                system_text.append(_gemini_text(raw_content))
            continue
        if message.get("role") == "function":
            function_name = message.get("name", "")
            if not isinstance(function_name, str) or not function_name:
                function_name = "unknown_function"
            _append_gemini_content(contents, "user", [{
                "functionResponse": {
                    "name": function_name,
                    "response": _gemini_response_value(raw_content),
                }
            }])
            continue
        blocks = raw_content if isinstance(raw_content, list) else [{"type": "text", "text": raw_content}]
        for block in blocks:
            if not isinstance(block, dict):
                parts.append({"text": _gemini_text(block)})
                continue
            block_type = block.get("type")
            if block_type == "text":
                stored_part = block.get("gemini_part")
                part = dict(stored_part) if isinstance(stored_part, dict) else {}
                part.setdefault("text", _gemini_text(block.get("text", "")))
                signature = (block.get("provider_meta") or {}).get("gemini_thought_signature")
                if isinstance(signature, str) and signature:
                    part["thoughtSignature"] = signature
                parts.append(part)
            elif block_type == "image":
                mime_type = block.get("mime_type", "")
                data = block.get("data", "")
                if mime_type in {"image/jpeg", "image/png"} and isinstance(data, str):
                    try:
                        decoded_image = base64.b64decode(data, validate=True)
                    except (binascii.Error, ValueError):
                        decoded_image = b""
                    if decoded_image and len(decoded_image) <= 2 * 1024 * 1024:
                        parts.append({"inlineData": {"mimeType": mime_type, "data": data}})
                    else:
                        parts.append({"text": "[Invalid image omitted from conversation history.]"})
                else:
                    parts.append({"text": "[Unsupported image omitted from conversation history.]"})
            elif block_type == "tool_use":
                call_id = block.get("id", "")
                function_name = block.get("name", "")
                if not isinstance(function_name, str) or not function_name:
                    function_name = "unknown_function"
                tool_names[call_id] = function_name
                provider_meta = block.get("provider_meta") or {}
                request_id = provider_meta.get("gemini_function_call_id", call_id)
                tool_request_ids[call_id] = request_id if isinstance(request_id, str) and request_id else None
                arguments = block.get("input", {})
                function_call = {
                    "name": function_name,
                    "args": arguments if isinstance(arguments, dict) else {},
                }
                request_id = tool_request_ids.get(call_id)
                if request_id:
                    function_call["id"] = request_id
                part = {"functionCall": function_call}
                signature = (block.get("provider_meta") or {}).get("gemini_thought_signature")
                if call_id in legacy_calls:
                    signature = LEGACY_GEMINI_THOUGHT_SIGNATURE
                if isinstance(signature, str) and signature:
                    part["thoughtSignature"] = signature
                parts.append(part)
            elif block_type == "tool_result":
                call_id = block.get("tool_use_id", "")
                name = tool_names.get(call_id, legacy_calls.get(call_id, block.get("name", "")))
                response = {
                    "name": name if isinstance(name, str) and name else "unknown_function",
                    "response": _gemini_response_value(block.get("content", "")),
                }
                request_id = tool_request_ids.get(call_id, call_id)
                if isinstance(request_id, str) and request_id:
                    response["id"] = request_id
                parts.append({"functionResponse": response})
            elif block_type == "gemini_part":
                part = block.get("part")
                if isinstance(part, dict):
                    serialized_part = dict(part)
                    signature = (block.get("provider_meta") or {}).get("gemini_thought_signature")
                    if isinstance(signature, str) and signature:
                        serialized_part["thoughtSignature"] = signature
                    parts.append(serialized_part)
            else:
                # Drop unknown provider-specific history blocks rather than replaying
                # malformed data into Gemini on every subsequent turn.
                continue
        has_function_response = any("functionResponse" in part for part in parts)
        # Gemini accepts only user/model content roles; function responses belong to a user turn.
        role = "user" if has_function_response else "model" if message.get("role") == "assistant" else "user"
        _append_gemini_content(contents, role, parts)

    payload: dict[str, Any] = {"contents": contents}
    if system_text:
        payload["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_text)}]}
    if request.tools:
        declarations = []
        for tool in request.tools:
            parameters = _sanitize_gemini_schema(tool.get("input_schema", {}))
            if not parameters:
                parameters = {"type": "object", "properties": {}}
            declarations.append({
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": parameters,
            })
        payload["tools"] = [{
            "functionDeclarations": declarations,
        }]
    return payload


def _openai_tool_result_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    return json.dumps(content)


def _tool_arguments(arguments: Any, provider: str) -> dict[str, Any]:
    if isinstance(arguments, dict):
        return arguments
    if not isinstance(arguments, str):
        raise ProviderError(f"{provider} returned invalid tool arguments: expected an object or JSON string")
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError as error:
        raise ProviderError(f"{provider} returned invalid tool arguments: {error}") from error
    if not isinstance(parsed, dict):
        raise ProviderError(f"{provider} returned invalid tool arguments: expected a JSON object")
    return parsed


def _openai_role(role: Any) -> str:
    if isinstance(role, str) and role in {"system", "user", "assistant", "tool"}:
        return role
    raise ProviderError(f"Unsupported internal message role for OpenAI-compatible provider: {role!r}")


def to_openai_payload(request: LLMRequest, model: str = "") -> dict[str, Any]:
    messages = to_plain_data(request.messages)
    converted_messages = []
    for message in messages:
        raw_content = message.get("content", "")
        if message.get("role") == "function":
            tool_call_id = message.get("tool_call_id") or message.get("id")
            if not isinstance(tool_call_id, str) or not tool_call_id:
                raise ProviderError("Groq legacy tool results require a tool_call_id.")
            converted_messages.append({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": _openai_tool_result_content(raw_content),
            })
            continue
        role = _openai_role(message.get("role", "user"))
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
            elif block_type == "image":
                raise ProviderError("Image input is supported only by the Gemini provider.")
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
                "role": role,
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
                "parameters": _sanitize_openai_schema(tool.get("input_schema", {})),
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
            signature = part.get("thoughtSignature")
            provider_meta = {"gemini_thought_signature": signature} if isinstance(signature, str) and signature else {}
            if part.get("functionCall"):
                function = part["functionCall"]
                provider_meta["gemini_function_call_id"] = function.get("id")
                yield LLMEvent(
                    type="tool_call",
                    tool_name=function.get("name", ""),
                    tool_input=function.get("args", {}),
                    tool_call_id=function.get("id") or f"gemini-{function.get('name', '')}",
                    provider_meta=provider_meta,
                )
            elif part.get("text") and part.get("thought") is True:
                raw_part = {key: value for key, value in part.items() if key != "thoughtSignature"}
                yield LLMEvent(type="provider_part", provider_meta=provider_meta, raw_part=raw_part)
            elif part.get("text"):
                raw_part = {key: value for key, value in part.items() if key != "thoughtSignature"}
                yield LLMEvent(
                    type="text_delta", text=part["text"], provider_meta=provider_meta,
                    raw_part=raw_part if provider_meta else {},
                )
            elif provider_meta:
                raw_part = {key: value for key, value in part.items() if key != "thoughtSignature"}
                yield LLMEvent(type="provider_part", provider_meta=provider_meta, raw_part=raw_part)
        yield LLMEvent(type="stop", stop_reason="stop")


class OllamaProvider(JSONHTTPProvider):
    name = "ollama"

    def generate(self, request: LLMRequest) -> EventStream:
        payload = to_openai_payload(request, self.model)
        payload["stream"] = False
        data = self._post(payload)
        message = data.get("message", {})
        content = message.get("content") or ""
        if content:
            yield LLMEvent(type="text_delta", text=content)
        for tool_call in message.get("tool_calls", []):
            function = tool_call.get("function", {})
            yield LLMEvent(
                type="tool_call",
                tool_name=function.get("name", ""),
                tool_input=_tool_arguments(function.get("arguments", {}), self.name),
                tool_call_id=tool_call.get("id", ""),
            )
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
            yield LLMEvent(
                type="tool_call",
                tool_name=function.get("name", ""),
                tool_input=_tool_arguments(function.get("arguments", "{}"), self.name),
                tool_call_id=tool_call.get("id", ""),
            )
        yield LLMEvent(type="stop", stop_reason=choice.get("finish_reason", "stop"))


class GroqProvider(OpenAICompatibleProvider):
    models_endpoint = "https://api.groq.com/openai/v1/models"
    _catalog_cache: dict[str, tuple[float, list[str]]] = {}
    _catalog_ttl_seconds = 300
    _reported_missing: set[tuple[str, str]] = set()

    def __init__(
        self,
        api_key: str,
        configured_model: str = "",
        endpoint: str = "https://api.groq.com/openai/v1/chat/completions",
        models_url: str | None = None,
    ):
        self.api_key = api_key
        self.models_url = models_url or self.models_endpoint
        models = self._fetch_models()
        selected = configured_model.strip()
        if selected and selected not in models:
            if models:
                self._report_missing_model(selected, models)
            selected = self._safe_default(models)
        elif not selected:
            selected = self._safe_default(models)
        if not selected:
            fix = (
                "Groq has no active text chat model. Set GROQ_MODEL in .env to an active chat model from "
                "https://api.groq.com/openai/v1/models."
            )
            print(fix, flush=True)
            raise ProviderError(fix, provider="groq")
        self.available_models = models
        super().__init__("groq", api_key, selected, endpoint)

    def _fetch_models(self, *, force: bool = False) -> list[str]:
        cache_key = hashlib.sha256(self.api_key.encode()).hexdigest()
        cached = self._catalog_cache.get(cache_key)
        if not force and cached and time.monotonic() - cached[0] < self._catalog_ttl_seconds:
            return list(cached[1])
        request = urllib.request.Request(
            self.models_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                body = response.read().decode("utf-8", errors="replace")
            data = json.loads(body)
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            detail = self._error_detail(str(error.code), body)
            LOGGER.warning("Groq model catalog HTTP %s response: %s", error.code, detail)
            raise ProviderError(
                "Groq model catalog is unavailable.",
                provider="groq",
                status=error.code,
                detail=detail,
            ) from error
        except (OSError, json.JSONDecodeError) as error:
            LOGGER.warning("Groq model catalog request failed: %s", error)
            raise ProviderError("Groq model catalog is unavailable.", provider="groq") from error
        model_data = data.get("data") if isinstance(data, dict) else None
        if not isinstance(model_data, list):
            raise ProviderError("Groq returned an invalid model catalog.", provider="groq")
        models = sorted({
            item["id"].strip()
            for item in model_data
            if isinstance(item, dict)
            and item.get("active", True) is not False
            and isinstance(item.get("id"), str)
            and item["id"].strip()
            and self._is_text_chat_model(item["id"].strip())
        })
        self._catalog_cache[cache_key] = (time.monotonic(), models)
        return models

    @staticmethod
    def _is_text_chat_model(model: str) -> bool:
        return not any(marker in model.casefold() for marker in (
            "whisper", "embedding", "moderation", "guard", "audio", "transcribe", "playai-tts",
            "-tts", "speech", "orpheus",
        ))

    @staticmethod
    def _safe_default(models: list[str]) -> str:
        chat_models = [model for model in models if GroqProvider._is_text_chat_model(model)]
        return chat_models[0] if chat_models else ""

    def _report_missing_model(self, model: str, models: list[str]) -> None:
        cache_key = hashlib.sha256(self.api_key.encode()).hexdigest()
        report_key = (cache_key, model)
        if report_key in self._reported_missing:
            return
        self._reported_missing.add(report_key)
        print(
            f"Groq model {model} not found. Available: {', '.join(models)}. Set GROQ_MODEL in .env.",
            flush=True,
        )

    def generate(self, request: LLMRequest) -> EventStream:
        try:
            yield from super().generate(request)
        except ProviderError as error:
            if error.status != 404:
                raise
            models = self._fetch_models(force=True)
            if self.model not in models:
                self._report_missing_model(self.model, models)
                replacement = self._safe_default(models)
                if not replacement:
                    raise ProviderError("Groq model not found and no chat models are available.", provider="groq") from error
                self.model = replacement
                self.available_models = models
                yield from super().generate(request)
                return
            raise
