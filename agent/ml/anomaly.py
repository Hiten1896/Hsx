from __future__ import annotations

import json
import logging
import threading
from collections import deque
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

import psutil
from joblib import dump, load
from sklearn.ensemble import IsolationForest

from config import DATA_DIR

LOGGER = logging.getLogger("hsx.ml.anomaly")
FEATURE_NAMES = ("cpu_percent", "ram_percent", "temperature_c", "outbound_connections")


@dataclass(frozen=True)
class SystemMetrics:
    cpu_percent: float
    ram_percent: float
    temperature_c: float | None
    outbound_connections: int

    def as_features(self) -> list[float]:
        return [
            self.cpu_percent,
            self.ram_percent,
            self.temperature_c or 0.0,
            float(self.outbound_connections),
        ]


class SystemMetricsCollector:
    def __init__(self):
        self._previous_cpu: tuple[int, int] | None = None

    @staticmethod
    def _cpu_counters() -> tuple[int, int] | None:
        stat_path = Path("/proc/stat")
        if not stat_path.is_file():
            return None
        fields = stat_path.read_text(encoding="ascii").splitlines()[0].split()[1:]
        values = [int(value) for value in fields]
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        return sum(values), idle

    def _cpu_percent(self) -> float:
        counters = self._cpu_counters()
        if counters is None:
            return psutil.cpu_percent(interval=None)
        previous = self._previous_cpu
        self._previous_cpu = counters
        if previous is None:
            return psutil.cpu_percent(interval=None)
        total_delta = counters[0] - previous[0]
        idle_delta = counters[1] - previous[1]
        return 0.0 if total_delta <= 0 else max(
            0.0,
            min(100.0, 100.0 * (total_delta - idle_delta) / total_delta),
        )

    @staticmethod
    def _temperature() -> float | None:
        temperature_reader = getattr(psutil, "sensors_temperatures", None)
        if temperature_reader is None:
            return None
        try:
            sensors = temperature_reader()
        except NotImplementedError:
            return None
        readings = [
            reading.current
            for entries in sensors.values()
            for reading in entries
            if reading.current is not None
        ]
        return max(readings) if readings else None

    @staticmethod
    def _outbound_connections() -> int:
        return sum(
            1
            for connection in psutil.net_connections(kind="inet")
            if connection.status == psutil.CONN_ESTABLISHED
            and connection.raddr
            and connection.raddr.ip not in {"127.0.0.1", "::1"}
        )

    def sample(self) -> SystemMetrics:
        return SystemMetrics(
            cpu_percent=self._cpu_percent(),
            ram_percent=psutil.virtual_memory().percent,
            temperature_c=self._temperature(),
            outbound_connections=self._outbound_connections(),
        )


class AnomalyDetector:
    def __init__(
        self,
        log_path: Path | None = None,
        model_path: Path | None = None,
        baseline_path: Path | None = None,
        *,
        min_samples: int = 32,
        contamination: float = 0.05,
        random_state: int = 42,
    ):
        if min_samples < 2:
            raise ValueError("min_samples must be at least 2.")
        if not 0 < contamination <= 0.5:
            raise ValueError("contamination must be greater than 0 and at most 0.5.")
        self.log_path = log_path or DATA_DIR / "anomalies.jsonl"
        self.model_path = model_path or DATA_DIR / "anomaly_isolation_forest.joblib"
        self.baseline_path = baseline_path or DATA_DIR / "anomaly_baseline.jsonl"
        self.min_samples = min_samples
        self.contamination = contamination
        self.random_state = random_state
        self._lock = threading.Lock()
        self._baseline: deque[list[float]] = deque(maxlen=5000)
        self._model: IsolationForest | None = None
        self._samples_since_fit = 0
        self._load_baseline()
        if self.model_path.is_file():
            self._model = load(self.model_path)

    def _load_baseline(self) -> None:
        if not self.baseline_path.is_file():
            return
        with self.baseline_path.open(encoding="utf-8") as source:
            for line in source:
                try:
                    entry = json.loads(line)
                    features = entry["features"]
                    if len(features) == len(FEATURE_NAMES):
                        self._baseline.append([float(value) for value in features])
                except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                    LOGGER.warning("Skipping malformed anomaly baseline record.")

    def _append_jsonl(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as destination:
            destination.write(json.dumps(payload, sort_keys=True) + "\n")

    def _fit(self) -> None:
        if len(self._baseline) < self.min_samples:
            return
        model = IsolationForest(
            contamination=self.contamination,
            random_state=self.random_state,
            n_estimators=100,
        )
        model.fit(list(self._baseline))
        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.model_path.with_suffix(self.model_path.suffix + ".tmp")
        dump(model, temporary_path)
        temporary_path.replace(self.model_path)
        self._model = model
        self._samples_since_fit = 0

    def observe(self, metrics: SystemMetrics) -> dict | None:
        features = metrics.as_features()
        with self._lock:
            if self._model is None:
                self._baseline.append(features)
                self._append_jsonl(
                    self.baseline_path,
                    {"timestamp": datetime.now(UTC).isoformat(), "features": features},
                )
                self._fit()
                return None

            score = float(self._model.decision_function([features])[0])
            if score >= 0:
                self._baseline.append(features)
                self._samples_since_fit += 1
                self._append_jsonl(
                    self.baseline_path,
                    {"timestamp": datetime.now(UTC).isoformat(), "features": features},
                )
                if self._samples_since_fit >= self.min_samples:
                    self._fit()
                return None
            entry = {
                "timestamp": datetime.now(UTC).isoformat(),
                "kind": "system_anomaly",
                "score": score,
                "metrics": asdict(metrics),
                "advisory_only": True,
            }
            self._append_jsonl(self.log_path, entry)
            LOGGER.warning(
                "Advisory system anomaly detected (score %.4f): %s",
                score,
                json.dumps(asdict(metrics), sort_keys=True),
            )
            return entry


class AnomalyMonitor:
    def __init__(
        self,
        detector: AnomalyDetector | None = None,
        collector: SystemMetricsCollector | None = None,
        interval_seconds: float = 10.0,
        on_sample: Callable[[SystemMetrics], None] | None = None,
    ):
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive.")
        self.detector = detector or AnomalyDetector()
        self.collector = collector or SystemMetricsCollector()
        self.interval_seconds = interval_seconds
        self.on_sample = on_sample
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="hsx-anomaly-monitor",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float | None = None) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                metrics = self.collector.sample()
                self.detector.observe(metrics)
                if self.on_sample is not None:
                    self.on_sample(metrics)
            except (OSError, RuntimeError, ValueError, psutil.Error):
                LOGGER.exception("Could not collect or analyze local system metrics.")
            self._stop.wait(self.interval_seconds)


ANOMALY_MONITOR = AnomalyMonitor()
