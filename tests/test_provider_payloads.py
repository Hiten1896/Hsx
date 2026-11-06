import json
import re

import pytest

from agent.llm.providers import (
    OpenAICompatibleProvider,
    ProviderError,
    assert_payload_valid,
    to_gemini_payload,
    to_openai_payload,
)
from agent.llm.types import LLMRequest


def test_gemini_registered_skills_have_complete_schemas_and_real_prompt(monkeypatch):
    from agent import llm_client
    from agent.llm.types import LLMEvent
    import server

    skills = [dict(skill) for skill in server.TOOLS]
    report = next(skill for skill in skills if skill["name"] == "report")
    assert report["input_schema"]["properties"]["content"]["type"] == "object"
    assert report["input_schema"]["properties"]["structure"]["properties"]

    class CaptureRouter:
        providers = None
        active_provider = "test"
        active_model = "test"

        def generate(self, request):
            self.request = request
            yield LLMEvent(type="text_delta", text="Captured.")
            yield LLMEvent(type="stop", stop_reason="stop")

    router = CaptureRouter()
    monkeypatch.setattr(llm_client, "default_router", lambda: router)
    llm_client.call_llm(
        [{"role": "user", "content": "tell the specs of the machine"}],
        skills,
        sensitive=True,
    )
    payload = to_gemini_payload(router.request)
    assert len(payload["tools"][0]["functionDeclarations"]) == len(skills)
    assert "machine facts must come only from tool results" in payload["systemInstruction"]["parts"][0]["text"]
    assert_payload_valid("gemini", payload)


def test_gemini_payload_uses_native_parts_for_text_turns():
    assert to_gemini_payload(LLMRequest([
        {"role": "user", "content": "hello"},
    ])) == {
        "contents": [{"role": "user", "parts": [{"text": "hello"}]}],
    }


def test_gemini_replays_thought_signature_on_original_parts_and_ids():
    payload = to_gemini_payload(LLMRequest([
        {"role": "user", "content": "What is the CPU?"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "Checking.", "provider_meta": {"gemini_thought_signature": "sig-text"}},
            {"type": "tool_use", "name": "system_info", "input": {}, "id": "call-1",
             "provider_meta": {"gemini_thought_signature": "sig-call"}},
            {"type": "tool_use", "name": "disk_usage", "input": {}, "id": "call-2"},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "call-1", "content": {"cpu": "Test CPU"}},
            {"type": "tool_result", "tool_use_id": "call-2", "content": {"disk": "Test disk"}},
        ]},
    ]))

    model_parts = payload["contents"][1]["parts"]
    assert model_parts == [
        {"text": "Checking.", "thoughtSignature": "sig-text"},
        {"functionCall": {"name": "system_info", "args": {}, "id": "call-1"},
         "thoughtSignature": "sig-call"},
        {"functionCall": {"name": "disk_usage", "args": {}, "id": "call-2"}},
    ]
    assert payload["contents"][2]["parts"] == [
        {"functionResponse": {"name": "system_info", "response": {"cpu": "Test CPU"}, "id": "call-1"}},
        {"functionResponse": {"name": "disk_usage", "response": {"disk": "Test disk"}, "id": "call-2"}},
    ]
    assert_payload_valid("gemini", payload)


def test_gemini_summarizes_legacy_unsigned_tool_turns():
    payload = to_gemini_payload(LLMRequest([
        {"role": "assistant", "content": [
            {"type": "tool_use", "name": "system_info", "input": {}, "id": "old-call"},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "old-call", "content": {"cpu": "Old CPU"}},
        ]},
    ]))

    assert all("functionCall" not in part and "functionResponse" not in part
               for content in payload["contents"] for part in content["parts"])
    assert "system_info" in json.dumps(payload)
    assert "Old CPU" in json.dumps(payload)


