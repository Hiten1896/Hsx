# Hsx implementation status audit

Audit date: 2026-11-07

This is a repository-evidence audit, not a claim that the current working tree
is committed or production-verified. Existing tracked and untracked changes
were preserved. **DONE + TESTED** means repository tests cover the behavior;
it does not imply live hardware or production-provider verification.

## Phase 1: Approvals and camera — DONE + TESTED

| Requirement | Status | Evidence |
|---|---|---|
| Single permission function and YAML ask-first rules; code-classified actions | DONE + TESTED | `agent/autonomy.py`, `agent/policy.py`, `config/permissions.yaml`, `broker/policy.py`, `broker/server.py`, `deploy/policy.yaml`, `tests/test_policy.py`, `tests/test_broker.py`; command parsing tests quotes, environment expansion, base64, and symlink overwrite classification. |
| Hard denies for secret exposure and disabling security controls | DONE + TESTED | `agent/autonomy.py`, `agent/policy.py`, `tests/test_policy.py`, `tests/test_phase1_security.py`. |
| Web/CLI approval UX, restart persistence, reply routing, permission settings | DONE + TESTED | `agent/approval.py`, `agent/ask_user.py`, `agent/orchestrator.py`, `agent/tui.py`, `server.py`, `web/index.html`, `web/chat.js`, `tests/test_approval_workflow.py`, `tests/test_orchestrator_approval.py`, `tests/test_browser_ui.py`. |
| One camera owner, latest-frame snapshots, session consent, stream lifecycle | DONE + TESTED | `agent/hardware/camera.py`, `agent/hardware/camera_stream.py`, `agent/hardware/motion_monitor.py`, `agent/tools/vision_capture.py`, `server.py`, `web/chat.js`, `tests/test_hardware_services.py`, `tests/test_policy.py`; ffmpeg/process and physical device checks are simulated, not live on this Windows host. |

## Phase 2: Provider reliability — DONE + TESTED

| Requirement | Status | Evidence |
|---|---|---|
| Gemini signatures retained per part and parallel calls, legacy history compatibility signature, Groq exclusion | DONE + TESTED | `agent/llm/providers.py`, `docs/provider-research.md`, `docs/research.md`, `tests/test_provider_payloads.py`, `tests/test_provider_smoke.py`; strict local fake servers require signature replay, and web-chat E2E covers both providers. |
| 503 backoff/fallback and transient provider status chip | DONE + TESTED | `agent/llm/providers.py`, `agent/llm/router.py`, `agent/llm_client.py`, `web/chat.js`, `tests/test_provider_errors.py`, `tests/test_llm_router.py`; notice is a separate status element, cleared before fallback text. |
| Live Groq model selection, failure causes, schema validation for all tools/providers | DONE + TESTED | `agent/llm/providers.py`, `agent/llm/router.py`, `tests/test_groq_catalog.py`, `tests/test_provider_payloads.py`; catalog selection and strict schemas are simulated; external provider services are not contacted by tests. |
| Suppress raw tool JSON and render friendly results | DONE + TESTED | `agent/orchestrator.py`, `tests/test_orchestrator_router.py`, `tests/test_e2e.py`; fake-provider web-chat responses are checked for user-facing text rather than raw tool payloads. |
| Web search diagnosis and query behavior | DONE + TESTED | `skills/web_search/skill.py` and `skill.json` confirm the required setting is `HSX_SEARCH_URL`; `agent/llm_client.py` prompt and `agent/orchestrator.py` route weather to the registered tool; `tests/test_orchestrator_router.py` verifies “weather in Delhi” invokes it. Search service is not live-tested. |
| Provider health probes and strict fake-provider chat E2E | DONE + TESTED | `server.py` (`/healthz/providers`), `tests/test_e2e.py`, `tests/test_provider_smoke.py`; each provider is exercised by local HTTP fakes which validate request schemas and Gemini signatures. Live provider health is not tested. |

## Phase 3: UI fixes — DONE + TESTED

