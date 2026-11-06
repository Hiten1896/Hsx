from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any


def to_plain_data(value: Any) -> Any:
    """Recursively convert model objects in provider payloads to plain data."""
    if hasattr(value, "to_dict"):
        return to_plain_data(value.to_dict())
    if isinstance(value, dict):
        return {key: to_plain_data(item) for key, item in value.items()}
    if isinstance(value, list):
        return [to_plain_data(item) for item in value]
    return value


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
    provider_meta: dict[str, Any] = field(default_factory=dict)


EventStream = Iterator[LLMEvent]
