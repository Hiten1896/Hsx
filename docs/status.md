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

## Phase 3: UI fixes — PARTLY

| Requirement | Status | Evidence |
|---|---|---|
| Connections popover geometry, dismissal, history/activity layout and responsive states | PARTLY | `web/chat.css`, `web/chat.js`, `tests/test_browser_ui.py`, `docs/screens/`; browser tests execute in Chromium at 360, 768, and 1280 px, but this row still needs requirement-by-requirement audit. |
| Composer controls, suggestion behavior, send/stop and keyboard behavior | PARTLY | `web/index.html`, `web/chat.js`, `tests/test_browser_ui.py`, `tests/test_ui_contract.py`; browser suite passed in Chromium, but requirement-by-requirement coverage remains to be confirmed. |
| UI data/source audit and bounded “Checking” states | PARTLY | `docs/ui-audit.md`, `server.py`, `web/chat.js`; not all states have browser verification. |
| Server-backed history, new-chat persistence, feedback and settings tabs | PARTLY | `agent/conversations.py`, `server.py`, `web/chat.js`, `tests/test_conversations.py`, `tests/test_browser_ui.py`; browser suite passed in Chromium, with broader behavior still marked partly pending review. |

## Phase 4: Truthful capabilities and personal voice — PARTLY

| Requirement | Status | Evidence |
|---|---|---|
| Runtime capabilities tool with live checks and known gaps, no tier labels | NOT STARTED | No `capabilities` skill or equivalent registry-backed capability report was found in `skills/` or `agent/`. |
| Grouped capability UI, including limitations and setup instructions | NOT STARTED | No capabilities-card implementation found in `web/`. |
| Hsx personal voice and forbidden-phrase constraints | PARTLY | `agent/llm_client.py`; dedicated prompt/fake-provider tests were not found. |

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
| Versioned service worker; token storage and log redaction | PARTLY | `web/` and `server.py`; no service worker was found; token handling requires further verification. |
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

## Baseline verification

- Initial full-suite attempt exited 1 because Playwright Chromium was not yet
  installed; after installing the project browser runtime, full-suite runs
  passed, including Chromium interactions and viewport screenshots.
- Latest complete-suite verification: `.\.venv\Scripts\python.exe -m pytest -q`
  exited 0 after Phase 2 changes and the strict fake-provider web-chat test.
- Ruff: `.\.venv\Scripts\ruff.exe check .` exited 0: “All checks passed!”
- `node --check web\chat.js` exited 0.
- Live hardware and external provider behavior were not verified; camera and
  provider services are simulated in tests.
