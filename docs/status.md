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
| Connections/notification dismissal, redesigned sidebars, responsive states | DONE + TESTED | `web/chat.css`, `web/chat.js`, `tests/test_browser_ui.py`, `docs/screens/`; Chromium verifies popover bounds and outside/Escape dismissal, collapsible activity cards, 40 px history rows, full-width search, long-row truncation and no horizontal overflow at 360, 768 and 1280 px. |
| Composer controls, file cards, suggestion behavior, send/stop and keyboard behavior | DONE + TESTED | `web/index.html`, `web/chat.js`, `web/chat.css`, `tests/test_browser_ui.py`; browser checks separate authenticated image/file cards, suggestion-to-send state, no composer camera or keyboard hint, shaped send control, shared send/stop location, and Enter behavior while generating. |
| Blue visual refresh, artifact filtering, and silent public web search | DONE + TESTED | `web/chat.css`, `server.py`, `agent/autonomy.py`, `tests/test_files_panel.py`, `tests/test_autonomy.py`, and `tests/test_browser_ui.py`; generated outputs exclude the internal activity trace, web search no longer requests an external-service approval, and sidebar/composer surfaces use the blue palette. |
| UI data/source audit and bounded “Checking” states | DONE + TESTED | `docs/ui-audit.md`, `server.py`, `web/chat.js`; displayed states are tied to live endpoints or browser facts, and API requests have an 8-second timeout with explicit unavailable states. |
| Server-backed history, new-chat persistence, feedback and settings tabs | DONE + TESTED | `agent/conversations.py`, `server.py`, `web/chat.js`, `tests/test_conversations.py`, `tests/test_browser_ui.py`; browser tests cover reload/another-browser history, rename/delete, feedback, and settings tabs. |

## Phase 4: Truthful capabilities and personal voice — DONE + TESTED

| Requirement | Status | Evidence |
|---|---|---|
| Runtime capabilities tool with live checks and known gaps, no tier labels | DONE + TESTED | `agent/capabilities.py`, `agent/skill_loader.py`, `config/capability_gaps.json`, and `server.py` build the report from the current registry and probe providers, camera, audio devices, ffmpeg, disk, and workspace writability. `tests/test_capabilities.py` verifies registry updates, report statuses, gaps, and a no-tool provider health probe against a fake provider. Real provider, camera, microphone, and configured search-service availability were not tested on this host; search availability is checked by actual search requests. |
| Grouped capability UI, including limitations and setup instructions | DONE + TESTED | `agent/activity_trace.py`, `web/chat.js`, and `web/chat.css` carry and render grouped capability cards through the existing activity event. `tests/test_activity_trace.py` and `tests/test_browser_ui.py` verify the typed event payload and browser cards. |
| Hsx personal voice and forbidden-phrase constraints | DONE + TESTED | `agent/llm_client.py` directs Hsx to answer capability questions from the live report and list known gaps with setup steps while retaining the Hsx voice constraints; `tests/test_capabilities.py` checks the prompt and response with a fake provider. |

## Phase 5: Self-extending, vision, music — DONE + TESTED

