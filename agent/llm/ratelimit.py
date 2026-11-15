"""Rate-limit awareness for LLM providers.

* ``parse_retry_after`` reads how long a provider says to wait (Retry-After, Groq's
  ``x-ratelimit-reset-*`` headers, Gemini's ``retryDelay``).
* ``ProviderCooldowns`` remembers providers that just failed so the router stops hitting a
  rate-limited (or misconfigured) provider on every request.
* ``RateBudget`` is an optional client-side limiter (``HSX_RATE_LIMITS``) that keeps requests
  and tokens per minute under a provider's published limit *before* it answers 429.

State is process-wide because ``default_router()`` builds a fresh router for every call.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from email.utils import parsedate_to_datetime
from typing import Any

DEFAULT_COOLDOWN_SECONDS = 60.0
DAILY_COOLDOWN_SECONDS = 3600.0
MIN_COOLDOWN_SECONDS = 5.0
MAX_COOLDOWN_SECONDS = 6 * 3600.0
WINDOW_SECONDS = 60.0

_DURATION_PART = re.compile(r"(\d+(?:\.\d+)?)\s*(ms|h|m|s)")
_RETRY_DELAY_FIELD = re.compile(r'"retryDelay"\s*:\s*"([^"]+)"')
_TRY_AGAIN_IN = re.compile(r"try again in\s+((?:\d+(?:\.\d+)?\s*(?:ms|h|m|s)\s*)+)", re.IGNORECASE)
_DAILY_MARKERS = ("per day", "daily", "rpd", "tokens per day", "requests per day")
_QUOTA_MARKERS = ("quota", "rate limit", "resource_exhausted", "too many requests")


def parse_duration(text: Any) -> float | None:
    """'2m59.56s' -> 179.56, '7.66s' -> 7.66, '500ms' -> 0.5, '30' -> 30.0, '14h12m' -> 51120.0."""
    if text is None:
        return None
    value = str(text).strip().casefold()
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    units = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}
    parts = _DURATION_PART.findall(value)
    if not parts or "".join(f"{amount}{unit}" for amount, unit in parts) != re.sub(r"\s+", "", value):
        return None
    return sum(float(amount) * units[unit] for amount, unit in parts)


def _header(headers: Any, name: str) -> str | None:
    if headers is None:
        return None
    try:
        value = headers.get(name)
    except AttributeError:
        return None
    return None if value is None else str(value)


def parse_retry_after(headers: Any = None, body: str = "") -> float | None:
    """Seconds the provider asks us to wait, or None when it does not say."""
    value = _header(headers, "Retry-After")
    if value:
        seconds = parse_duration(value)
        if seconds is not None:
            return seconds
        try:
            return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
        except (TypeError, ValueError):
            pass
    # Groq: the request quota resets daily, the token quota every minute. Wait for whichever is exhausted.
    requests_left = _header(headers, "x-ratelimit-remaining-requests")
    reset_requests = parse_duration(_header(headers, "x-ratelimit-reset-requests"))
    reset_tokens = parse_duration(_header(headers, "x-ratelimit-reset-tokens"))
    if requests_left is not None and requests_left.strip() == "0" and reset_requests is not None:
        return reset_requests
    if reset_tokens is not None:
        return reset_tokens
    if reset_requests is not None and requests_left is not None and requests_left.strip() == "0":
        return reset_requests
    match = _RETRY_DELAY_FIELD.search(body or "")  # Gemini: "retryDelay": "38s"
    if match:
        return parse_duration(match.group(1))
    match = _TRY_AGAIN_IN.search(body or "")  # Groq: "Please try again in 7.66s."
    if match:
        return parse_duration(match.group(1).strip())
    return None


def _clamp(seconds: float) -> float:
    return min(MAX_COOLDOWN_SECONDS, max(MIN_COOLDOWN_SECONDS, seconds))


def is_quota_error(error: BaseException) -> bool:
    text = str(error).casefold()
    return getattr(error, "status", None) == 429 or any(marker in text for marker in _QUOTA_MARKERS)


def cooldown_for_error(error: BaseException) -> tuple[float, str]:
    """How long to leave a provider alone after this error, and why. (0, '') means do not cool down."""
    status = getattr(error, "status", None)
    text = str(error).casefold()
    if is_quota_error(error):
        retry_after = getattr(error, "retry_after", None)
        if retry_after is None:
            retry_after = DAILY_COOLDOWN_SECONDS if any(m in text for m in _DAILY_MARKERS) else DEFAULT_COOLDOWN_SECONDS
        return _clamp(float(retry_after)), "rate limited"
    if status in (401, 403):
        return 300.0, "credentials rejected"
    if status == 404 or "model not found" in text:
        return 600.0, "model not found"
    if isinstance(status, int) and status >= 500:
        return 30.0, f"HTTP {status}"
    if "could not connect" in text:
        return 30.0, "unreachable"
    return 0.0, ""


class ProviderCooldowns:
    """Thread-safe 'leave this provider alone until ...' table."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._until: dict[str, tuple[float, str]] = {}

    def mark(self, provider: str, seconds: float, reason: str = "") -> None:
        if seconds <= 0:
            return
        with self._lock:
            current = self._until.get(provider)
            until = self._clock() + seconds
            if current is None or until > current[0]:
                self._until[provider] = (until, reason)

    def remaining(self, provider: str) -> float:
        with self._lock:
            entry = self._until.get(provider)
            if entry is None:
                return 0.0
            left = entry[0] - self._clock()
            if left <= 0:
                del self._until[provider]
                return 0.0
            return left

    def clear(self, provider: str | None = None) -> None:
        with self._lock:
            if provider is None:
                self._until.clear()
            else:
                self._until.pop(provider, None)

    def snapshot(self) -> dict[str, dict[str, Any]]:
        now = self._clock()
        with self._lock:
            return {
                name: {"seconds_left": round(until - now, 1), "reason": reason}
                for name, (until, reason) in self._until.items()
                if until > now
            }


