from __future__ import annotations

import json
import logging
import math
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from sklearn.linear_model import Ridge

from config import DATA_DIR

LOGGER = logging.getLogger("hsx.ml.forecaster")
RESOURCE_METRICS = {"disk_used_bytes", "ram_percent"}


class WorkloadForecaster:
    def __init__(
        self,
        history_path: Path | None = None,
        *,
        max_samples: int = 5000,
        min_regression_samples: int = 3,
        smoothing: float = 0.3,
    ):
        if max_samples < 1:
            raise ValueError("max_samples must be positive.")
        if min_regression_samples < 2:
            raise ValueError("min_regression_samples must be at least 2.")
        if not 0 < smoothing <= 1:
            raise ValueError("smoothing must be greater than 0 and at most 1.")
        self.history_path = history_path or DATA_DIR / "workload_history.jsonl"
        self.max_samples = max_samples
        self.min_regression_samples = min_regression_samples
        self.smoothing = smoothing
        self._lock = threading.Lock()
        self._resources: dict[str, deque[tuple[float, float]]] = defaultdict(
            lambda: deque(maxlen=max_samples)
        )
        self._durations: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=max_samples)
        )
        self._load()

    def _load(self) -> None:
        if not self.history_path.is_file():
            return
        with self.history_path.open(encoding="utf-8") as source:
            for line in source:
                try:
                    entry = json.loads(line)
                    if not isinstance(entry, dict):
                        LOGGER.warning("Skipping malformed workload history record.")
                        continue
                    if entry.get("kind") == "resource":
                        metric = entry["metric"]
                        value = float(entry["value"])
                        timestamp = float(entry["timestamp"])
                        if (
                            metric in RESOURCE_METRICS
                            and math.isfinite(value)
                            and math.isfinite(timestamp)
                        ):
                            self._resources[metric].append((timestamp, value))
                    elif entry.get("kind") == "job" and entry.get("succeeded") is True:
                        duration = float(entry["duration_seconds"])
                        category = str(entry["category"])
                        if duration >= 0 and math.isfinite(duration):
                            self._durations[category].append(duration)
                except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                    LOGGER.warning("Skipping malformed workload history record.")

    def _append(self, entry: dict[str, Any]) -> None:
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        with self.history_path.open("a", encoding="utf-8") as destination:
            destination.write(json.dumps(entry, sort_keys=True) + "\n")

    def record_resource_sample(
        self,
        *,
        disk_used_bytes: int,
        ram_percent: float,
        timestamp: float | None = None,
    ) -> None:
        if (
            disk_used_bytes < 0
            or not math.isfinite(float(disk_used_bytes))
            or not math.isfinite(ram_percent)
            or not 0 <= ram_percent <= 100
        ):
            raise ValueError("Resource sample values are outside valid ranges.")
        observed_at = time.time() if timestamp is None else float(timestamp)
        if not math.isfinite(observed_at):
            raise ValueError("Resource sample timestamp must be finite.")
        samples = {
            "disk_used_bytes": float(disk_used_bytes),
            "ram_percent": float(ram_percent),
        }
        with self._lock:
            for metric, value in samples.items():
                self._resources[metric].append((observed_at, value))
                self._append({
                    "kind": "resource",
                    "timestamp": observed_at,
                    "metric": metric,
                    "value": value,
                })

    def record_job(
        self,
        category: str,
        duration_seconds: float,
        *,
        succeeded: bool,
        timestamp: float | None = None,
    ) -> None:
        category = category.strip().lower()
        duration = float(duration_seconds)
        if not category or duration < 0 or not math.isfinite(duration):
            raise ValueError("A category and non-negative finite duration are required.")
        observed_at = time.time() if timestamp is None else float(timestamp)
        if not math.isfinite(observed_at):
            raise ValueError("Job timestamp must be finite.")
        with self._lock:
            if succeeded:
                self._durations[category].append(duration)
            self._append({
                "kind": "job",
                "timestamp": observed_at,
                "category": category,
                "duration_seconds": duration,
                "succeeded": bool(succeeded),
            })

    def _forecast_trend(
        self,
        metric: str,
        samples: deque[tuple[float, float]],
        horizon_seconds: float,
    ) -> dict | None:
        if len(samples) < 2:
            return None
        origin = samples[0][0]
        x = [[timestamp - origin] for timestamp, _ in samples]
        y = [value for _, value in samples]
        if len(samples) >= self.min_regression_samples:
            model = Ridge(alpha=1.0)
            model.fit(x, y)
            predicted = float(model.predict([[x[-1][0] + horizon_seconds]])[0])
        else:
            slope = (y[-1] - y[0]) / max(x[-1][0] - x[0][0], 1.0)
            predicted = y[-1] + slope * horizon_seconds
        if metric == "ram_percent":
            predicted = min(100.0, predicted)
        return {
            "value": max(0.0, predicted),
            "method": (
                "ridge"
                if len(samples) >= self.min_regression_samples
                else "linear_fallback"
            ),
            "samples": len(samples),
            "horizon_seconds": horizon_seconds,
        }

    def forecast_resource(self, metric: str, horizon_seconds: float = 3600.0) -> dict | None:
        if metric not in RESOURCE_METRICS:
            raise ValueError(f"Unsupported resource metric: {metric}")
        if horizon_seconds < 0 or not math.isfinite(horizon_seconds):
            raise ValueError("horizon_seconds must be non-negative.")
        with self._lock:
            return self._forecast_trend(metric, self._resources[metric], horizon_seconds)

    def predict_job_duration(self, category: str) -> dict | None:
        category = category.strip().lower()
        with self._lock:
            category_samples = self._durations.get(category)
            samples = category_samples or next(
                (values for values in self._durations.values() if values),
                None,
            )
            if not samples:
                return None
            estimate = samples[0]
            for observed in list(samples)[1:]:
                estimate = self.smoothing * observed + (1 - self.smoothing) * estimate
            return {
                "category": category,
                "seconds": max(0.0, estimate),
                "samples": len(samples),
                "source": "category" if category_samples else "global_fallback",
            }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "resources": {name: list(samples) for name, samples in self._resources.items()},
                "job_durations": {name: list(samples) for name, samples in self._durations.items()},
            }


def classify_job(request: str) -> str:
    text = request.lower()
    if any(term in text for term in ("shell", "command", "terminal", "script", "run code")):
        return "shell"
    if any(term in text for term in ("read", "find", "search", "summarize", "open")):
        return "file_or_search"
    if any(term in text for term in ("write", "create", "generate", "draft", "make")):
        return "file_generation"
    return "agent"


FORECASTER = WorkloadForecaster()
