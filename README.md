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
- Phase 18: shared workspace, ask-me flow, report skill, notifications, and PWA complete

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

### Headless Debian boot automation

To enable both TTY1 auto-login and a systemd service that launches the project at boot, run the repo setup script once from the project root as root:

```bash
sudo bash scripts/setup_systemd.sh
```

This script verifies that the project contains the expected entries (`main.py`, `venv/bin/activate`, and `.env`), writes a `hsx.service` unit under `/etc/systemd/system/`, enables it with `Restart=always` and `RestartSec=5`, and redirects logs to `logs/hsx_service.log`. It also writes the TTY1 override file at `/etc/systemd/system/getty@tty1.service.d/override.conf` so the configured user is auto-logged in without a password on boot.

If you want to customize the service user or autologin account, set `HSX_AUTO_LOGIN_USER` before running the script, for example:

```bash
sudo HSX_AUTO_LOGIN_USER=hsx bash scripts/setup_systemd.sh
```

After installation:

```bash
sudo systemctl status hsx.service
sudo journalctl -u hsx.service -f
```

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
- Shared-workspace reads resolve inside `HSX_WORKSPACE`; generated files are confined to `HSX_WORK_DIR`, with traversal and symlink escapes rejected. Every task output is placed in a dated folder and uses collision-safe names.
- Agent-facing file browsing and downloads are bearer-token protected. Notifications contain titles and job identifiers only; workspace file contents are never sent in notification payloads.
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

### Local system administration control

The `system_execute` skill is a dangerous, capability-scoped tool for host-level administration on Debian 13. It can execute shell commands, inspect files and directories outside the workspace, read/write system config files, and inspect processes, services, and resource usage. By design it requires explicit owner approval and audit logging through the policy engine. The environment toggle `HSX_ALLOW_SYSTEM_CONTROL=1` or `HSX_ALLOW_DANGEROUS=1` can enable a trusted local override for administrative workflows, but the default remains locked behind policy checks.

## Roadmap additions

### Phase 4b: Advisory + informed-consent flow

Skills may declare an optional plain-language `advisory` (under 300 characters) and `requires_acknowledgement`. When present, the policy engine will pause and request explicit “I understand, proceed” or “Cancel” consent with the tool, arguments, and advisory. Consent is shown once per session per skill unless arguments materially change; dangerous skills always ask. Decisions are appended to `logs/audit.jsonl`. The planned `download_media` background skill will use `yt-dlp`, save only under `outputs/`, and enforce file-size and duration limits.

## Phase 18 shared workspace

On Debian, set `HSX_WORKSPACE=/home/hsxshare` and `HSX_WORK_DIR=Hsx Work`. The work directory must be a dedicated subdirectory of the workspace, not the workspace root itself. The Windows development default is the repository workspace, preserving local testing; set both variables to use another directory. Hsx reads supported files anywhere in the workspace and writes deliverables only under `Hsx Work/YYYY-MM-DD HHMM <task>/`. It never deletes or overwrites an existing file. Workspace paths reject absolute paths, traversal, and symlinks that escape the workspace.

The `workspace` skill lists, typo-tolerantly searches, describes, and reads text, Markdown, CSV, DOCX, XLSX, and PDF files. The authenticated Files panel provides workspace search, text/PDF/image preview, byte-range downloads, and result retrieval. The `report` skill extracts DOCX/PDF layout details, reads CSV/XLSX/DOCX/PDF data, and creates a PDF with ReportLab. PDF-template output follows section order and approximates typography and pagination; it is not pixel-perfect. When source/template matching is ambiguous, Hsx asks in the web interface. Questions are checkpointed in SQLite, expire after the configured question timeout, and are limited to three per job.

### Shared-folder permissions

Run Hsx as the dedicated `hsx` account. Grant it read/traverse access across the Samba share, then grant write access only to the results directory. For an existing share, review and apply ACLs as the administrator:

```bash
sudo setfacl -Rm u:hsx:r-X /home/hsxshare
sudo setfacl -Rdm u:hsx:r-X /home/hsxshare
sudo setfacl -Rm u:hsx:rwX "/home/hsxshare/Hsx Work"
sudo setfacl -Rdm u:hsx:rwX "/home/hsxshare/Hsx Work"
```

Keep the Samba owner's existing ownership and write permissions. Confirm the effective permissions with `getfacl` before starting the service; the operating-system ACL and Hsx's path jail are separate protections.

### Web notifications and installable app

Install dependencies, then generate the VAPID key pair into `/etc/hsx/secrets.env` with `sudo sh deploy/generate-vapid.sh`. The script writes the private key only to the secrets file and sets it to `root:hsx` mode `0640`. Add the secrets file as an optional systemd environment file, restart Hsx, then enable push on each device from the Notifications panel. Device subscriptions are bearer-token protected and limited to approved HTTPS push-service hosts. Routing, quiet hours (`HSX_QUIET_HOURS=22:00-07:00`), and the all-devices/recent-device mode are configurable; Telegram is an optional backup for health alerts and heartbeat events.

To reach the web app only over Tailscale, keep Hsx bound to loopback and use Tailscale Serve without opening another port:

1. In the Tailscale admin console, enable HTTPS certificates for the server's tailnet.
2. On the server, run `sudo tailscale serve --https=443 http://127.0.0.1:8000`.
3. Open the displayed `https://<machine>.<tailnet>.ts.net` URL on the owner's laptop or phone.

Chrome and Edge support web push by default. Brave blocks push unless **Use Google Services for push messaging** is enabled in Brave settings. Push notifications contain a title only; clicking opens the relevant Hsx job.

### Phase 18 acceptance test

1. Put two files in the Samba share (for example, a DOCX template and an XLSX data file).
2. In the Hsx web interface, send a deliberately informal request such as: `mak a repot frm file1 frmt n file2 data pdf`.
3. Hsx posts a one-line restatement. If it cannot confidently identify the template and data file, it displays a question card with options and a text reply.
4. The PDF appears in a dated folder under `Hsx Work/`; an existing file is never replaced.
5. A push notification arrives on subscribed phone/laptop devices. Clicking opens the exact job or result; notification payloads contain no file contents.
6. Review `logs/audit.jsonl` for the job start, tool decisions, question/answer transitions, and completion.

## Architecture

- `HSX_WORKSPACE/`: shared source material, treated as read-only by Hsx
- `scratch/`: temporary code and intermediate work
- `HSX_WORK_DIR/`: finished files shown in the Files panel
- `memory/`: durable user/project facts
- `skills/*/skill.json + skill.py`: self-contained plugins discovered at startup

Adding a plugin does not require changes to the orchestrator. A skill only needs metadata in `skill.json` and a `run(**kwargs) -> dict` function in `skill.py`.