class RateBudget:
    """Sliding 60 s window of requests and tokens per provider, from ``HSX_RATE_LIMITS``.

    ``HSX_RATE_LIMITS="groq:rpm=30,tpm=8000;gemini:rpm=10"``. Providers without an entry are unlimited.
    """

    def __init__(self, limits: Mapping[str, Mapping[str, int]] | None = None, clock: Callable[[], float] = time.monotonic):
        self._limits = {name.casefold(): dict(values) for name, values in (limits or {}).items()}
        self._clock = clock
        self._lock = threading.Lock()
        self._events: dict[str, deque[tuple[float, int]]] = {}

    @classmethod
    def from_env(cls, value: str | None = None) -> RateBudget:
        raw = os.getenv("HSX_RATE_LIMITS", "") if value is None else value
        limits: dict[str, dict[str, int]] = {}
        for entry in raw.split(";"):
            name, separator, settings = entry.partition(":")
            if not separator or not name.strip():
                continue
            parsed: dict[str, int] = {}
            for setting in settings.split(","):
                key, _, number = setting.partition("=")
                key = key.strip().casefold()
                if key in {"rpm", "tpm"} and number.strip().isdigit():
                    parsed[key] = int(number.strip())
            if parsed:
                limits[name.strip().casefold()] = parsed
        return cls(limits)

    def configured(self, provider: str) -> bool:
        return provider.casefold() in self._limits

    def _trim(self, events: deque[tuple[float, int]], now: float) -> None:
        while events and now - events[0][0] >= WINDOW_SECONDS:
            events.popleft()

    def wait_needed(self, provider: str, tokens: int) -> float:
        """Seconds until a request of ``tokens`` fits (0 = now). ``inf`` if it can never fit."""
        limit = self._limits.get(provider.casefold())
        if not limit:
            return 0.0
        tpm, rpm = limit.get("tpm"), limit.get("rpm")
        if tpm is not None and tokens > tpm:
            return float("inf")
        with self._lock:
            events = self._events.setdefault(provider.casefold(), deque())
            now = self._clock()
            self._trim(events, now)
            wait = 0.0
            if rpm is not None and len(events) >= rpm:
                wait = max(wait, WINDOW_SECONDS - (now - events[len(events) - rpm][0]))
            if tpm is not None:
                used = sum(count for _, count in events)
                if used + tokens > tpm:
                    freed = 0
                    for stamp, count in events:
                        freed += count
                        if used - freed + tokens <= tpm:
                            wait = max(wait, WINDOW_SECONDS - (now - stamp))
                            break
            return max(0.0, wait)

    def record(self, provider: str, tokens: int) -> None:
        if provider.casefold() not in self._limits:
            return
        with self._lock:
            events = self._events.setdefault(provider.casefold(), deque())
            now = self._clock()
            self._trim(events, now)
            events.append((now, max(0, int(tokens))))


COOLDOWNS = ProviderCooldowns()
BUDGET = RateBudget.from_env()


def estimate_request_tokens(request: Any) -> int:
    """Rough input size (4 characters per token); image bytes are not counted as text."""
    messages = []
    for message in getattr(request, "messages", []) or []:
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            content = [
                {key: value for key, value in block.items() if key != "data"} if isinstance(block, dict) else block
                for block in content
            ]
            message = {**message, "content": content}
        messages.append(message)
    payload = {"system": getattr(request, "system", ""), "messages": messages, "tools": getattr(request, "tools", [])}
    return max(1, len(json.dumps(payload, ensure_ascii=False, default=str)) // 4)
