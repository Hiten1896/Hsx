# Hsx AI/ML Runtime Audit

## Scope and evidence

This audit traced the implementation and call sites under `agent/ml`, the
intent router, provider selection, feedback storage, server startup, retention,
and their tests. Runtime checks use local tests and fake providers; this does
not verify the HP host, camera hardware, a network share, or external provider
behavior.

## Findings and changes

| Severity | Finding | Resolution |
|---|---|---|
| High | The documented production entry point is `python main.py`, but the seven-day maintenance worker was only started by `server.py` when run directly. Chat/feedback/training records therefore were not automatically pruned in the normal deployment. | `main.py` now runs maintenance once at startup and starts the hourly worker. A startup test covers this path. |
| High | Workload JSONL uses numeric epoch timestamps. The pruner only understood ISO strings and fell back to the file modification time, so recent appends could keep older workload records indefinitely. | Numeric timestamps are now parsed and expired records are removed by their own event time. Regression coverage uses old and recent epoch entries in a recently modified file. |
| High | The default feedback redaction removed top-level tool messages but not `tool_result` blocks nested in a user message. Serialized read/search outputs could therefore survive the default file-content exclusion. | Nested tool results are now replaced with `[TOOL_RESULT_OMITTED]`; file/code-bearing tool inputs are omitted unless file-content storage is explicitly enabled. |
| Medium | An existing intent-router model was loaded without incorporating subsequent user feedback. | The router is rebuilt from current tool descriptions, successful uncertain-intent examples, and recent thumbs-up routing examples at startup and during hourly maintenance. Router replacement is synchronized with inference. Thumbs-down does not label a replacement tool intent; it still influences provider selection and remains in feedback history. |
| Medium | Feedback records did not have a structured manifest for upload/output/tool references. | Feedback now records uploaded and generated file paths, tools used, names of tools created, and available camera capture references as metadata. Raw file bytes and camera frames are not added to this manifest. |
| High | The normal `main.py` startup called `CAMERA.start()` even when camera streaming and motion detection were both disabled. That kept capture running without a session owner. | Default startup now leaves the camera off; explicitly enabled camera/motion services and user-started sessions remain supported. A startup test verifies no default camera start. |

## Runtime inventory

| Component | Runtime use | What it learns / limitation |
|---|---|---|
| Intent router (`agent/router.py`) | Called for each chat turn; high-confidence tool predictions can bypass an LLM call. Refreshed hourly. | TF-IDF character features plus logistic regression. Trains from tool descriptions, successful uncertain-intent resolutions, and positive feedback only; it is not neural model fine-tuning. |
| Provider bandit (`agent/llm_client.py`) | Ranks configured providers and receives provider attempt outcomes and user ratings. | Online Thompson-sampling statistics affect provider choice, except cloud-priority paths that deliberately prioritize configured cloud vendors. |
| Experience memory (`agent/ml/memory.py`) | Only explicit thumbs-up feedback for nonsensitive tasks is eligible for retrieval; a later thumbs-down retracts that job's example, and a later thumbs-up restores it. Older unvoted success entries and stale indexes are ignored. | TF-IDF similarity retrieval over locally stored, redacted examples. This is retrieval personalization, not model-weight training. |
| SFT / DPO collectors | SFT examples are exported only after thumbs-up; a changed rating replaces or removes the job's SFT row. DPO preference candidates use execution evidence. | These are dataset collectors/exporters, not trainers. No in-repository process fine-tunes provider model weights from these files. |
| Semantic cache | Lookup and write are guarded in the chat loop. | It is skipped when tools, memory, or device context are present. Since the normal server supplies tools, it is intentionally conservative and may have little ordinary-chat usage. |
| Intent prefetcher | Records per-session intent transitions and asynchronously loads predicted tool metadata. | No runtime call consumes `take_prefetched_context`; the prefetched result currently has no effect on a response. |
| Workload forecaster | Records resource and completed-job samples. | Forecast methods exist, but no production caller uses their predictions to change agent behavior. Telemetry only. |
| Anomaly monitor | Started by `main.py`; samples host metrics and emits advisory notifications after model initialization. | Isolation Forest uses local system metrics; it does not change task execution or train on chat content. |
| Hierarchical planner | Used for classifier-approved multi-step file/document tasks. | LLM-generated plans and bounded step retries; not an online learned model. |

## What feedback contains and what is deliberately excluded

- Ratings, latest query/response, redacted conversation context, provider
  provenance, tool names, and the new file/tool reference manifest are kept
  locally when feedback saving is enabled.
- The feedback endpoint also forwards explicit ratings to local learning:
  thumbs-up contributes a redacted retrieval-memory and SFT example; thumbs-down
  removes that job from both sources. A later rating change updates the example.
  Unrated and sensitive jobs are not used by these feedback-learning paths.
- Previously exported SFT rows are not retroactively assigned a rating. They
  remain subject to the existing seven-day retention; no in-repository trainer
  consumes them automatically.
- The default feedback setting excludes file contents and tool outputs. The
  explicit file-content setting can include file payloads in local feedback
  storage/exports; this increases privacy risk and is not required for normal
  tool-use learning.
- Camera frames in the live service are transient. Feedback keeps camera tool
  and available snapshot-path references, but it does **not** retain camera
  footage or still-image bytes. Continuous footage is intentionally not
  copied into a training dataset.
- The main application entry point now leaves camera capture off unless an
  opt-in camera/motion service is configured or a user starts a session.
- Chat, feedback, and JSONL training/log records are subject to seven-day
  pruning; generated workspace files and explicit memory facts are retained
  separately. The retention worker summarizes recent counts before pruning;
  that summary is not model-weight training.

## Validation performed

The audit changes are covered by targeted regression tests for nested tool
output redaction, file/tool reference metadata, numeric-timestamp retention,
the production startup maintenance worker, positive-feedback router training,
feedback-rating changes in experience memory, and SFT row removal. Final
verification passed: `ruff check .`, the complete `pytest -q` suite,
`node --check web\chat.js`, and `git diff --check`. Hardware, remote storage,
camera capture, and external provider behavior were not live-tested.
