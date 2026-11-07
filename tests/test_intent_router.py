import json

import pytest
from joblib import load
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression

from agent.router import IntentRouter


def test_intent_router_trains_saves_and_reloads_pipeline(tmp_path):
    model_path = tmp_path / "models" / "intent_router.joblib"
    examples = [
        ("find files in the workspace", "search_files"),
        ("look for a file", "search_files"),
        ("open and read this document", "read_file"),
        ("show me the contents of a file", "read_file"),
    ]

    router = IntentRouter(model_path).train(examples)
    loaded_pipeline = load(model_path)
    vectorizer = loaded_pipeline.named_steps["tfidf"]
    classifier = loaded_pipeline.named_steps["classifier"]

    assert model_path.is_file()
    assert isinstance(vectorizer, TfidfVectorizer)
    assert vectorizer.analyzer == "char_wb"
    assert vectorizer.ngram_range == (2, 4)
    assert isinstance(classifier, LogisticRegression)
    assert classifier.class_weight == "balanced"
    assert router.predict("find a file") == "search_files"
    assert IntentRouter(model_path).predict("read the document").startswith("read_file")


def test_intent_router_can_train_from_positive_local_feedback(tmp_path):
    log_path = tmp_path / "router.jsonl"
    entries = [
        {"event": "user_prompt", "job_id": "good-job", "prompt": "search the workspace"},
        {"event": "tool_predicted", "job_id": "good-job", "tool": "search_files"},
        {"event": "user_feedback", "job_id": "good-job", "feedback": "good"},
        {"event": "user_prompt", "job_id": "bad-job", "prompt": "read the document"},
        {"event": "tool_predicted", "job_id": "bad-job", "tool": "search_files"},
        {"event": "user_feedback", "job_id": "bad-job", "feedback": "bad"},
    ]
    log_path.write_text("".join(json.dumps(entry) + "\n" for entry in entries), encoding="utf-8")
    router = IntentRouter(tmp_path / "router.joblib")
    examples = router._feedback_examples(log_path, {"search_files", "read_file"})

    assert examples == [("search the workspace", "search_files")]


def test_intent_router_feedback_loader_skips_missing_or_malformed_logs(tmp_path):
    router = IntentRouter(tmp_path / "router.joblib")
    missing = tmp_path / "missing.jsonl"
    malformed = tmp_path / "malformed.jsonl"
    malformed.write_text(
        '{invalid json}\nnull\n{"event":"tool_predicted","job_id":[],"tool":[]}\n',
        encoding="utf-8",
    )

    assert router._feedback_examples(missing, {"search_files"}) == []
    assert router._feedback_examples(malformed, {"search_files"}) == []


def test_intent_router_requires_at_least_two_intents(tmp_path):
    with pytest.raises(ValueError, match="at least two intents"):
        IntentRouter(tmp_path / "router.joblib").train([("search files", "search_files")])
