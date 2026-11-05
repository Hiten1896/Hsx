# Hsx

Hsx is a local-first workspace and AI agent for turning conversations into useful,
auditable work. It combines a browser interface, a local terminal client, persistent
jobs and memory, a discovered skill system, shared files, notifications, and explicit
approval boundaries for actions that can affect the host or external services.

Hsx is designed to run on a machine you control. The web server binds to loopback by
default, workspace files stay under configured directories, and the model is treated as
an untrusted decision-maker that must operate through bounded tools and policy checks.

## Current state

Hsx is a working local-first assistant platform rather than only a prompt-and-response
demo. The implemented runtime currently provides:

- A loopback-only browser workspace with streaming chat, job history, file browsing,
  previews, downloads, output retrieval, and an installable PWA shell.
- A local TTY client with streamed responses, follow-up questions, job cancellation,
  and the same API and policy boundaries as the browser client.
- Automatic discovery of configured LLM providers with ordered fallback routing and a
  provider-neutral event stream.
- Optional multi-model answer panels for text-only requests: independent drafts are
  produced in parallel and a configured judge synthesizes the result.
- A persistent SQLite-backed job queue that can pause for an answer or approval and
  resume without losing the task state.
- Persistent user/project facts with explicit remember, recall, and forget operations.
  Retrieved memory is marked as untrusted context before it reaches the model.
- A plugin-like skill system. Skills are discovered from `skill.json` metadata and a
  `skill.py` implementation, so adding a normal skill does not require editing the
  orchestrator.
- Document, spreadsheet, PDF, presentation, report, notes, file, web-fetch, web-search,
  media, video, memory, and workspace-oriented skills, subject to their dependency and
  policy requirements.
- Safe generation of new temporary tools through the `tool_creator` skill. Generated
  tools are validated before registration and are not silently promoted into permanent
  production skills.
- Notifications through the web interface and optional Web Push and Telegram channels.
  Notification payloads contain titles and identifiers, not workspace file contents.
- Capability-scoped device pairing and a body-status view for the host interfaces that
  Hsx is allowed to use.
- Optional local voice input using Vosk and WebRTC VAD, with wake-phrase gating and
  transient audio handling.
- Guarded local media actions, including browser media routing and loopback-only host
  audio/camera routing.
- Advisory Isolation Forest anomaly telemetry, resource and workload forecasting, and
  untrusted retrieval of similar successful tasks for few-shot context.
- Low-footprint Markov intent prefetching, MinHash/LSH semantic caching for safe
  tool-free replies, uncertainty-triggered cloud routing, and hierarchical planning
  with policy-checked step execution and bounded reflection retries.
- A streaming, SQLite-backed workspace RAG index using local hashed n-gram vectors,
  redacted `.py`/`.md`/`.json` chunks, and bounded untrusted context retrieval.
- A Planner/Coder/Critic flow for non-sensitive complex plans; proposals are reviewed
  before the normal policy-gated executor handles any tools.
- Successful non-sensitive execution traces exported as redacted ShareGPT JSONL, plus
  a persistent dynamic-tool registry limited to syntax-checked, vetted pure operations.
- Opt-in, low-resolution camera/RTSP and headless Motion process wrappers, confirmation-
  gated Gemini still-image context, and asynchronous Piper/ALSA notification cues.
- A policy broker and audit trail for dangerous or administrative operations, plus
  Docker-first isolation for model-requested code execution.

The system is still an actively developed personal project. Hardware co-processors,
general visual awareness, ambient sound classification, and independent hardware
panic/reflex controls are design directions, not claims about the current runtime.

## How it works

```text
Browser or local CLI
        |
        v
Loopback-only local API
        |
        +--> job queue and question/approval checkpoints
        +--> policy engine, audit log, and kill switch
        +--> skill loader and bounded tool execution
        +--> LLM router and normalized streaming events
        +--> workspace, memory, notifications, and device adapters
```

The model can propose a tool call, but the tool implementation and policy layer decide
whether that call is available, requires confirmation, or must be denied. Host-level
operations are further separated behind the policy broker and the operating-system
permissions of the Hsx service account.

## Supported model providers

Configure one or more providers in `.env`. The router discovers configured credentials;
you do not need to install a separate SDK for every provider.