def test_gemini_captures_response_part_thought_signature():
    from agent.llm.providers import GeminiProvider

    class FakeGemini(GeminiProvider):
        def _post(self, _payload):
            return {"candidates": [{"content": {"parts": [
                {"text": "Visible", "thoughtSignature": "visible-signature"},
                {"text": "Thinking only.", "thought": True, "thoughtSignature": "opaque-text"},
                {"inlineData": {"mimeType": "image/png", "data": "AA=="}, "thoughtSignature": "opaque-other"},
                {"functionCall": {"name": "system_info", "args": {}, "id": "exact-id"},
                 "thoughtSignature": "opaque-call"},
            ]}}]}

    events = list(FakeGemini("model", "http://localhost").generate(LLMRequest([])))
    assert events[0].provider_meta == {"gemini_thought_signature": "visible-signature"}
    assert events[0].raw_part == {"text": "Visible"}
    assert events[1].type == "provider_part"
    assert events[1].provider_meta == {"gemini_thought_signature": "opaque-text"}
    assert events[1].raw_part == {"text": "Thinking only.", "thought": True}
    assert events[2].type == "provider_part"
    assert events[2].provider_meta == {"gemini_thought_signature": "opaque-other"}
    assert events[2].raw_part == {"inlineData": {"mimeType": "image/png", "data": "AA=="}}
    assert events[3].provider_meta == {
        "gemini_thought_signature": "opaque-call", "gemini_function_call_id": "exact-id"
    }
    assert events[3].tool_call_id == "exact-id"
    replay = to_gemini_payload(LLMRequest([{
        "role": "assistant",
        "content": [{"type": "gemini_part", "part": events[2].raw_part, "provider_meta": events[2].provider_meta}],
    }]))
    assert replay["contents"][0]["parts"][0]["thoughtSignature"] == "opaque-other"
    text_replay = to_gemini_payload(LLMRequest([{
        "role": "assistant",
        "content": [{
            "type": "text", "text": events[0].text,
            "gemini_part": events[0].raw_part, "provider_meta": events[0].provider_meta,
        }],
    }]))
    assert text_replay["contents"][0]["parts"][0] == {
        "text": "Visible", "thoughtSignature": "visible-signature"
    }
    assert_payload_valid("gemini", text_replay)
    thought_replay = to_gemini_payload(LLMRequest([{
        "role": "assistant",
        "content": [{"type": "gemini_part", "part": events[1].raw_part, "provider_meta": events[1].provider_meta}],
    }]))
    assert thought_replay["contents"][0]["parts"][0] == {
        "text": "Thinking only.", "thought": True, "thoughtSignature": "opaque-text"
    }
    assert_payload_valid("gemini", thought_replay)


def test_provider_meta_survives_normalization_and_is_ignored_by_groq(monkeypatch):
    from agent import llm_client
    from agent.llm.types import LLMEvent

    class CaptureRouter:
        providers = None
        active_provider = "gemini"
        active_model = "test"

        def generate(self, _request):
            yield LLMEvent(
                type="tool_call", tool_name="system_info", tool_input={}, tool_call_id="call-1",
                provider_meta={"gemini_thought_signature": "opaque-signature"},
            )
            yield LLMEvent(type="stop")

    monkeypatch.setattr(llm_client, "default_router", CaptureRouter)
    response = llm_client.call_llm([], [], sensitive=True)
    history_block = json.loads(json.dumps(response.content[0].to_dict()))
    assert history_block["provider_meta"] == {"gemini_thought_signature": "opaque-signature"}

    payload = to_openai_payload(LLMRequest([
        {"role": "assistant", "content": [history_block]},
    ]), "groq-model")
    assert "thoughtSignature" not in json.dumps(payload)
    assert to_gemini_payload(LLMRequest([
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": [{"type": "text", "text": "hi"}]},
        {"role": "user", "content": "next"},
    ])) == {
        "contents": [
            {"role": "user", "parts": [{"text": "hello"}]},
            {"role": "model", "parts": [{"text": "hi"}]},
            {"role": "user", "parts": [{"text": "next"}]},
        ],
    }


def test_gemini_sanitizes_roles_merges_turns_and_extracts_system_messages():
    payload = to_gemini_payload(LLMRequest([
        {"role": "system", "content": "Follow the system rules."},
        {"role": "developer", "content": "Keep answers brief."},
        {"role": "human", "content": "hello"},
        {"role": "assistant", "content": "hi"},
        {"role": "assistant", "content": "again"},
        {"role": "user", "content": "next"},
    ], system="Base instruction."))

    assert payload["systemInstruction"] == {"parts": [{"text": "Base instruction.\n\nFollow the system rules.\n\nKeep answers brief."}]}
    assert [item["role"] for item in payload["contents"]] == ["user", "model", "user"]
    assert payload["contents"][1]["parts"] == [{"text": "hi"}, {"text": "again"}]
    assert_payload_valid("gemini", payload)