| Requirement | Status | Evidence |
|---|---|---|
| Safe capability-gap resolver with pinned catalog, approval, install, self-test and continuation | DONE + TESTED | `agent/package_catalog.py`, `skills/capability_resolver/`, `agent/tools/dynamic_registry.py`, `tests/test_dynamic_tool_creation.py`, `tests/test_capability_resolver.py`, `tests/test_policy.py`, `tests/test_orchestrator_approval.py`, and `tests/test_browser_ui.py`; package approval is a plain plan card, installation is pinned and restricted to the active virtual environment, mocked self-test and original-task continuation pass. Dynamic tools select vetted pure operations rather than executing generated source. Live package installation is not verified. |
| Image description restricted to named/attached files, resized/converted and provider-tested | DONE + TESTED | `skills/describe_image/skill.py` limits and resizes images to the provider cap, converts to JPEG, uses Gemini, and `agent/orchestrator.py` enforces explicit path boundaries; `tests/test_describe_image.py` and `tests/test_orchestrator_router.py` check the image part and reject unmentioned or path-confusable files. Provider and image inputs are simulated. |
| Music search/play, bounded cache, range endpoint, player card, mobile gesture and settings updater | DONE + TESTED | `agent/music_catalog.py`, `skills/music_search/`, `skills/music_play/`, `server.py`, `web/chat.js`, `web/chat.css`, and `web/index.html` implement Commons-only free-license search, capped/expiring downloads, cookie-scoped Range streaming, attribution, a non-autoplay player, and the Advanced updater routed through package approval. Fake API/downloader/browser tests cover these flows; live Commons, yt-dlp installation/download, production HTTPS, and HP audio-device checks are not verified. |

## Phase 6: Web access and hardening — DONE + TESTED

| Requirement | Status | Evidence |
|---|---|---|
| Exact allowed origins and token-gated no-Origin requests | DONE + TESTED | `server.py`, `agent/auth.py`, `config.py`, `tests/test_phase13b_security.py`; exact-origin, look-alike, opaque-origin, and bearer-token cases pass. |
| Mic dictation controls and safe recognition behavior | DONE + TESTED | `web/chat.js`, `tests/test_browser_ui.py`; browser tests cover feature detection, language selection, duplicate results, and draft-only dictation. |
| SSE heartbeats/resume and idempotent client message IDs | DONE + TESTED | `api/chat.py`, `agent/ask_user.py`, `server.py`, `web/chat.js`, `tests/test_chat_stream.py`, `tests/test_ask_user.py`, `tests/test_e2e.py`; fake-server browser tests cover reconnect/replay and deduplication. |
| Versioned service worker; token storage and log redaction | DONE + TESTED | `web/service-worker.js`, `web/chat.js`, `server.py`, `tests/test_phase13b_security.py`, `tests/test_browser_ui.py`; the token is kept in memory/session storage and HTTP logs redact query credentials. |
| Security headers/CSP, sanitized markdown and port-conflict messaging | DONE + TESTED | `server.py`, `web/chat.js`, `tests/test_phase13b_security.py`, `tests/test_browser_ui.py`; response headers, markdown rendering, and the occupied-port path are covered. |
| Health endpoint, check script, rotating logs, systemd memory/restart policy | DONE + TESTED | `server.py`, `scripts/check.sh`, `deploy/hsx.logrotate`, `deploy/hsx.service`; files and local checks are verified, but HP systemd/Tailscale operation has not been tested live. |

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
| Exact comparison to reference screenshots and live camera/attached-image sources | PARTLY | The requested reference PNGs are not present under `docs/design/`; `docs/design.md` documents the current UI, and `docs/screens/after-{360,768,1280}-activity-trace.png` are generated from the implemented UI. Exact screenshot matching and live device streams remain unverified. |

## Baseline verification

- Initial full-suite attempt exited 1 because Playwright Chromium was not yet
  installed; after installing the project browser runtime, full-suite runs
  passed, including Chromium interactions and viewport screenshots.
- Latest complete-suite verification: `.\.venv\Scripts\python.exe -m pytest -q`
  exited 0 after Phase 6 authentication, SSE, idempotency, and browser tests were
  included. The full suite includes expected platform skips.
- Ruff: `.\.venv\Scripts\ruff.exe check .` exited 0: “All checks passed!”
- `node --check web\chat.js` and `git diff --check` exited 0.
- HP deployment, Tailscale routing, systemd behavior, and camera/microphone hardware
  remain not verified from this Windows development host.
- `node --check web\chat.js` exited 0.
- Browser UI checks ran locally in Chromium, including Activity Trace
  screenshots at 360, 768, and 1280 px. Live camera/audio hardware and
  external provider behavior were not verified; device/provider services
  are simulated in tests.