| Provider | Configuration | Transport |
| --- | --- | --- |
| Gemini | `GEMINI_API_KEY`, `GEMINI_MODEL` | Gemini HTTP API |
| Groq | `GROQ_API_KEY`, `GROQ_MODEL` | OpenAI-compatible HTTP API |
| Grok/XAI | `GROK_API_KEY` or `XAI_API_KEY`, `GROK_MODEL` | OpenAI-compatible HTTP API |
| OpenRouter | `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` | OpenAI-compatible HTTP API |
| OpenAI | `OPENAI_API_KEY`, `OPENAI_MODEL` | OpenAI-compatible HTTP API |
| Custom endpoint | `OPENAI_API_KEY`, `OPENAI_COMPATIBLE_URL` | OpenAI-compatible HTTP API |
| Anthropic | `ANTHROPIC_API_KEY`, `HSX_MODEL` | Anthropic SDK |
| Ollama | `OLLAMA_URL`, `OLLAMA_MODEL` | Optional local provider; set a model name to include it in provider selection |

Configured Gemini, Groq, and Ollama providers are dynamically ordered by a
Thompson-sampling selector. Provider success, failure, latency, and response
feedback are stored in `data/provider_bandit.json`; transient 503 and timeout
errors fall through to the next configured provider.

`HSX_PROVIDER` selects the preferred provider and `HSX_FALLBACKS` supplies an ordered
fallback list. If they are empty, Hsx discovers providers in its built-in order and uses
the credentials that are present. Provider quotas, model names, and availability are
controlled by the provider and can change independently of Hsx.

The Anthropic SDK is included in the core dependency profile for compatibility, but it
is loaded only when the Anthropic provider is used. The other hosted providers use the
shared HTTP implementation.

## Installation

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

Open <http://127.0.0.1:8000> after startup. The API accepts local loopback requests
without a bearer token; keep the server bound to loopback unless you add an external
authentication boundary.

### Dependency profiles

`requirements.txt` is the normal installation entry point. It includes:

- `requirements-core.txt`: runtime dependencies for the API, agent, skills, documents,
  notifications, and provider support.
- `requirements-dev.txt`: pytest, Ruff, and coverage tools.

Feature-specific dependencies are intentionally separate because they can be large,
platform-sensitive, or require system packages and downloadable models:

```bash
pip install -r requirements-media.txt    # media processing and downloads
pip install -r requirements-memory.txt   # persistent vector retrieval backend
pip install -r requirements-voice.txt    # local speech recognition and VAD
```

The optional profiles are independent. Install only the profiles needed by the skills
you intend to use.

## Configuration

Copy `.env.example` to `.env` and fill only the settings you need. Important groups are:

| Group | Variables | Purpose |
| --- | --- | --- |
| Provider | `HSX_PROVIDER`, `HSX_FALLBACKS`, provider API keys and model names | Select and configure LLMs |
| Server | `HSX_HOST`, `HSX_PORT`, `HSX_ALLOWED_ORIGINS` | Bind address, port, and browser origins |
| Owner approval | `HSX_OWNER_PIN` | Approval for privileged actions |
| Workspace | `HSX_WORKSPACE`, `HSX_WORK_DIR` | Source files and generated deliverables |
| Execution | `HSX_SANDBOX`, `HSX_ALLOW_DANGEROUS`, `HSX_ALLOW_SYSTEM_CONTROL` | Code isolation and administrative controls |
| Voice | `HSX_ENABLE_VOICE_LISTENER`, `HSX_VOICE_WAKE_PHRASE`, `HSX_VOICE_MODEL_PATH` | Opt-in local microphone input |
| Hardware | `HSX_ENABLE_CAMERA_STREAM`, `HSX_ENABLE_MOTION_MONITOR`, `HSX_ENABLE_VISION_CAPTURE`, `HSX_ENABLE_AUDIO_ALERTS`, `HSX_ENABLE_VOICE_ALERTS` | Opt-in camera, motion, and speaker services |
| Notifications | `HSX_VAPID_*`, `HSX_NOTIFY_*`, `TELEGRAM_*` | Web Push, in-app, and Telegram routing |

The local server accepts loopback connections without an access token and
rejects non-loopback HTTP requests. Keep the server bound to localhost. When

### Headless Debian hardware (optional)

