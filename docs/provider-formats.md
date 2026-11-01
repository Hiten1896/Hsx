# Provider payload formats

Hsx keeps conversation history in its internal format until the provider boundary. History entries are plain dictionaries:

```json
{"role": "assistant", "content": [{"type": "tool_use", "id": "call-1", "name": "search", "input": {"query": "cats"}}]}
```

The conversion functions in `agent/llm/providers.py` produce provider-native payloads. In debug mode (`HSX_DEBUG=1`), the payload is checked immediately before the HTTP request.

## Gemini

Gemini accepts `contents` with only `user` and `model` roles. Each content has `parts`; internal block types are never sent:

```json
{
  "contents": [
    {"role": "user", "parts": [{"text": "Find cats"}]},
    {"role": "model", "parts": [{"functionCall": {"name": "search", "args": {"query": "cats"}}}]},
    {"role": "user", "parts": [{"functionResponse": {"name": "search", "response": {"items": ["cat"]}}}]}
  ],
  "systemInstruction": {"parts": [{"text": "Be concise."}]}
}
```

Tool declarations use `tools[].functionDeclarations[]` with `name`, `description`, and `parameters`. Tool responses are placed in a `user` content and are matched to the function name through the tool-call ID.

## Groq / OpenAI-compatible APIs

Groq uses OpenAI chat-completions messages and wraps every tool declaration:

```json
{
  "model": "llama-3.1-8b-instant",
  "messages": [
    {"role": "user", "content": "Find cats"},
    {"role": "assistant", "content": null, "tool_calls": [{
      "id": "call-1",
      "type": "function",
      "function": {"name": "search", "arguments": "{\"query\": \"cats\"}"}
    }]},
    {"role": "tool", "tool_call_id": "call-1", "content": "{\"items\": [\"cat\"]}"}
  ],
  "tools": [{"type": "function", "function": {
    "name": "search",
    "description": "Search.",
    "parameters": {"type": "object", "properties": {}}
  }}]
}
```

Tool-call arguments are JSON strings. Tool results are strings, including an empty string or a JSON-encoded error object. An assistant message with only a tool call uses `content: null`.

## Adding a provider checklist

1. Define the provider-native history roles and message/part keys.
2. Define tool-call IDs, result correlation, and argument encoding.
3. Define system-prompt placement and empty-content behavior.
4. Add a dedicated conversion function; do not send internal `type` blocks directly.
5. Add strict payload validation that rejects unknown keys and missing required fields.
6. Add no-network contract tests for text, multi-turn history, tool calls/results, errors, empty content, long history, Unicode/nested arguments, and empty schemas.
7. Add a fake HTTP-server smoke test that exercises the real orchestrator.
8. Redact API keys and trim provider error bodies before logging or surfacing errors.

## Known failure messages

| Provider | Failure | Cause | Fix |
| --- | --- | --- | --- |
| Gemini | `Invalid JSON payload. Unknown name 'type' at 'contents[1].parts[0]'` | Internal Hsx block dictionaries were sent directly as Gemini parts. | Convert text, tool calls, and tool results to Gemini-native parts. |
| Groq | `'tools.0.type': property 'type' is missing` | Hsx tool schemas were sent without the OpenAI function wrapper. | Send `{"type":"function","function":{...}}` and convert history to OpenAI messages. |
