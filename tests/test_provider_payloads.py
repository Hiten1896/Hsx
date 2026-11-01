import json

import pytest

from agent.llm.providers import OpenAICompatibleProvider, assert_payload_valid, to_gemini_payload, to_openai_payload
from agent.llm.types import LLMRequest
from agent.llm_client import NormalizedBlock


def test_gemini_payload_uses_native_parts_for_text_turns():
    assert to_gemini_payload(LLMRequest([
        {"role": "user", "content": "hello"},
    ])) == {
        "contents": [{"role": "user", "parts": [{"text": "hello"}]}],
    }

    assert to_gemini_payload(LLMRequest([
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": [NormalizedBlock(type="text", text="hi")]},
        {"role": "user", "content": "next"},
    ])) == {
        "contents": [
            {"role": "user", "parts": [{"text": "hello"}]},
            {"role": "model", "parts": [{"text": "hi"}]},
            {"role": "user", "parts": [{"text": "next"}]},
        ],
    }


def test_gemini_payload_uses_function_call_and_response_parts():
    payload = to_gemini_payload(LLMRequest([
        {"role": "user", "content": "search"},
        {"role": "assistant", "content": [NormalizedBlock(
            type="tool_use", name="search", input={"query": "cats"}, id="call-1"
        )]},
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
                "functionCall": {"name": "search", "args": {"query": "cats"}}
            }]},
            {"role": "user", "parts": [{
                "functionResponse": {
                    "name": "search",
                    "response": {"items": ["cat"]},
                }
            }]},
            {"role": "user", "parts": [{"text": "continue"}]},
        ],
    }
    assert all("type" not in part for content in payload["contents"] for part in content["parts"])


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
        {"role": "assistant", "content": [NormalizedBlock(type="text", text="hi")]},
        {"role": "user", "content": "next"},
    ]), "llama")["messages"] == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
        {"role": "user", "content": "next"},
    ]


def test_groq_payload_uses_tool_calls_and_tool_results():
    payload = to_openai_payload(LLMRequest([
        {"role": "user", "content": "search"},
        {"role": "assistant", "content": [NormalizedBlock(
            type="tool_use", name="search", input={"query": "cats"}, id="call-1"
        )]},
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