Hardware access is disabled by default. Grant the HSX service account only the
necessary `video` and/or `audio` device permissions, then opt in through `.env`.
`HSX_CAMERA_DEVICE` defaults to `/dev/video0`. Enable either
`HSX_ENABLE_CAMERA_STREAM=1` with a private-network `HSX_RTSP_URL`, or
`HSX_ENABLE_MOTION_MONITOR=1`; these two camera daemons are mutually exclusive by
default because many webcams cannot be opened by both at once. The FFmpeg publisher
pushes to an already-running RTSP server (for example, MediaMTX); FFmpeg itself does
not host the RTSP endpoint. Motion snapshots are bounded to 500 files under
`data/snapshots/motion/`, with event metadata in `data/motion_events.json`.

Install `ffmpeg` or `fswebcam` for still captures. Set
`HSX_ENABLE_VISION_CAPTURE=1` to allow a user-requested live camera frame to be sent
to the configured Gemini provider. Local camera access still requires the existing
owner confirmation; captured frame bytes are omitted from conversation history and
the latest JPEG is stored with owner-only permissions at `data/snapshots/latest.jpg`.
Voice alerts require `HSX_ENABLE_VOICE_ALERTS=1`, a local Piper model path in
`HSX_PIPER_MODEL`, and `piper` plus `aplay`. Lightweight `aplay` tones require
`HSX_ENABLE_AUDIO_ALERTS=1`.

To install the optional AirPlay receiver on Debian, run
`sudo bash deploy/setup_airplay.sh`. It installs Shairport Sync, Avahi, and ALSA
utilities and backs up an existing Shairport configuration before replacing it.
Set `HSX_AIRPLAY_NAME` in the installer environment to customize the advertised
receiver name. Keep the server bound to localhost. When
`HSX_OWNER_PIN` is omitted, an owner PIN is generated and shown once.
The PIN is stored as a salted hash outside the agent context and is never placed in a
prompt or memory record.

## Workspace and generated files

The workspace has separate roles:

- `HSX_WORKSPACE`: source material that Hsx may read.
- `HSX_WORK_DIR`: generated deliverables exposed by the Files panel.
- `scratch/`: temporary work and isolated prototypes.
- `uploads/`: uploaded request material.
- `memory/` and `data/`: persistent facts and SQLite databases.
- `logs/`: append-only audit records and operational logs.

`HSX_WORK_DIR` must be a dedicated directory inside `HSX_WORKSPACE`. Generated work is
placed in dated task folders and uses collision-safe names; existing files are not
silently overwritten. Workspace path resolution rejects traversal and symlink escapes.
Local ML telemetry stays under `data/`: `anomalies.jsonl` contains advisory-only
outliers, `workload_history.jsonl` contains resource/job measurements, and
`intent_logs.jsonl` contains bounded, redacted successful task examples. Explicitly
sensitive tasks are excluded from experience retrieval; retrieved examples are passed
to the model only as untrusted context.

The workspace skill can list, search, describe, and read supported text and office files.
The report skill can combine DOCX, PDF, CSV, and XLSX inputs into a PDF report. If a
request does not identify a template or source file confidently, Hsx pauses and asks a
bounded question in the web interface instead of guessing.

## Safety and security model

- The local API accepts loopback requests without a bearer token and rejects
  non-loopback HTTP requests. Keep the service bound to localhost.
- Skills have policy tiers such as safe, confirm, and dangerous. Decisions are recorded
  in an append-only audit log.
- `POST /api/stop` provides a global kill switch for active work.
- Owner-sensitive actions require the separate owner approval flow, with rate limiting
  and lockout.
- `run_code` uses Docker when available with no network, resource limits, a read-only
  root filesystem, and a temporary writable directory. The subprocess fallback is
  weaker and should not be treated as equivalent isolation.
- The service does not receive a root shell. Administrative actions go through the
  typed policy broker and remain constrained by the service account and broker policy.
- Device tokens are returned only once and stored as hashes. Capabilities are allowlisted
  and can be revoked immediately.
- Browser media actions require browser permission and user interaction. Host media
  routing is limited to loopback-origin requests and cannot be selected by a remote
  browser request.
- Notifications contain minimal metadata and do not include file contents or secrets.
- Provider keys, device tokens, owner PINs, and notification secrets must remain
  in local configuration and must never be committed.

