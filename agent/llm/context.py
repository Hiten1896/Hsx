"""Keep what is sent to the LLM small, without changing the stored conversation.

Long chats and big tool results (file contents, search pages) are the usual reason a free tier's
tokens-per-minute limit is hit. ``compact_messages`` returns a trimmed *copy*:

1. Tool results and long text in older turns keep their start and end; the middle is cut.
2. If the conversation is still over ``HSX_MAX_CONTEXT_TOKENS`` (default 16000 estimated tokens),
   the oldest whole turns are dropped, never splitting a tool call from its result.

The most recent turns are always sent untouched.
"""
from __future__ import annotations

import json
import os
from typing import Any

KEEP_RECENT_MESSAGES = 6
OLD_TEXT_LIMIT = 6000
OLD_TOOL_RESULT_LIMIT = 2000
DEFAULT_MAX_CONTEXT_TOKENS = 16000
TRIM_NOTE = "[Earlier conversation was trimmed to save tokens.]"


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    return int(value) if value.isdigit() else default


def _shorten(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head, tail = int(limit * 0.7), int(limit * 0.2)
    return f"{text[:head]}\n…[{len(text) - head - tail} characters trimmed]…\n{text[len(text) - tail:]}"


def _estimate(messages: list[dict[str, Any]]) -> int:
    parts = []
    for message in messages:
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            content = [
                {key: value for key, value in block.items() if key != "data"} if isinstance(block, dict) else block
                for block in content
            ]
        parts.append({"role": message.get("role") if isinstance(message, dict) else "", "content": content})
    return len(json.dumps(parts, ensure_ascii=False, default=str)) // 4


def _shrink_message(message: dict[str, Any]) -> dict[str, Any]:
    content = message.get("content")
    if isinstance(content, str):
        return {**message, "content": _shorten(content, OLD_TEXT_LIMIT)}
    if not isinstance(content, list):
        return message
    shrunk = []
    for block in content:
        if not isinstance(block, dict):
            shrunk.append(block)
        elif block.get("type") == "tool_result" and isinstance(block.get("content"), str):
            shrunk.append({**block, "content": _shorten(block["content"], OLD_TOOL_RESULT_LIMIT)})
        elif block.get("type") == "text" and isinstance(block.get("text"), str):
            shrunk.append({**block, "text": _shorten(block["text"], OLD_TEXT_LIMIT)})
        else:
            shrunk.append(block)
    return {**message, "content": shrunk}


def _starts_a_turn(message: dict[str, Any]) -> bool:
    """A user message that is a real prompt, not the result of a tool call."""
    if message.get("role") != "user":
        return False
    content = message.get("content")
    if isinstance(content, str):
        return True
    return isinstance(content, list) and not any(
        isinstance(block, dict) and block.get("type") == "tool_result" for block in content
    )


def _with_note(message: dict[str, Any]) -> dict[str, Any]:
    content = message.get("content")
    if isinstance(content, str):
        return {**message, "content": f"{TRIM_NOTE}\n\n{content}"}
    return {**message, "content": [{"type": "text", "text": TRIM_NOTE}, *content]}


def compact_messages(
    messages: list[dict[str, Any]],
    *,
    keep_recent: int = KEEP_RECENT_MESSAGES,
    max_tokens: int | None = None,
) -> list[dict[str, Any]]:
    budget = max_tokens if max_tokens is not None else _int_env("HSX_MAX_CONTEXT_TOKENS", DEFAULT_MAX_CONTEXT_TOKENS)
    if budget <= 0 or not messages:
        return messages  # HSX_MAX_CONTEXT_TOKENS=0 turns compaction off
    cut = max(0, len(messages) - keep_recent)
    compacted = [_shrink_message(m) if index < cut and isinstance(m, dict) else m for index, m in enumerate(messages)]
    if _estimate(compacted) <= budget:
        return compacted
    # Still too big: drop whole old turns, always keeping the latest turn.
    starts = [index for index, m in enumerate(compacted) if isinstance(m, dict) and _starts_a_turn(m)]
    for start in starts[1:]:
        candidate = compacted[start:]
        if _estimate(candidate) <= budget:
            return [_with_note(candidate[0]), *candidate[1:]]
    if len(starts) > 1:
        candidate = compacted[starts[-1]:]
        return [_with_note(candidate[0]), *candidate[1:]]
    return compacted


def has_tool_blocks(messages: list[dict[str, Any]]) -> bool:
    return any(
        isinstance(m, dict)
        and isinstance(m.get("content"), list)
        and any(isinstance(b, dict) and b.get("type") in {"tool_use", "tool_result", "gemini_part"} for b in m["content"])
        for m in messages
    )


def flatten_tool_blocks(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rewrite tool calls/results as plain text so history can be sent with NO tool definitions.

    Some APIs (Anthropic) reject history that mentions tools when none are declared, and sending
    the definitions just to satisfy that would defeat the point.
    """
    flattened: list[dict[str, Any]] = []
    for message in messages:
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            flattened.append(message)
            continue
        blocks: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "tool_use":
                blocks.append({"type": "text", "text": f"[used tool: {block.get('name', 'tool')}]"})
            elif kind == "tool_result":
                result = block.get("content")
                result = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
                blocks.append({"type": "text", "text": f"[tool output: {_shorten(result, 400)}]"})
            elif kind == "gemini_part":
                continue
            else:
                blocks.append(block)
        if blocks:
            flattened.append({**message, "content": blocks})
    return flattened