| Requirement | Status | Evidence |
|---|---|---|
| Connections popover geometry, dismissal, history/activity layout and responsive states | DONE + TESTED | `web/chat.css`, `web/chat.js`, `tests/test_browser_ui.py`, `docs/screens/`; Chromium verifies popover bounds, escape/outside dismissal, 40 px history rows, full-width search, long-row truncation and no horizontal overflow at 360, 768 and 1280 px. |
| Composer controls, suggestion behavior, send/stop and keyboard behavior | DONE + TESTED | `web/index.html`, `web/chat.js`, `tests/test_browser_ui.py`; browser checks suggestion-to-send state, absence of the removed options button, shared send/stop location, Enter behavior while generating, and separate controls. |
| UI data/source audit and bounded “Checking” states | DONE + TESTED | `docs/ui-audit.md`, `server.py`, `web/chat.js`; displayed states are tied to live endpoints or browser facts, and API requests have an 8-second timeout with explicit unavailable states. |
| Server-backed history, new-chat persistence, feedback and settings tabs | DONE + TESTED | `agent/conversations.py`, `server.py`, `web/chat.js`, `tests/test_conversations.py`, `tests/test_browser_ui.py`; browser tests cover reload/another-browser history, rename/delete, feedback, and settings tabs. |

## Phase 4: Truthful capabilities and personal voice — PARTLY

| Requirement | Status | Evidence |
|---|---|---|
| Runtime capabilities tool with live checks and known gaps, no tier labels | DONE + TESTED | `agent/capabilities.py`, `agent/skill_loader.py`, `config/capability_gaps.json`, and `server.py` build the report from the current registry and probe providers, camera, audio devices, ffmpeg, disk, and workspace writability. `tests/test_capabilities.py` verifies registry updates, report statuses, gaps, and a no-tool provider health probe against a fake provider. Real provider, camera, microphone, and configured search-service availability were not tested on this host; search availability is checked by actual search requests. |
| Grouped capability UI, including limitations and setup instructions | DONE + TESTED | `agent/activity_trace.py`, `web/chat.js`, and `web/chat.css` carry and render grouped capability cards through the existing activity event. `tests/test_activity_trace.py` and `tests/test_browser_ui.py` verify the typed event payload and browser cards. |
| Hsx personal voice and forbidden-phrase constraints | DONE + TESTED | `agent/llm_client.py` directs Hsx to answer capability questions from the live report and list known gaps with setup steps while retaining the Hsx voice constraints; `tests/test_capabilities.py` checks the prompt and response with a fake provider. |

## Phase 5: Self-extending, vision, music — PARTLY

| Requirement | Status | Evidence |
|---|---|---|
| Safe capability-gap resolver with pinned catalog, approval, install, self-test and continuation | PARTLY | `agent/package_catalog.py`, `skills/capability_resolver/`, `agent/tools/dynamic_registry.py`, `tests/test_dynamic_tool_creation.py`; pinned, audited installation and end-to-end continuation are not established. |
| Image description restricted to named/attached files, resized/converted and provider-tested | PARTLY | `skills/describe_image/`, `agent/tools/vision_capture.py`, `tests/test_hardware_routing.py`; provider image-part enforcement was not verified. |
| Music search/play, bounded cache, range endpoint, player card, mobile gesture and settings updater | NOT STARTED | No music skills, authenticated audio endpoint, or player implementation found. |

## Phase 6: Web access and hardening — PARTLY

