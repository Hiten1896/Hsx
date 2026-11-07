# Provider research: Gemini signatures and Groq tool use

Reviewed 2026-11-07 against the current official provider documentation.

## Gemini GenerateContent (REST)

Sources:
- Thought signatures: https://ai.google.dev/gemini-api/docs/thought-signatures
- Function calling and manual history rules: https://ai.google.dev/gemini-api/docs/generate-content/function-calling
- REST GenerateContent / Part schema: https://ai.google.dev/api/generate-content

Rules applied:
1. Gemini is stateless across GenerateContent calls. For manually managed history, echo each returned `thoughtSignature` in its original `Part`; REST uses camelCase `thoughtSignature` (SDK examples may expose `thought_signature`). Signatures are opaque strings and must not be synthesized or modified.
2. Gemini 3 may attach signatures to any response Part; echo all signatures. Passing them back is mandatory for function-calling signatures. Never merge signed and unsigned Parts, nor combine two signed Parts.
3. Keep each model function-call Part distinct and keep the original function-call `id`; return that exact id in its corresponding function response. For parallel calls preserve the original Part ordering and per-Part metadata.
   The official parallel-call example specifically shows a signature on the first function-call Part and no signature on later parallel Parts; those unsigned siblings remain part of that signed model response and must not be treated as legacy turns.
4. Older manually stored tool-call history with no signature uses Google's documented compatibility placeholder `context_engineering_is_the_way_to_go` on the original function-call Part. The corresponding function response retains its tool-call ID. This placeholder is used only for unsigned legacy history; new signatures are always replayed unchanged.
5. Provider metadata is internal JSON. Only the Gemini adapter maps `provider_meta.gemini_thought_signature` to REST `thoughtSignature`; other provider adapters omit it.
6. Gemini contents use `user` and `model` roles. System instructions belong in top-level `systemInstruction`.

## Groq Chat Completions

Sources:
- Local tool calling: https://console.groq.com/docs/tool-use/local-tool-calling
- Tool use overview and model support: https://console.groq.com/docs/tool-use/overview
- Models endpoint / available models: https://console.groq.com/docs/models
- Chat completions API reference: https://console.groq.com/docs/api-reference

Rules applied:
1. Local tool use is an application-managed loop: send function declarations, execute returned calls locally, then send matching tool results on the next completion. Parallel calls must all be processed before the follow-up completion.
2. Model IDs are discoverable from `GET https://api.groq.com/openai/v1/models`; the response is the live source for currently available IDs. Select only listed active chat models. The current tool-use overview states that all hosted Groq models support local tool use; non-chat modalities still need to be excluded.
3. Groq chat completions use the OpenAI-compatible `tools` function declaration shape and support `tool_choice`; preserve call IDs when returning tool results. The API reference documents a maximum of 128 tools.
4. Groq has no Gemini thought-signature field. Never serialize Gemini `provider_meta` into Groq requests.
5. Provider health probes should use a tiny tool-free chat request, so credential/model health is separated from tool schema validation.
