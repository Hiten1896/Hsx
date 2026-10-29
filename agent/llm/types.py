from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any


@dataclass
class LLMRequest:
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] = field(default_factory=list)
    stream: bool = False
    system: str = ""


@dataclass
class LLMEvent:
    type: str
    text: str = ""
    tool_name: str = ""
    tool_input: dict[str, Any] = field(default_factory=dict)
    tool_call_id: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    stop_reason: str = ""


EventStream = Iterator[LLMEvent]