def test_gemini_structures_unparseable_function_output_as_json_object():
    payload = to_gemini_payload(LLMRequest([
        {"role": "assistant", "content": [{"type": "tool_use", "name": "lookup", "input": {}, "id": "call-1"}]},
        {"role": "function", "name": "lookup", "content": "plain output"},
    ]))
    assert payload["contents"][-1]["parts"] == [{
        "functionResponse": {"name": "lookup", "response": {"content": "plain output"}}
    }]


def test_gemini_drops_invalid_history_blocks_and_normalizes_tool_args():
    payload = to_gemini_payload(LLMRequest([
        {"role": "odd-provider-role", "content": [
            {"type": "future_block", "value": "bad"}, {"type": "text", "text": "kept"},
        ]},
        {"role": "assistant", "content": [{
            "type": "tool_use", "name": "lookup", "input": ["not", "an", "object"], "id": "call-1",
            "provider_meta": {"gemini_thought_signature": "sig"},
        }]},
    ]))
    assert payload["contents"] == [
        {"role": "user", "parts": [{"text": "kept"}]},
        {"role": "model", "parts": [{
            "functionCall": {"name": "lookup", "args": {}, "id": "call-1"},
            "thoughtSignature": "sig",
        }]},
    ]
    assert_payload_valid("gemini", payload)


def test_gemini_schema_validation_failure_logs_payload_to_stderr(monkeypatch, capsys):
    from agent.llm.providers import GeminiProvider

    payload = {"contents": [{"role": "system", "parts": [{"text": "bad"}]}]}
    monkeypatch.setenv("HSX_DEBUG", "1")
    with pytest.raises(ValueError):
        GeminiProvider("model", "http://127.0.0.1:1")._post(payload)
    assert json.loads(capsys.readouterr().err) == payload

def test_gemini_payload_uses_function_call_and_response_parts():
    payload = to_gemini_payload(LLMRequest([
        {"role": "user", "content": "search"},
        {"role": "assistant", "content": [{
            "type": "tool_use", "name": "search", "input": {"query": "cats"}, "id": "call-1",
            "provider_meta": {"gemini_thought_signature": "sig"},
        }]},
        {"role": "user", "content": [{
            "type": "tool_result",
            "tool_use_id": "call-1",
            "content": '{"items": ["cat"]}',
        }]},
        {"role": "user", "content": "continue"},
    ]))

    assert payload == {
        "contents": [
            {"role": "user", "parts": [{"text": "search"}]},
            {"role": "model", "parts": [{
                "functionCall": {"name": "search", "args": {"query": "cats"}, "id": "call-1"},
                "thoughtSignature": "sig",
            }]},
            {"role": "user", "parts": [{
                "functionResponse": {
                    "name": "search",
                    "response": {"items": ["cat"]},
                    "id": "call-1",
                }
            }, {"text": "continue"}]},
        ],
    }
    assert all("type" not in part for content in payload["contents"] for part in content["parts"])
    assert all(content["role"] in {"user", "model"} for content in payload["contents"])


def test_gemini_rejects_unsupported_function_role():
    with pytest.raises(ValueError, match="role.*must be user or model"):
        assert_payload_valid("gemini", {
            "contents": [{"role": "function", "parts": [{"text": "unsupported"}]}],
        })


def test_gemini_maps_legacy_function_result_to_function_response():
    payload = to_gemini_payload(LLMRequest([
        {"role": "user", "content": "check this"},
        {"role": "assistant", "content": [{
            "type": "tool_use", "name": "lookup", "input": {}, "id": "call-1"
        }]},
        {"role": "function", "name": "lookup", "content": '{"ok": true}'},
    ]))
    assert payload["contents"][-1] == {
        "role": "user",
        "parts": [{"functionResponse": {"name": "lookup", "response": {"ok": True}}}],
    }
    assert_payload_valid("gemini", payload)


