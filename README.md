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
- Direct, structured local-host tools for system information, disk usage, network
  status, USB devices, and no-overwrite text-file creation in the shared workspace.
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
- A bounded NetworkX knowledge graph of local Python symbols, imports, calls, and
  Markdown links, persisted in `data/knowledge_graph.json` and retrieved as untrusted
  context alongside RAG results.
- A Planner/Coder/Critic flow for non-sensitive complex plans; proposals are reviewed
  before the normal policy-gated executor handles any tools.
- Successful non-sensitive execution traces exported as redacted ShareGPT JSONL, plus
  a persistent dynamic-tool registry limited to syntax-checked, vetted pure operations.
- Opt-in, low-resolution camera/RTSP and headless Motion process wrappers, confirmation-
  gated Gemini still-image context, and asynchronous Piper/ALSA notification cues.
- A policy broker and audit trail for dangerous or administrative operations, plus
  Docker or fail-closed Linux network-namespace/resource-limited code execution.

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

Autonomy is controlled by `HSX_AUTONOMY=auto|ask|strict` (default `auto`). SAFE
actions run with audit logging, NORMAL actions run silently in `auto` and `strict`
but ask in `ask`, DANGEROUS actions always require approval, and BLOCKED actions
never run. Approval requests persist across restarts and are resumed from the stored
tool call. See [docs/approvals.md](docs/approvals.md) for action tiers and response
handling.

## Open Hsx from your phone or laptop

Hsx is designed to run as one product across a browser and the terminal. On the same
host, open the local web UI for a richer mobile-friendly experience or run the
terminal client over SSH for keyboard-first work on the HP itself.

- Browser: `http://127.0.0.1:8000` or the Tailscale URL published by `tailscale serve`.
- Terminal: `python cli.py` from the project environment; it keeps the local execution
  target and the same approval language as the web client.
- Access rules: same-origin requests are allowed only for configured hosts; no-origin
  requests require a bearer token, and all browser traffic keeps credentials out of the
  URL and logs. Set `HSX_REMOTE_TOKEN` in the CLI/TTY environment when connecting
  without a browser Origin header.
- Data stays local by default: feedback is stored on this computer only unless the user
  opts in to a broader export.

### Feedback and local export

Ratings and optional notes are stored in the Hsx data directory on this computer only. The web Settings panel shows the saved count, can disable saving, and can delete all saved feedback after confirmation. Exports redact secrets, email addresses, phone numbers, and long tokens by default:

```sh
python tools/export_feedback.py --format jsonl --since 2025-01-01
```

By default, each export contains only the last user prompt and assistant response. Full-conversation export and file-content export require their respective Settings opt-ins; file contents additionally require `--include-file-contents`. Feedback is never sent to an external service.

## Web vs terminal at a glance

| Surface | Best for | Shared behaviors |
| --- | --- | --- |
| Web UI | Mobile, screenshots, artifact browsing, uploads, camera-safe views | Same chat flow, approvals, status chips, and human-readable prompts |
| Terminal UI | SSH sessions, direct local work, narrow consoles, scripting | Same local execution target, plain-English decisions, and job recovery |

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

Groq model IDs are verified against the authenticated live model catalog. If
`GROQ_MODEL` is unset, Hsx selects a chat-capable ID from that catalog; if the
configured ID is unavailable, the CLI/server prints the available IDs and
suggests setting `GROQ_MODEL` in `.env`.

`HSX_PROVIDER` selects the preferred provider and `HSX_FALLBACKS` supplies an ordered
fallback list. If they are empty, Hsx discovers providers in its built-in order and uses
the credentials that are present. Provider quotas, model names, and availability are
controlled by the provider and can change independently of Hsx.

The Anthropic SDK is included in the core dependency profile for compatibility, but it
is loaded only when the Anthropic provider is used. The other hosted providers use the
shared HTTP implementation.

### Known provider failure messages

| Provider | Failure | Resolution |
| --- | --- | --- |
| Gemini | `Role 'function' is not supported` | Tool results are converted to `functionResponse` parts in a `user` turn. |
| Gemini | `function_declarations...items missing` | Array schemas are sanitized recursively and receive an item schema. |
| Groq | `model_not_found` / HTTP 404 | Hsx refreshes the authenticated model catalog; set `GROQ_MODEL` to an available ID if needed. |
| Gemini or Groq | HTTP 400 / conversation-format rejection | Hsx displays a short plain-English error; inspect the server log for the redacted provider response. |

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
| Server | `HSX_HOST`, `HSX_PORT`, `HSX_ALLOWED_ORIGINS`, `HSX_ALLOWED_HOSTS`, `HSX_REMOTE_TOKEN` | Bind address, exact browser origins and hosts, and bearer auth for requests without an Origin |
| Mesh | `HSX_MESH_HOST`, `HSX_MESH_PORT`, `HSX_MESH_TOKEN`, `HSX_MESH_TLS_CERT`, `HSX_MESH_TLS_KEY` | Optional private-LAN HTTPS node API |
| Owner approval | `HSX_OWNER_PIN` | Approval for privileged actions |
| Workspace | `HSX_WORKSPACE`, `HSX_WORK_DIR` | Source files and generated deliverables |
| Execution | `HSX_SANDBOX`, `HSX_ALLOW_DANGEROUS`, `HSX_ALLOW_SYSTEM_CONTROL` | Code isolation and administrative controls |
| Voice | `HSX_ENABLE_VOICE_LISTENER`, `HSX_VOICE_WAKE_PHRASE`, `HSX_VOICE_MODEL_PATH` | Opt-in local microphone input |
| Hardware | `HSX_ENABLE_CAMERA_STREAM`, `HSX_ENABLE_MOTION_MONITOR`, `HSX_ENABLE_VISION_CAPTURE`, `HSX_ENABLE_AUDIO_ALERTS`, `HSX_ENABLE_VOICE_ALERTS` | Opt-in camera, motion, and speaker services |
| Notifications | `HSX_VAPID_*`, `HSX_NOTIFY_*`, `TELEGRAM_*` | Web Push, in-app, and Telegram routing |

