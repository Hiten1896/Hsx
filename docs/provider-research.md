# Provider API contract notes

Research source URLs and rules checked against the current official provider documentation:

- Gemini function calling: <https://ai.google.dev/gemini-api/docs/function-calling>
  - Function declarations use a name, description, and JSON-schema `parameters`.
    Array properties include an `items` schema (for example
    `{"type":"array","items":{"type":"string"}}`).
  - Function results are returned in a `functionResponse` part associated with
    the function name and response data; they are not a `function` content role.
- Gemini `generateContent`: <https://ai.google.dev/api/generate-content>
  - Requests use `contents` and the `generateContent` method; function calls and
    responses are parts of content turns. This repository's adapter maps model
    turns to Gemini's `model` role and ordinary/tool-result turns to `user`.
- Groq tool use: <https://console.groq.com/docs/tool-use>
  - Tool declarations use Chat Completions' `tools` array with
    `type: "function"` and a JSON-schema `function.parameters` object.
  - The model returns an assistant message with `tool_calls`; each call carries
    an ID and function name/arguments. Results use `role: "tool"` and a
    `tool_call_id` matching the call ID.
- Groq Chat Completions: <https://console.groq.com/docs/api-reference#chat-create>
  - `messages` and a model ID are required. The model ID must be available to
    the account and compatible with the endpoint. Unsupported/deprecated IDs
    may be rejected; do not infer availability from a local default.
- Groq model catalog and list endpoint: <https://console.groq.com/docs/models>
  - The docs identify `GET https://api.groq.com/openai/v1/models` as the active
    model catalog endpoint. The endpoint requires account authentication; an
    unauthenticated request returned HTTP 401 during this research. At runtime,
    use the configured Groq API key and choose a compatible ID from the live
    response rather than hardcoding a model ID.

Implementation assumptions made explicit in tests: Gemini function-response
parts are placed in a `user` content turn; Groq tool results remain separate
`tool` messages with matching call IDs; missing Gemini array item schemas default
recursively to `{"type":"string"}`.
