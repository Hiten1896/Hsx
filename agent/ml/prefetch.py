from __future__ import annotations

import json
import logging
import threading
from collections import Counter, OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from config import DATA_DIR
from .storage import atomic_write_json

LOGGER = logging.getLogger("hsx.ml.prefetch")
MAX_INTENTS = 256
MAX_SESSIONS = 2000


class IntentPrefetcher:
    """Track first-order intent transitions and asynchronously preload local context."""

    def __init__(self, path: Path | None = None):
        self.path = path or DATA_DIR / "intent_transitions.json"
        self._lock = threading.Lock()
        self._transitions: dict[str, Counter[str]] = {}
        self._last_by_session: OrderedDict[str, str] = OrderedDict()
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="hsx-intent-prefetch",
        )
        self._futures: set[Future] = set()
        self._prefetched_context: dict[str, object] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            LOGGER.warning("Could not load intent transition history: %s", error)
            return
        if not isinstance(state, dict):
            LOGGER.warning("Ignoring invalid intent transition history.")
            return
        transitions = state.get("transitions", {})
        last_by_session = state.get("last_by_session", {})
        if isinstance(transitions, dict):
            for source, targets in list(transitions.items())[-MAX_INTENTS:]:
                if not isinstance(source, str) or not isinstance(targets, dict):
                    continue
                cleaned = Counter({
                    target: int(count)
                    for target, count in targets.items()
                    if isinstance(target, str) and isinstance(count, int) and count > 0
                })
                if cleaned:
                    self._transitions[source] = cleaned
        if isinstance(last_by_session, dict):
            self._last_by_session.update(
                (str(session), str(intent))
                for session, intent in list(last_by_session.items())[-MAX_SESSIONS:]
                if isinstance(session, str) and isinstance(intent, str)
            )

    def _save(self) -> None:
        atomic_write_json(
            self.path,
            {
                "version": 1,
                "transitions": {
                    source: dict(targets)
                    for source, targets in self._transitions.items()
                },
                "last_by_session": dict(self._last_by_session),
            },
        )

    def predict_next(self, intent: str) -> tuple[str | None, float]:
        intent = intent.strip()
        if not intent:
            return None, 0.0
        with self._lock:
            targets = self._transitions.get(intent)
            if not targets:
                return None, 0.0
            predicted, count = targets.most_common(1)[0]
            total = sum(targets.values())
            return predicted, count / total

    def observe(self, intent: str, session_id: str = "default") -> tuple[str | None, float]:
        intent = intent.strip()[:100]
        session_id = session_id.strip()[:128] or "default"
        if not intent:
            return None, 0.0
        with self._lock:
            previous = self._last_by_session.pop(session_id, None)
            if previous and previous != intent:
                if previous not in self._transitions and len(self._transitions) >= MAX_INTENTS:
                    self._transitions.pop(next(iter(self._transitions)))
                targets = self._transitions.setdefault(previous, Counter())
                if intent not in targets and len(targets) >= MAX_INTENTS:
                    targets.pop(targets.most_common()[-1][0])
                targets[intent] += 1
            self._last_by_session[session_id] = intent
            self._last_by_session.move_to_end(session_id)
            while len(self._last_by_session) > MAX_SESSIONS:
                self._last_by_session.popitem(last=False)
            self._save()
            targets = self._transitions.get(intent, Counter())
            if not targets:
                return None, 0.0
            predicted, count = targets.most_common(1)[0]
            return predicted, count / sum(targets.values())

    def prefetch_async(
        self,
        intent: str,
        loader: Callable[[str], object],
    ) -> Future | None:
        predicted, _ = self.predict_next(intent)
        if predicted is None:
            return None
        future = self._executor.submit(loader, predicted)
        future.add_done_callback(
            lambda completed, next_intent=predicted: self._store_prefetched(
                next_intent,
                completed,
            )
        )
        with self._lock:
            self._futures.add(future)
        future.add_done_callback(self._finish_prefetch)
        return future

    def take_prefetched_context(self, intent: str) -> object | None:
        with self._lock:
            return self._prefetched_context.pop(intent, None)

    def _store_prefetched(self, intent: str, future: Future) -> None:
        try:
            value = future.result()
        except Exception:
            return
        with self._lock:
            self._prefetched_context[intent] = value
            while len(self._prefetched_context) > MAX_INTENTS:
                self._prefetched_context.pop(next(iter(self._prefetched_context)))

    def _finish_prefetch(self, future: Future) -> None:
        with self._lock:
            self._futures.discard(future)
        try:
            future.result()
        except Exception:
            LOGGER.exception("Asynchronous intent context prefetch failed.")


INTENT_PREFETCHER = IntentPrefetcher()
