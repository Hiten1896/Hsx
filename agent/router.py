import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from config import BASE_DIR
from joblib import dump, load
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline


_FAST_PATH_EXAMPLES = {
    "list_files": (
        "list files",
        "show files",
        "list workspace files",
        "show me files in the workspace",
        "browse workspace files",
        "what files are in the workspace",
        "list all workspace files",
        "show workspace contents",
        "view files in my workspace",
        "find files in the workspace",
    ),
    "notes": (
        "search my notes",
        "show my notes",
        "find my notes",
        "look up my notes",
        "search personal notes",
        "show saved notes",
        "find a saved note",
        "what notes have i saved",
        "search notes",
        "look up personal notes",
    ),
    "system_status": (
        "check system status",
        "show system status",
        "what is the system status",
        "check local disk and queue status",
        "report disk and queue status",
        "show local disk usage",
        "get system health",
        "how is the system doing",
        "check the computer status",
        "tell me the disk status",
        "how much disk space is free",
        "check storage space and task queue",
    ),
}


class RouterEventLogger:
    def __init__(self, log_path: Path | None = None):
        self.log_path = log_path or (BASE_DIR / "logs" / "router.jsonl")
        self.feedback_path = BASE_DIR / "data" / "feedback_logs.jsonl"
        self._lock = threading.Lock()

    def _write_to(self, path: Path, entry: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, path.open("a", encoding="utf-8") as log:
            log.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")

    def _write(self, event: str, session_id: str, job_id: str, **fields: Any) -> dict[str, Any]:
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "event": event,
            "session_id": session_id,
            "job_id": job_id,
            **fields,
        }
        self._write_to(self.log_path, entry)
        return entry

    def log_prompt(self, session_id: str, job_id: str, prompt: str) -> None:
        self._write("user_prompt", session_id, job_id, prompt=prompt)

    def log_tool_prediction(
        self,
        session_id: str,
        job_id: str,
        tool_name: str,
        tool_call_id: str,
    ) -> None:
        self._write("tool_predicted", session_id, job_id, tool=tool_name, tool_call_id=tool_call_id)

    def log_intent_prediction(
        self,
        session_id: str,
        job_id: str,
        tool_name: str,
        confidence: float,
        inference_ms: float,
        route: str,
    ) -> None:
        self._write(
            "intent_prediction",
            session_id,
            job_id,
            tool=tool_name,
            confidence=round(confidence, 6),
            inference_ms=round(inference_ms, 3),
            route=route,
        )

    def log_tool_execution(
        self,
        session_id: str,
        job_id: str,
        tool_name: str,
        tool_call_id: str,
        duration_ms: float,
        status: str,
    ) -> None:
        self._write(
            "tool_execution",
            session_id,
            job_id,
            tool=tool_name,
            tool_call_id=tool_call_id,
            duration_ms=round(duration_ms, 3),
            status=status,
        )

    def log_feedback(self, session_id: str, job_id: str, feedback: str) -> None:
        if feedback not in {"good", "bad"}:
            raise ValueError("feedback must be good or bad")
        entry = self._write("user_feedback", session_id, job_id, feedback=feedback)
        self._write_to(self.feedback_path, entry)


