# Hsx

Hsx is a local-first creative workspace for an AI agent. It combines an auto-discovered skill architecture with a Claude-inspired chat interface, clear file zones, persistent memory, and an explicit artifact handoff.

## Author

GitHub: [@Hiten1896](https://github.com/Hiten1896)
Name: **[Hiten Sharma]**

## Status

- Phase 1: security and hygiene complete
- Phase 2: persistent memory complete
- Phase 3: provider-agnostic LLM layer complete
- Phase 4: policy engine and advisory consent flow complete
- Phase 5: persistent resumable job queue complete
- Phase 6: safe local skill expansion complete
- Phase 7: persona modes, browser voice, and Telegram allowlist complete
- Phase 8: bounded specialist sub-agent delegation complete
- Phase 9: deployment artifacts complete
- Phase 10: CI and quality automation complete
- Phase 11: guarded self-improvement proposal pipeline complete
- Phase 12: free-tier provider configuration complete
- Phase 13: multi-model debate and synthesis complete
- Phase 17: capability-scoped device-control foundation complete

## Setup

### Windows

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python main.py
```

### Linux

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python main.py
```

Open `http://127.0.0.1:8000`. You do not need Anthropic: configure any available provider key in `.env` and order providers with `HSX_PROVIDER` and `HSX_FALLBACKS`. Gemini, Groq, OpenRouter free models, OpenAI-compatible endpoints, and local Ollama are supported. Without a configured provider, the workspace remains usable for local testing.

### Free-tier provider setup

Copy the key slots from `.env.example` into `.env` and fill only the providers you use:

```dotenv
HSX_PROVIDER=gemini
HSX_FALLBACKS=groq,openrouter,ollama
GEMINI_API_KEY=your_gemini_key
GEMINI_MODEL=gemini-2.0-flash
GROQ_API_KEY=your_groq_key
GROQ_MODEL=llama-3.1-8b-instant
OPENROUTER_API_KEY=your_openrouter_key
OPENROUTER_MODEL=google/gemini-2.0-flash-exp:free
```

Keys are read only from `.env` and are never committed. Free-tier quotas, model names, and availability are controlled by each provider and can change; fallback order lets Hsx continue with the next configured provider.

### Multi-model answer panel

For text-only requests, `HSX_PANEL_MODE=off` is the default. Set it to `auto` to send the request to two configured providers in parallel, then send both drafts to a third configured judge for critique and synthesis. Configure `HSX_PANEL_PROVIDERS` with 2–3 provider names such as `gemini,groq,openrouter`. If fewer than two are available, Hsx safely falls back to the normal ordered router. Tool-executing turns stay single-provider so side effects are never duplicated.

## Security model

- API responses use a CORS allowlist, defaulting to the local web UI origins.
- A bearer token is required for every request. Set `HSX_AUTH_TOKEN` explicitly or let Hsx generate a 32-byte token, save it to `.env`, and print it once on first start.
- Owner approval uses a separate salted scrypt PIN hash stored outside the workspace, with rate limiting and lockout. The PIN is never put in the agent environment or memory.
- `run_code` uses Docker first with no network, resource limits, a read-only root, and a temporary writable area. If Docker is unavailable, it uses a weaker confirm-tier subprocess fallback with a scrubbed environment, temporary working directory, and best-effort network blocking; Docker is the only strong isolation.
- File-writing skills use a path jail so resolved paths remain inside their declared workspace directories; traversal and symlink escapes are rejected.
- The LLM router supports Anthropic, Gemini, and Ollama with ordered fallbacks configured through `HSX_PROVIDER` and `HSX_FALLBACKS`; `/api/chat/stream` emits normalized SSE events.
- Skills default to `confirm`, support `safe` and `dangerous` tiers, and are audited in append-only JSONL. Advisory skills request explicit consent, with session-level repeat suppression; `POST /api/stop` activates the global kill switch.
- Phase 11 self-improvement is proposal-only: research and prototypes stay in `scratch/self_improvement/`, dependencies and production files are not changed automatically, strict tests must pass, and approval/promotion require the separate owner PIN.
- Device control is capability-scoped: pairing requires the owner PIN, device tokens are stored only as hashes and returned once, capabilities are allowlisted, every authorization is audited, and revocation is immediate. The foundation does not expose unrestricted shell, browser, desktop, camera, microphone, or LAN control.

### Phase 11 workflow

1. Create a proposal with a rationale and isolated scratch workspace.
2. Prototype only reviewable text/source files; no automatic package installation or production edits.
3. Run the fixed strict pytest check with a scrubbed environment.
4. Present the passing proposal, test output, changed files, and risk summary to Hiten.
5. Require explicit owner approval before promotion; promotion is limited to `agent/`, `skills/`, `interfaces/`, or `prompts/`.

## Roadmap additions

### Phase 4b: Advisory + informed-consent flow

Skills may declare an optional plain-language `advisory` (under 300 characters) and `requires_acknowledgement`. When present, the policy engine will pause and request explicit “I understand, proceed” or “Cancel” consent with the tool, arguments, and advisory. Consent is shown once per session per skill unless arguments materially change; dangerous skills always ask. Decisions are appended to `logs/audit.jsonl`. The planned `download_media` background skill will use `yt-dlp`, save only under `outputs/`, and enforce file-size and duration limits.

## Architecture

- `uploads/`: source material, treated as read-only by convention
- `scratch/`: temporary code and intermediate work
- `outputs/`: finished files shown in the Artifacts panel
- `memory/`: durable user/project facts
- `skills/*/skill.json + skill.py`: self-contained plugins discovered at startup

Adding a plugin does not require changes to the orchestrator. A skill only needs metadata in `skill.json` and a `run(**kwargs) -> dict` function in `skill.py`.