| Requirement | Status | Evidence |
|---|---|---|
| Exact allowed origins and token-gated no-Origin requests | PARTLY | `server.py`, `tests/test_phase13b_security.py`; exhaustive look-alike and deployment checks not established. |
| Mic dictation controls and safe recognition behavior | NOT STARTED | No `SpeechRecognition`/`webkitSpeechRecognition` implementation found in the web client. |
| SSE heartbeats/resume and idempotent client message IDs | PARTLY | `server.py`, `web/chat.js`, `web/api-contract.json`, `tests/test_e2e.py`; reconnect/resume behavior not established end-to-end. |
| Versioned service worker; token storage and log redaction | PARTLY | `web/service-worker.js` now versions the shell cache as `hsx-shell-v13`; `web/chat.js` and `server.py` still need a separate token/logging audit. |
| Security headers/CSP, sanitized markdown and port-conflict messaging | PARTLY | `server.py`, `web/chat.js`, `tests/test_phase13b_security.py`; full header and rendering validation not established. |
| Health endpoint, check script, rotating logs, systemd memory/restart policy | PARTLY | `server.py`, `scripts/check.sh`, `deploy/hsx.logrotate`, `deploy/hsx.service`; deployment-unit requirements are not all verified. |

## Phase 7: Feedback dataset — PARTLY

| Requirement | Status | Evidence |
|---|---|---|
| Persist exact message, provider, model and tools for ratings | PARTLY | `agent/feedback.py`, `tests/test_feedback_store.py`; full provenance completeness needs verification. |
| Redacted JSONL export with secrets/contact data removed and file contents excluded | NOT STARTED | No feedback export script was found in `scripts/`. |
| Settings to disable saving and delete stored feedback | PARTLY | `server.py`, `web/index.html`, `web/chat.js`, `tests/test_feedback_store.py`; browser workflow not verified. |

## Phase 8: New skills — NOT STARTED

| Requirement | Status | Evidence |
|---|---|---|
| Document conversion, page-referenced summarize/compare, vision OCR | NOT STARTED | No corresponding document/OCR skills were found under `skills/`. |
| Capped semantic file search backed by API embeddings and SQLite | NOT STARTED | Existing `agent/rag/` is not evidence of the requested API-embedding file-search workflow. |
| Scheduler, spreadsheet tools, camera motion, health watch and workspace backup | PARTLY | `agent/jobs.py`, `agent/hardware/motion_monitor.py`, existing CSV skill, and related tests provide partial components; the requested integrated features and tests are absent. |
| Long-running plans/progress/Stop, memory settings and 30-task eval | NOT STARTED | No complete implementation or fixed 30-task evaluation suite was found. |

## Part E: Activity trace — PARTLY

| Requirement | Status | Evidence |
|---|---|---|
| Typed tool rows, live/final verbs, icon mapping, grouping and narration | DONE + TESTED | `agent/activity_trace.py`, `agent/orchestrator.py`, `server.py`, `web/index.html`, `web/chat.js`, `tests/test_activity_trace.py`, and the Activity Trace browser test; registered tools are checked against the mapping. |
| Redacted file diffs, command details, image thumbnails, file viewer and retry | DONE + TESTED | `server.py`, `web/chat.js`, `web/chat.css`, `tests/test_activity_trace.py`, and `tests/test_browser_ui.py`; retry rechecks policy, and thumbnail paths are jailed. |
| Rich CLI rows, spinner, `/steps N`, Syntax diffs and width/no-color behavior | DONE + TESTED | `cli.py`, `requirements-core.txt`, `tests/test_cli.py`; tests render at 60, 80 and 120 columns with colors disabled. |
| Exact comparison to reference screenshots and live camera/attached-image sources | PARTLY | No files were present under `docs/design/`; `docs/screens/after-{360,768,1280}-activity-trace.png` are generated from the implemented UI. Exact screenshot matching and live device streams remain unverified. |

## Baseline verification

- Initial full-suite attempt exited 1 because Playwright Chromium was not yet
  installed; after installing the project browser runtime, full-suite runs
  passed, including Chromium interactions and viewport screenshots.
- Latest complete-suite verification: `.\.venv\Scripts\python.exe -m pytest -q`
  exited 0 after the Activity Trace browser, backend, and CLI tests were added.
- Ruff: `ruff check .` exited 0: “All checks passed!”
- `node --check web\chat.js` exited 0.
- Browser UI checks ran locally in Chromium, including Activity Trace
  screenshots at 360, 768, and 1280 px. Live camera/audio hardware and
  external provider behavior were not verified; device/provider services
  are simulated in tests.