class IntentRouter:
    def __init__(self, model_path: Path | None = None):
        self.model_path = model_path or (BASE_DIR / "models" / "intent_router.joblib")
        self.pipeline: Pipeline | None = None

    def train(self, examples: list[tuple[str, str]]) -> "IntentRouter":
        usable = [(text.strip(), label.strip()) for text, label in examples if text.strip() and label.strip()]
        labels = {label for _, label in usable}
        if len(labels) < 2:
            raise ValueError("Intent routing requires examples for at least two intents.")

        self.pipeline = Pipeline([
            ("tfidf", TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4))),
            ("classifier", LogisticRegression(class_weight="balanced", max_iter=1000, C=20)),
        ])
        self.pipeline.fit(
            [text for text, _ in usable],
            [label for _, label in usable],
        )
        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        dump(self.pipeline, self.model_path)
        return self

    def train_from_tools(
        self,
        tools: list[dict],
        feedback_log: Path | None = None,
    ) -> "IntentRouter":
        examples: list[tuple[str, str]] = []
        tool_names = set()
        for tool in tools:
            name = tool["name"]
            description = str(tool.get("description", "")).strip()
            label = str(name).strip()
            if not label:
                continue
            tool_names.add(label)
            display_name = label.replace("_", " ")
            examples.extend([
                (display_name, label),
                (description or display_name, label),
                (f"{display_name}: {description}", label),
            ])
            examples.extend((text, label) for text in _FAST_PATH_EXAMPLES.get(label, ()))

        if feedback_log is not None:
            examples.extend(self._feedback_examples(feedback_log, tool_names))
        from agent.ml.intent_learning import UNCERTAIN_INTENTS

        examples.extend(
            (prompt, intent)
            for prompt, intent in UNCERTAIN_INTENTS.offline_examples()
            if intent in tool_names
        )
        return self.train(examples)

    @staticmethod
    def _feedback_examples(path: Path, tool_names: set[str]) -> list[tuple[str, str]]:
        prompts: dict[str, str] = {}
        predictions: dict[str, set[str]] = {}
        feedback: dict[str, str] = {}
        with path.open(encoding="utf-8") as log:
            for line in log:
                entry = json.loads(line)
                job_id = entry.get("job_id", "")
                if not job_id:
                    continue
                if entry.get("event") == "user_prompt":
                    prompts[job_id] = str(entry.get("prompt", "")).strip()
                elif entry.get("event") == "tool_predicted":
                    tool = entry.get("tool")
                    if tool in tool_names:
                        predictions.setdefault(job_id, set()).add(tool)
                elif entry.get("event") == "user_feedback":
                    feedback[job_id] = entry.get("feedback", "")
        return [
            (prompts[job_id], next(iter(tools)))
            for job_id, tools in predictions.items()
            if feedback.get(job_id) == "good"
            and prompts.get(job_id)
            and len(tools) == 1
        ]

    def load(self) -> "IntentRouter":
        self.pipeline = load(self.model_path)
        return self

    def predict_with_confidence(self, prompt: str) -> tuple[str, float]:
        if self.pipeline is None:
            self.load()
        if self.pipeline is None:
            raise RuntimeError("The intent router model is not loaded.")
        probabilities = self.pipeline.predict_proba([prompt])[0]
        best_index = int(probabilities.argmax())
        classifier = self.pipeline.named_steps["classifier"]
        return str(classifier.classes_[best_index]), float(probabilities[best_index])

    def predict(self, prompt: str) -> str:
        return self.predict_with_confidence(prompt)[0]


ROUTER_LOGGER = RouterEventLogger()
INTENT_ROUTER = IntentRouter()


class RouterTelemetry:
    def __init__(self):
        self._lock = threading.Lock()
        self._snapshot: dict[str, Any] = {
            "provider": "not connected",
            "model": "—",
            "router_confidence": None,
            "latency_ms": None,
            "input_tokens": 0,
            "output_tokens": 0,
            "token_usage_estimated": True,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "route": "idle",
        }

    def update_confidence(self, confidence: float | None, route: str) -> None:
        with self._lock:
            self._snapshot["router_confidence"] = round(confidence, 4) if confidence is not None else None
            self._snapshot["route"] = route

    def record_llm(
        self,
        provider: str,
        model: str,
        latency_ms: float,
        input_tokens: int,
        output_tokens: int,
        token_usage_estimated: bool,
    ) -> None:
        with self._lock:
            self._snapshot.update({
                "provider": provider,
                "model": model,
                "latency_ms": round(latency_ms, 2),
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "token_usage_estimated": token_usage_estimated,
                "route": "llm",
            })
            self._snapshot["total_input_tokens"] += input_tokens
            self._snapshot["total_output_tokens"] += output_tokens

    def record_local(self, tool: str, latency_ms: float) -> None:
        with self._lock:
            self._snapshot.update({
                "provider": f"local · {tool}",
                "model": "local tool",
                "latency_ms": round(latency_ms, 2),
                "input_tokens": 0,
                "output_tokens": 0,
                "token_usage_estimated": False,
                "route": "direct",
            })

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._snapshot)


LLM_TELEMETRY = RouterTelemetry()


if __name__ == "__main__":
    from agent.skill_loader import load_skills

    tools, _ = load_skills(str(BASE_DIR / "skills"))
    router = IntentRouter().train_from_tools(tools)
    print(f"Trained intent router saved to {router.model_path}")
