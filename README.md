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
- Phase 14: startup, secrets, and approval hardening complete
- Phase 15: one-time web-token login complete
- Phase 16: hardened Docker code-execution isolation complete
- Phase 17: capability-scoped device registry and policy broker complete
- Phase 18: shared workspace, ask-me flow, report skill, notifications, and PWA complete
- Phase 19: System-as-Body runtime context, host-device routing enforcement, and hardware lifecycle safety complete
- Phase 20: live-streaming local CLI, provider-agnostic routing, lightweight voice input, and validated headless boot setup complete
- Dynamic tool creation: policy-gated generated skills with syntax/import/path/risk validation and live runtime registration complete
- Reliability tooling: `hsx doctor`, verified Vosk installation, microphone self-test, API service boundaries, and E2E coverage complete
- Phase 21: hardware workbench and ESP32-S3 co-processor bridge planned
- Phase 22: multimodal sensory controls and conversational system status planned
- Phase 23: ambient awareness, gaze-aware presence, hardware reflexes, and proactive dialogue planned

### Latest updates: Phases 19 and 20

**Phase 19 — System-as-Body and safe local hardware routing**

- Added a runtime body context that maps the host microphone, speakers, webcam, TTY, and command execution to their operational roles. This is a routing metaphor, not a claim of sentience or elevated ownership.
- Added authenticated `/api/body` status for host platform, interface availability, target device, and the service account's permission boundary.
- Enforced `local_host` routing on the server: only loopback-origin requests may select host peripherals; browser requests remain routed to `client_browser`.
- Kept local audio playback asynchronous and made camera capture reliably clean up the ffmpeg process and device access on success and timeout.
- Persisted successful and failed local/browser media action outcomes for job history and the web interface.

**Phase 20 — interactive CLI streaming and boot validation**

- Wired model text deltas into the `/api/chat/stream` SSE endpoint and the local TTY CLI; the CLI keeps the conversation focused with clean `You >` and `HSX >` prompts while remaining independent of the server process.
- Refactored provider startup to discover configured Gemini, Grok/XAI, Anthropic, Groq, OpenRouter, and OpenAI-compatible credentials without requiring a specific provider.
- Added an opt-in lightweight Vosk microphone listener with WebRTC VAD, wake-phrase gating, in-memory audio handling, and a daemon listener thread.
- Strengthened the unified Debian boot installer with checks for service-account access to `.env` and the virtual environment, Vosk model validation, default audio input/output availability, and `audio`, `dialout`, and `video` group setup.
- Added regression coverage for provider discovery, SSE delivery, local-only host routing, media action reporting, asynchronous audio, and camera process cleanup.

### Next development phases

Phases 21–23 are the next planned implementation phases; their modules are not yet
implemented merely by being described here. The detailed component boundaries, data
flows, safety controls, and acceptance criteria are in [ARCHITECTURE.md](./ARCHITECTURE.md).

**Phase 21 — hardware workbench and ESP32-S3 co-processor bridge**

- Discover explicitly permitted USB serial microcontrollers at `/dev/ttyACM*` and
  `/dev/ttyUSB*`; compile and flash supported firmware with `arduino-cli` or `esptool`.
- Add an authenticated, framed serial protocol to an ESP32-S3 bridge that exposes its
  Bluetooth radio to HSX as a BLE peripheral co-processor.
- Keep firmware builds staged and flashing behind policy authorization and explicit
  device/firmware confirmation; grant serial access without granting HSX root or a shell.

**Phase 22 — multimodal sensory controls and conversational status**

- Combine local VAD, wake-word detection, and a configurable two-clap signature for
  hands-free wake-up; retain the existing opt-in microphone behavior.
- Add opt-in OpenCV gesture and motion-presence processing from `/dev/video0`.
- Add a bounded live telemetry context for CPU, temperature, and subsystem health so a
  status question can receive a natural-language answer grounded in current readings.

**Phase 23 — ambient awareness, reflexes, and proactive dialogue**

- Add local ambient sound classification for configured cues such as footsteps, knocks,
  and doors, plus face-tracked desk presence and configurable HSX session locking.
- Add a hardware panic input that independently interrupts active audio/video capture and
  playback, with a safe latched state and audited reset.