The primary API defaults to loopback. Requests with an `Origin` must match
`HSX_ALLOWED_ORIGINS`, and every request's exact `Host` value must match
`HSX_ALLOWED_HOSTS`. API requests without an `Origin` require a valid bearer token.
For direct Tailscale access, bind `HSX_HOST` to the server's Tailscale address
(preferred) or `0.0.0.0`, then set `HSX_REMOTE_TOKEN` to a random value of at
least 32 characters. Remote API requests are accepted only from Tailscale IPv4
(`100.64.0.0/10`) or IPv6 (`fd7a:115c:a1e0::/48`) peers and must include
`Authorization: Bearer <HSX_REMOTE_TOKEN>`; an unset token keeps remote access
disabled. Remote browser origins must also be listed exactly in
`HSX_ALLOWED_ORIGINS`; add their exact host values to `HSX_ALLOWED_HOSTS` as well.
The same token can be entered in the browser's access-token control for API
requests without an Origin. The restricted TLS mesh listener remains separate.

Run `sudo scripts/check.sh` on Debian to check the service, listener, health
endpoint, Tailscale/serve state, disk headroom, and clock synchronization.

### Headless Debian hardware (optional)

Grant the HSX service account only the necessary `video` and/or `audio` device
permissions. When the web server starts, its standalone camera service begins
capturing `/dev/video0` at 640x480 and 15 fps, independent of chat jobs. The
dashboard uses the loopback-only `GET /api/camera/stream` MJPEG endpoint; the
service keeps only the latest bounded JPEG frame in memory and returns 503 while
the camera or FFmpeg is unavailable. `HSX_CAMERA_DEVICE` defaults to `/dev/video0`.
Enable either
`HSX_ENABLE_CAMERA_STREAM=1` with a private-network `HSX_RTSP_URL`, or
`HSX_ENABLE_MOTION_MONITOR=1`; these two camera daemons are mutually exclusive by
default because many webcams cannot be opened by both at once. The FFmpeg publisher
pushes to an already-running RTSP server (for example, MediaMTX); FFmpeg itself does
not host the RTSP endpoint. These optional direct-device modes take precedence over
the dashboard capture process to avoid competing webcam handles. Motion snapshots are bounded to 500 files under
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
receiver name. Keep the primary server bound to localhost. If `HSX_OWNER_PIN` is
omitted, an owner PIN is generated and shown once.

Runtime Python logs and structured router/job events are batched into
`data/datasets/agent_logs.jsonl`, with one bounded rotated file. Successful,
non-sensitive ShareGPT training examples and tool trajectories are stored in
`data/datasets/sft_dataset.jsonl`; log messages and structured events redact
credential fields, and sensitive jobs are excluded from training records.
Preference candidates are conservatively labeled only when explicit test and tool
evidence exists; matching chosen/rejected runs are paired in
`data/datasets/dpo_dataset.jsonl`. Use `agent.ml.dpo_collector.export_clean_pairs`
to export validated pairs for offline alignment. The startup code-graph pass is
bounded and runs in the background; graph hints contain symbol names and paths, not
source text.

The optional mesh listener is separate from the loopback web server and is disabled
unless `HSX_MESH_HOST` is set to a private, non-loopback IP address. Enabling it
requires a TLS certificate/key and a random `HSX_MESH_TOKEN` of at least 32
characters; keep the private key readable only by the HSX service account. Remote
clients must authenticate with a token over HTTPS. The mesh exposes only node info,
telemetry, camera status/stream, job status, and task submission; it does not expose
the general local API. `GET /api/mesh/info` provides node metadata, and the optional
IPv4 multicast responder on `239.255.42.99:45800` supports discovery on the local
network (`agent.mesh.discover_nodes`). Discovery advertises only the private HTTPS
address and node identifier; all API requests still require TLS and the token. No
public or wildcard listener is started. For browser-based clients, add the exact HTTPS origin to
`HSX_ALLOWED_ORIGINS`.

The Python sandbox denies network access with a Linux network namespace and applies
CPU, address-space, file-size, and descriptor limits. If the required namespace is
unavailable, execution fails closed. Docker remains available as the isolated
`run_code` backend. This is not a filesystem container; run the HSX service as an
unprivileged account and keep proposal directories writable only by that account.
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

The web dashboard remains intended for local use. Direct remote API clients,
including `cli.py` when `HSX_REMOTE_TOKEN` is set, can connect over Tailscale
with bearer authentication. Do not expose the primary listener through Tailscale
Serve, a reverse proxy, a container port, or a public port; proxying can obscure
the peer address used to enforce the Tailscale-only policy.

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
Python. Successful, non-sensitive jobs are exported to `data/datasets/sft_dataset.jsonl` with
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