## Optional voice and host media

Voice input is opt-in. It uses a local Vosk model and WebRTC VAD, waits for the configured
wake phrase, keeps capture buffers in memory for transcription, and discards the audio.
Install the Python profile and Debian audio dependencies as needed:

```bash
sudo apt install libportaudio2 portaudio19-dev ffmpeg mpv yt-dlp
source venv/bin/activate
pip install -r requirements-voice.txt
```

Download and verify a Vosk model with an operator-provided official SHA-256 checksum:

```bash
python scripts/setup_voice_model.py \
  --sha256 <official-model-archive-sha256>
```

Test microphone availability without transcribing a file:

```bash
python -m skills.voice_input --test-microphone
```

For unattended Debian startup, the repository includes a boot-mode installer:

```bash
sudo bash scripts/enable_boot_mode.sh
```

The installer checks service-account access, the virtual environment, the Vosk model,
audio devices, and relevant device groups before enabling the systemd and listener
services. Persistent microphone listening must be deliberately enabled, and people near
the machine should know when it is active. See [deploy/README.md](./deploy/README.md)
for deployment details.

The body status endpoint (`/api/body`) reports available host interfaces and routing
context. “System-as-Body” is an operational metaphor for routing microphones, speakers,
cameras, the TTY, and local actions; it is not a claim of sentience or elevated access.

## Notifications and remote access

In-app notifications work with the local web interface. Web Push requires VAPID keys;
Telegram is an optional backup channel for selected health and heartbeat events. Routing,
quiet hours, and target selection are configurable through `.env`.

The local interface is intentionally loopback-only and has no API access token.
Do not expose it through Tailscale Serve, a reverse proxy, a container port, or
a public port.

## Diagnostics and development

Run the read-only diagnostic from the repository root:

```bash
python cli.py doctor
python -m hsx doctor
```

The diagnostic checks Python and dependency availability, redacted provider-key presence,
port reachability, voice readiness, serial access, systemd state, and broker connectivity.
Platform-specific hardware or services are reported as warnings where appropriate.

Run the test suite and lint checks with the development dependencies installed:

```bash
pytest
ruff check .
```

The main HTTP entry point is [server.py](./server.py). The local terminal client is
[cli.py](./cli.py), and the startup wrapper is [main.py](./main.py).

## Extending Hsx

A normal skill lives in its own directory under `skills/` and contains:

```text
skills/example/
├── skill.json
└── skill.py
```

The metadata declares the skill name, description, arguments, risk tier, and related
policy information. The implementation exposes `run(**kwargs) -> dict`. The loader
validates metadata and imports skills at startup.

The `tool_creator` skill can generate a temporary skill when an approved request needs a
missing capability. Generated code is checked for syntax, imports, paths, filesystem
writes, and risk metadata. It is placed in controlled scratch storage by default;
permanent promotion requires an explicit request and owner approval.

The separate `create_dynamic_tool` meta-tool registers declarations backed only by
vetted pure text operations. It does not import or execute arbitrary model-generated
Python. Successful, non-sensitive jobs are exported to `data/sft_dataset.jsonl` with
credential-like strings redacted; sensitive jobs are excluded. The optional local RAG
index uses hashed features instead of external embedding APIs and excludes hidden,
generated, dependency, and data directories from its streaming workspace scan.

The self-improvement workflow is proposal-only: prototypes remain isolated, production
files and dependencies are not changed automatically, strict tests are required, and
promotion is owner-approved.

## Repository map

```text
agent/       orchestration, LLM routing, policy, memory, jobs, devices, notifications
api/         loopback API helpers and request/state boundaries
broker/      typed policy-controlled host-action broker
interfaces/  external notification and device interfaces
skills/      discovered built-in skills
web/         browser application, PWA assets, and design notes
deploy/      Debian services and deployment scripts
scripts/     setup, boot, update, and operational utilities
tests/       unit, integration, security, and end-to-end coverage
```

See [ARCHITECTURE.md](./ARCHITECTURE.md) for deeper runtime boundaries and safety
constraints. Its hardware and sensory design material should be read as design guidance
unless the corresponding implementation is present in the repository.

## Author

- GitHub: [@Hiten1896](https://github.com/Hiten1896)
- Name: **Hiten Sharma**