def test_gemini_file_creation_conversation_uses_only_native_roles():
    history = [
        {"role": "user", "content": "Create a file called note.txt with hello."},
        {"role": "assistant", "content": [{
            "type": "tool_use", "name": "ask_user", "input": {"question": "What text?"}, "id": "ask-1",
            "provider_meta": {"gemini_thought_signature": "ask-signature"},
        }]},
        {"role": "user", "content": [{
            "type": "tool_result", "tool_use_id": "ask-1", "content": '{"answer": "hello"}'
        }]},
        {"role": "assistant", "content": [{"type": "text", "text": "I will create it."}]},
        {"role": "user", "content": "Actually, use hello."},
        {"role": "assistant", "content": [{
            "type": "tool_use", "name": "create_text_file",
            "input": {"path": "note.txt", "content": "hello"}, "id": "create-1",
            "provider_meta": {"gemini_thought_signature": "create-signature"},
        }]},
        {"role": "user", "content": [{
            "type": "tool_result", "tool_use_id": "create-1", "content": '{"success": true}'
        }]},
        {"role": "assistant", "content": [{"type": "text", "text": "Created note.txt."}]},
    ]
    payload = to_gemini_payload(LLMRequest(history))
    assert all(item["role"] in {"user", "model"} for item in payload["contents"])
    assert payload["contents"][5]["parts"][0]["functionCall"]["name"] == "create_text_file"
    assert payload["contents"][6]["parts"][0]["functionResponse"]["name"] == "create_text_file"
    assert_payload_valid("gemini", payload)


def test_groq_file_creation_conversation_preserves_tool_call_ids():
    history = [
        {"role": "user", "content": "Create a file called note.txt with hello."},
        {"role": "assistant", "content": [{
            "type": "tool_use", "name": "ask_user", "input": {"question": "What text?"}, "id": "ask-1"
        }]},
        {"role": "user", "content": [{
            "type": "tool_result", "tool_use_id": "ask-1", "content": '{"answer": "hello"}'
        }]},
        {"role": "assistant", "content": [{"type": "text", "text": "I will create it."}]},
        {"role": "user", "content": "Actually, use hello."},
        {"role": "assistant", "content": [{
            "type": "tool_use", "name": "create_text_file",
            "input": {"path": "note.txt", "content": "hello"}, "id": "create-1"
        }]},
        {"role": "user", "content": [{
            "type": "tool_result", "tool_use_id": "create-1", "content": '{"success": true}'
        }]},
    ]
    payload = to_openai_payload(LLMRequest(history), "live-selected-model")
    assert all(item["role"] in {"system", "user", "assistant", "tool"} for item in payload["messages"])
    assert payload["messages"][2] == {
        "role": "tool", "tool_call_id": "ask-1", "content": '{"answer": "hello"}'
    }
    assert payload["messages"][6] == {
        "role": "tool", "tool_call_id": "create-1", "content": '{"success": true}'
    }
    assert_payload_valid("groq", payload)


def test_gemini_array_schemas_get_default_items_recursively():
    request = LLMRequest([], tools=[_tool(schema={
        "type": "object",
        "properties": {
            "filenames": {"type": "array"},
            "nested": {"type": "object", "properties": {"values": {"type": "array"}}},
        },
    })])
    payload = to_gemini_payload(request)
    parameters = payload["tools"][0]["functionDeclarations"][0]["parameters"]
    assert parameters["properties"]["filenames"]["items"] == {"type": "string"}
    assert parameters["properties"]["nested"]["properties"]["values"]["items"] == {"type": "string"}
    assert_payload_valid("gemini", payload)


def test_openai_compatible_maps_legacy_function_role_to_correlated_tool_result():
    payload = to_openai_payload(LLMRequest([
        {"role": "assistant", "content": [{
            "type": "tool_use", "name": "lookup", "input": {}, "id": "call-legacy"
        }]},
        {"role": "function", "name": "lookup", "tool_call_id": "call-legacy", "content": "legacy function output"},
    ]), "model")

    assert payload["messages"][-1] == {
        "role": "tool",
        "tool_call_id": "call-legacy",
        "content": "legacy function output",
    }


def test_openai_compatible_rejects_unpaired_legacy_function_result():
    with pytest.raises(ProviderError, match="require a tool_call_id"):
        to_openai_payload(LLMRequest([
            {"role": "function", "name": "lookup", "content": "legacy function output"},
        ]), "model")


