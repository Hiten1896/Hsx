from agent.llm.providers import to_gemini_payload, to_openai_payload
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