- Add policy-controlled, rate-limited dialogue when configured thresholds detect repeated
  compilation failures or actionable system alerts.

### Reliability commands

Run the read-only installation diagnostic from the repository root:

```bash
python cli.py doctor
python -m hsx doctor
```

The diagnostic reports Python and dependency availability, redacted provider-key presence,
serial device access, microphone and Vosk model readiness, port 8000 reachability, systemd
unit state, and broker socket connectivity. Missing hardware or platform-specific services
are reported as warnings and never expose secret values.

Install the lightweight Vosk model with an operator-provided SHA-256 checksum:

```bash
python scripts/setup_voice_model.py \
  --sha256 <official-model-archive-sha256>
```

The installer uses a temporary directory, rejects unsafe archive paths, verifies the archive
before extraction, and atomically replaces the configured model directory. Test microphone
capture without transcribing an audio file:

```bash
python -m skills.voice_input --test-microphone
```

The HTTP entry point remains [server.py](./server.py), while authentication, chat-stream
registries, job payloads, file resolution, and device target validation are organized under
the [api/](./api) package.

### Dynamic tool creation

When an approved request needs a capability that is not present, the agent can use the
`tool_creator` skill to submit a generated `run` implementation and its `skill.json` metadata.
The creator rejects syntax errors, restricted imports and operations, filesystem writes,
invalid metadata, and dangerous risk levels. New tools are written to the dynamic scratch
area by default and registered in the active `TOOLS` and `SKILL_MAP` collections immediately;
permanent placement under `skills/` must be explicitly requested and remains policy-gated.
Generated tools cannot execute arbitrary shell commands, access sockets, mutate global state,
or write outside the controlled skill locations.

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
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python main.py
```

Open `http://127.0.0.1:8000`. Configure at least one supported provider key in `.env`; the router discovers configured providers automatically and respects `HSX_PROVIDER` and `HSX_FALLBACKS` when they are set. Gemini, Grok/XAI, Anthropic, Groq, OpenRouter, OpenAI-compatible endpoints, and local Ollama are supported. The authenticated workspace can start without a provider, but LLM-backed requests require one.

### Headless Debian boot automation

After creating `venv`, installing `requirements.txt`, and copying `.env.example` to `.env`,
run this single installer from the repository root:

```bash
sudo bash scripts/enable_boot_mode.sh
```

The installer asks for one-time consent before enabling persistent microphone listening.
It then installs/checks the host audio/video and local Vosk, sounddevice, and WebRTC VAD dependencies,
checks the selected Vosk model under the service user's account, enables the HSX
systemd service at `multi-user.target`, enables the voice-listener service, configures
passwordless TTY1 auto-login, and adds a TTY1-only CLI launcher to `~/.profile` (and
`~/.bash_profile` or `~/.bash_login` when present). `hsx.service` uses `Restart=on-failure`; SSH and other
TTY sessions do not start the CLI.

The voice service continuously uses the microphone while enabled, but only sends recognized
speech after the configured wake phrase (`HSX_VOICE_WAKE_PHRASE`, default `hey hsx`).
Captured audio is held in memory for local transcription and discarded. Its active state
is printed to the voice service journal. To disable microphone startup later:

```bash
sudo systemctl disable --now hsx-voice-listener.service
```

Set `HSX_AUTO_LOGIN_USER` to select a different Linux account. The selected account needs
access to the microphone, sound, and serial devices; the installer adds it to `audio`,
`dialout`, and `video` groups
when those groups exist. Ensure all people near the machine know that the microphone is
active.

After installation, check status and logs:

```bash
sudo systemctl status hsx.service hsx-voice-listener.service
sudo journalctl -u hsx.service -f
sudo journalctl -u hsx-voice-listener.service -f
```

TTY1 launches `python cli.py` after auto-login. The client submits prompts to the local API
with `target_device=local_host`, displays streamed model text with clean conversational
prompts, and asks for answers when a job pauses for user input.
The CLI is a separate process from the server; leaving the prompt does not stop HSX.
Other terminals and SSH logins remain normal shells. The CLI can also be started manually
with `python cli.py`.

### Local voice and hardware