def test_openai_compatible_rejects_unknown_internal_roles():
    with pytest.raises(ProviderError, match="Unsupported internal message role"):
        to_openai_payload(LLMRequest([
            {"role": "developer", "content": "unsupported"},
        ]), "model")


def test_groq_payload_uses_openai_text_and_tool_schemas():
    request = LLMRequest([
        {"role": "user", "content": "hello"},
    ], tools=[{
        "name": "search",
        "description": "Search the web.",
        "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}},
    }])

    assert to_openai_payload(request, "llama") == {
        "model": "llama",
        "messages": [{"role": "user", "content": "hello"}],
        "tools": [{
            "type": "function",
            "function": {
                "name": "search",
                "description": "Search the web.",
                "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
            },
        }],
    }

    assert to_openai_payload(LLMRequest([
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": [{"type": "text", "text": "hi"}]},
        {"role": "user", "content": "next"},
    ]), "llama")["messages"] == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
        {"role": "user", "content": "next"},
    ]


def test_groq_payload_uses_tool_calls_and_tool_results():
    payload = to_openai_payload(LLMRequest([
        {"role": "user", "content": "search"},
        {"role": "assistant", "content": [{
            "type": "tool_use", "name": "search", "input": {"query": "cats"}, "id": "call-1"
        }]},
        {"role": "user", "content": [{
            "type": "tool_result",
            "tool_use_id": "call-1",
            "content": '{"items": ["cat"]}',
        }]},
        {"role": "user", "content": "continue"},
    ]), "llama")

    assert payload["messages"] == [
        {"role": "user", "content": "search"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call-1",
                "type": "function",
                "function": {"name": "search", "arguments": '{"query": "cats"}'},
            }],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": '{"items": ["cat"]}'},
        {"role": "user", "content": "continue"},
    ]


def _tool(name: str = "search", schema: dict | None = None) -> dict:
    return {
        "name": name,
        "description": f"Run {name}.",
        "input_schema": schema if schema is not None else {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    }


def _contract_histories() -> list[tuple[list[dict], list[dict]]]:
    tool_call = {"type": "tool_use", "name": "search", "input": {"query": "cats"}, "id": "call-1"}
    result = {"type": "tool_result", "tool_use_id": "call-1", "content": '{"items": ["cat"]}'}
    two_calls = [
        {"type": "tool_use", "name": "first", "input": {"value": 1}, "id": "call-1"},
        {"type": "tool_use", "name": "second", "input": {"value": 2}, "id": "call-2"},
    ]
    long_history = []
    for index in range(50):
        long_history.extend([
            {"role": "user", "content": f"turn {index}"},
            {"role": "assistant", "content": [{"type": "text", "text": f"reply {index}"}]},
        ])
    histories = [
        [{"role": "user", "content": "plain"}],
        [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": [{"type": "text", "text": "reply"}]},
            {"role": "user", "content": "second"},
        ],
        [
            {"role": "user", "content": "search"},
            {"role": "assistant", "content": [tool_call]},
            {"role": "user", "content": [result]},
            {"role": "assistant", "content": [{"type": "text", "text": "follow-up"}]},
        ],
        [
            {"role": "user", "content": "two searches"},
            {"role": "assistant", "content": two_calls},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "call-2", "content": '{"ok": 2}'},
                {"type": "tool_result", "tool_use_id": "call-1", "content": '{"ok": 1}'},
            ]},
            {"role": "assistant", "content": [{"type": "text", "text": "done"}]},
        ],
        [
            {"role": "user", "content": "errors"},
            {"role": "assistant", "content": [tool_call]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call-1", "content": ""}]},
            {"role": "assistant", "content": [{"type": "text", "text": "error handled"}]},
        ],
        [
            {"role": "user", "content": "combined"},
            {"role": "assistant", "content": [{"type": "text", "text": ""}, tool_call]},
        ],
        long_history,
        [{"role": "user", "content": 'quotes "newlines\n非ASCII'}, {"role": "assistant", "content": [
            {"type": "tool_use", "name": "nested", "input": {"json": {"line": "a\nb", "quote": '"'}}, "id": "nested-1"}
        ]}],
        [{"role": "user", "content": "no parameters"}],
        [
            {"role": "user", "content": "ask"},
            {"role": "assistant", "content": [{"type": "tool_use", "name": "ask_user", "input": {"question": "Pick one"}, "id": "ask-1"}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "ask-1", "content": '{"success": true, "answer": "one"}'}]},
            {"role": "assistant", "content": [{"type": "text", "text": "resumed"}]},
        ],
    ]
    tools = [_tool(), _tool("first"), _tool("second"), _tool("nested"), _tool("ask_user"), _tool("ping", {})]
    return [(history, tools) for history in histories]


