# Hsx

Hsx is a local-first creative workspace for an AI agent. It combines an auto-discovered skill architecture with a Claude-inspired chat interface, clear file zones, persistent memory, and an explicit artifact handoff.

## Author

GitHub: [@Hiten1896](https://github.com/Hiten1896)
Name: **[Full name to be added]**

## Status

- Phase 1: security and hygiene complete
- Phase 2: persistent memory complete
- Phase 3: provider-agnostic LLM layer complete
- Phase 4: policy engine and advisory consent flow complete

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

Open `http://127.0.0.1:8000`. Add `ANTHROPIC_API_KEY` to `.env` for the full LLM loop. Without a key, the workspace still runs with a small local fallback and the skills remain available for testing.

## Security model

- API responses use a CORS allowlist, defaulting to the local web UI origins.
- A bearer token is required whenever the server binds to a non-loopback host.
- `run_code` uses Docker first with no network, resource limits, a read-only root, and a temporary writable area. If Docker is unavailable, it uses a weaker subprocess fallback with a scrubbed environment and time/resource limits.
- File-writing skills use a path jail so resolved paths remain inside their declared workspace directories; traversal and symlink escapes are rejected.
- The LLM router supports Anthropic, Gemini, and Ollama with ordered fallbacks configured through `HSX_PROVIDER` and `HSX_FALLBACKS`; `/api/chat/stream` emits normalized SSE events.
- Skills default to `confirm`, support `safe` and `dangerous` tiers, and are audited in append-only JSONL. Advisory skills request explicit consent, with session-level repeat suppression; `POST /api/stop` activates the global kill switch.

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