Media requests carry a server-validated `target_device`: browser requests are fixed to
`client_browser`; the TTY and voice listener use `local_host`. Host audio playback uses
`mpv` (and `yt-dlp` for supported online sources). Host camera capture uses `ffmpeg` and
`/dev/video0`; outputs are saved under the active dated task folder. Browser audio and
camera requests are handed back to the browser. Audio requires a user click; camera
capture requires an explicit browser confirmation and the browser's camera permission.
Requests from non-loopback clients cannot select `local_host`, so a remote browser request
cannot trigger host audio or camera capture by changing its payload.

HSX uses a **System-as-Body** operational metaphor: the microphone is ears, speakers are
voice, `/dev/video0` is eyes, the local TTY is the terminal interface, and `system_execute`
is motor control. This is a routing/context model, not a claim of sentience. Host operations
run only with the Unix account permissions assigned to the HSX service; the service does
not acquire root privileges automatically. The authenticated `/api/body` endpoint reports
the current host interface availability and routing target.

The microphone listener is opt-in and uses frame-based WebRTC VAD plus a lightweight local Vosk
transcription. Captured audio remains in memory for transcription and is discarded; only
recognized command text is submitted to HSX and then processed by the configured LLM
provider (which may be remote). The default wake phrase is `hey hsx`.

Install optional voice and hardware dependencies on Debian:

```bash
sudo apt install libportaudio2 portaudio19-dev ffmpeg mpv yt-dlp
source venv/bin/activate
pip install -r requirements-voice.txt
```

The single boot-mode installer above enables the listener after explicit consent. It checks
that the service account can read `.env` and run the virtualenv, validates the wake phrase
and Vosk model, and verifies that PortAudio can see default input and output devices. If
`/dev/video0` exists but the service account lacks read/write access, setup warns that
camera capture may fail. For a manual service setup, set
`HSX_ENABLE_VOICE_LISTENER=1` in `.env` and install the optional listener service:

```bash
sudo cp deploy/hsx-voice-listener.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hsx-voice-listener.service
sudo systemctl status hsx-voice-listener.service
```

The supplied service unit assumes `/opt/hsx`, user `hsx`, and virtual environment
`/opt/hsx/venv`; adjust those paths/user to match the installation. Grant the HSX
service account only the needed `audio` and `video` device access. Do not enable the
listener unless people near the device know the microphone is active.

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
- The LLM router discovers configured provider keys, supports Gemini, Grok/XAI, Anthropic, Groq, OpenRouter, OpenAI-compatible endpoints, and Ollama, and emits normalized events through `/api/chat/stream`.
- Skills default to `confirm`, support `safe` and `dangerous` tiers, and are audited in append-only JSONL. Advisory skills request explicit consent, with session-level repeat suppression; `POST /api/stop` activates the global kill switch.
- Phase 11 self-improvement is proposal-only: research and prototypes stay in `scratch/self_improvement/`, dependencies and production files are not changed automatically, strict tests must pass, and approval/promotion require the separate owner PIN.
- Device pairing remains capability-scoped: pairing requires the owner PIN, device tokens are stored only as hashes and returned once, capabilities are allowlisted, every authorization is audited, and revocation is immediate. Local media capture/playback is provided separately by explicit skills; browser camera capture always requires a user confirmation and browser permission.

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

On Debian, set `HSX_WORKSPACE=/home/hsxshare` and `HSX_WORK_DIR="Hsx Work"`. The work directory must be a dedicated subdirectory of the workspace, not the workspace root itself. The Windows development default is the repository workspace, preserving local testing; set both variables to use another directory. Hsx reads supported files anywhere in the workspace and writes deliverables only under `Hsx Work/YYYY-MM-DD HHMM <task>/`. It never deletes or overwrites an existing file. Workspace paths reject absolute paths, traversal, and symlinks that escape the workspace.

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
3. If it cannot confidently identify the template and data file, it displays a question card with options and a text reply.
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

The current runtime and planned hardware/sensory expansion are specified in
[ARCHITECTURE.md](./ARCHITECTURE.md). The planned modules extend existing device routing,
orchestration, and policy controls; they do not bypass the service account's operating
system permissions or existing user-consent boundaries.

The Debian boot installer can install an hourly GitHub update timer. It is disabled by
default; enabling it requires a pinned GPG signer fingerprint and applies only signed,
fast-forward updates from the configured origin/branch. See [deploy/README.md](./deploy/README.md)
for the trust setup and operational limits.