@pytest.mark.parametrize("history,tools", _contract_histories())
def test_gemini_contract_covers_all_history_shapes(history, tools):
    payload = to_gemini_payload(LLMRequest(history, tools=tools, system="Be concise."))
    json.dumps(payload, ensure_ascii=False)
    assert_payload_valid("gemini", payload)


def test_gemini_contract_sanitizes_array_schema_without_items():
    payload = to_gemini_payload(LLMRequest(
        [{"role": "user", "content": "compress these files"}],
        tools=[_tool("compress_files", {
            "type": "object",
            "properties": {
                "filenames": {
                    "type": "array",
                    "description": "Files to compress.",
                    "default": [],
                },
            },
            "required": ["filenames"],
            "additionalProperties": False,
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        })],
    ))

    parameters = payload["tools"][0]["functionDeclarations"][0]["parameters"]
    assert parameters == {
        "type": "object",
        "properties": {"filenames": {
            "type": "array",
            "description": "Files to compress.",
            "default": [],
            "items": {"type": "string"},
        }},
        "required": ["filenames"],
    }
    assert_payload_valid("gemini", payload)


def test_gemini_contract_rejects_array_schema_without_items():
    with pytest.raises(ValueError, match=r"gemini payload invalid at .*parameters.*'items'.*required for array schemas"):
        assert_payload_valid("gemini", {
            "contents": [],
            "tools": [{"functionDeclarations": [{
                "name": "compress_files",
                "description": "Compress files.",
                "parameters": {"type": "array"},
            }]}],
        })


@pytest.mark.parametrize("schema,offending_key", [
    ({"type": "object", "properties": {"x": {}}}, "type"),
    ({"type": "object", "properties": {"x": {"type": "string", "enum": [1]}}}, "enum"),
    ({"type": "object", "properties": {"x": {"type": "object"}}}, "properties"),
    ({"type": "object", "properties": {"x": {"type": "array"}}}, "items"),
    ({"type": "object", "properties": {"x": {"type": "string", "$schema": "bad"}}}, "$schema"),
])
def test_gemini_validator_rejects_invalid_tool_schemas(schema, offending_key):
    declaration = {"name": "sample", "description": "Example.", "parameters": schema}
    with pytest.raises(ValueError, match=rf"offending key '{re.escape(offending_key)}'"):
        assert_payload_valid("gemini", {"contents": [], "tools": [{"functionDeclarations": [declaration]}]})


@pytest.mark.parametrize("history,tools", _contract_histories())
def test_groq_contract_covers_all_history_shapes(history, tools):
    payload = to_openai_payload(LLMRequest(history, tools=tools, system="Be concise."), "llama")
    json.dumps(payload, ensure_ascii=False)
    assert_payload_valid("groq", payload)


def test_strict_contract_checker_reports_unknown_and_missing_keys():
    with pytest.raises(ValueError, match=r"gemini payload invalid at contents\[0\]\.parts\[0\].*'type'"):
        assert_payload_valid("gemini", {"contents": [{"role": "user", "parts": [{"type": "text", "text": "bad"}]}]})
    with pytest.raises(ValueError, match=r"groq payload invalid at tools\[0\].*'type'"):
        assert_payload_valid("groq", {"model": "llama", "messages": [], "tools": [{"function": {}}]})


def test_debug_guard_validates_before_an_http_request(monkeypatch):
    monkeypatch.setenv("HSX_DEBUG", "1")
    provider = OpenAICompatibleProvider("groq", "secret", "llama", "http://127.0.0.1:1")
    with pytest.raises(ValueError, match="groq payload invalid"):
        provider._post({"model": "llama", "messages": [], "tools": [{"function": {}}]})
