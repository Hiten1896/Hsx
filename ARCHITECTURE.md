# HSX System Architecture

## Purpose and status

HSX is a local-first Debian "System-as-Body" node: host interfaces provide bounded
inputs and outputs to the agent, while the policy layer remains responsible for
authorization. "Body" is an operational metaphor, not a claim of sentience or
independent agency.

Phases 1–20 are the current implementation baseline described in [README.md](./README.md).
The hardware and sensory modules below are **planned** for Phases 21–23; this document
specifies their intended boundaries and is not evidence that they are already available.

## Existing runtime foundation

- The local API and orchestrator route requests to an LLM and provide System-as-Body
  context. The authenticated `/api/body` endpoint reports host interface availability.
- Local TTY and voice-listener requests may target `local_host`; remote browser requests
  are restricted to `client_browser`.
- The opt-in voice listener uses WebRTC VAD and local Whisper transcription. The webcam
  can be accessed through the existing host-media path at `/dev/video0`.
- Device capabilities and dangerous operations are governed by HSX policy and the
  separate root-owned policy broker. The agent service does not receive a root shell.
- Existing audio/video actions and system-control actions retain their present consent,
  audit, and service-account permission boundaries.

## Planned component layout

```text
Host devices
  microphone ──> local acoustic pipeline ─────────┐
  /dev/video0 ─> vision/presence pipeline ────────┤
  USB serial ──> hardware workbench / ESP32 bridge ┤
  panic input ─> independent reflex controller ───┤
                                                  v
                                      event and device adapters
                                                  |
                              policy / consent / audit / kill switch
                                                  |
                            orchestrator + bounded body/telemetry context
                                                  |
                                      LLM and local actuators
```

The adapters should publish typed, timestamped events and capability-scoped actions.
Sensor processing and safety reflexes must not depend on an LLM response to operate.
Only the minimum event summary needed for a user-facing action should enter the
orchestrator context; raw sensor streams stay local and ephemeral by default.

## Phase 21: hardware workbench and ESP32-S3 bridge

### Host hardware workbench

- Discover serial candidates under `/dev/ttyACM*` and `/dev/ttyUSB*`, report stable
  device identity and availability, and handle device disconnects without blocking HSX.
- Build firmware in a per-job staging directory using explicitly installed
  `arduino-cli` or `esptool`. Do not install packages or fetch board cores automatically
  as a side effect of a build request.
- Flash only a selected, detected device and a reviewed build artifact. Require policy
  authorization and explicit confirmation for every flash operation, and audit the
  chosen device, tool, artifact hash, result, and requester.
- Grant the service account access only to configured serial devices through a dedicated
  device group or narrowly scoped udev rules. Do not solve access failures with root,
  unrestricted device permissions, or a general-purpose privileged command path.
- Treat firmware and tool output as untrusted input. Bound build time, output size, and
  staging space; distinguish compilation from the irreversible device-write step.

### ESP32-S3 serial-to-BLE co-processor

The host talks to the ESP32-S3 over its USB serial interface. The ESP32-S3 owns its BLE
radio and exposes a narrow GATT peripheral service; HSX does not assume the Debian host
has a Bluetooth adapter. The bridge protocol should define:

- Versioned framing with message type, request ID, bounded payload length, and integrity
  check; reject malformed, oversized, unknown, and out-of-sequence messages.
- Allowlisted BLE operations and explicit timeouts, cancellation, connection state, and
  errors. Do not expose a transparent arbitrary serial shell or arbitrary BLE command
  tunnel to the LLM.
- Pairing/bonding and peer identity policy on the ESP32-S3, with credentials kept out of
  prompts, logs, and returned tool output.
- A reconnect strategy that reports loss of the co-processor and fails closed rather
  than silently treating BLE operations as successful.

**Phase 21 acceptance:** enumerate a permitted serial board; compile a known firmware
artifact without flashing; flash only after explicit confirmation; verify audited
success/failure and disconnect handling; exercise version mismatch and malformed-frame
rejection; and demonstrate BLE peripheral operations through the ESP32-S3 with no host
Bluetooth adapter.

## Phase 22: core sensory and control layer

### Dual acoustic summoning

Extend the existing opt-in listener with a local acoustic pipeline:

1. WebRTC VAD gates speech activity.
2. A local wake-word engine detects the configured phrase.
3. A configurable two-clap signature provides an alternate wake signal.
4. Only after a wake event does the listener capture a bounded command window for local
   transcription and submit recognized text to HSX.

All detectors run locally. Microphone use remains explicitly enabled and visibly
reported; raw buffers are transient and discarded after detection/transcription. The
clap detector must have tunable timing/energy thresholds and must not submit ambient
audio as a command on its own.

### Vision and gesture commands

An opt-in OpenCV adapter may consume `/dev/video0` for configured hand gestures and
motion-based presence. Convert frames to short-lived local detections; do not retain
frames or run face identification as part of gesture recognition. Gesture actions map
to a small, user-configured allowlist and pass through the same policy checks as other
actions. The adapter reports camera unavailable/permission errors explicitly and
releases the device on stop, cancellation, and failure.

### Embodied status reporting

Add a read-only telemetry provider that samples CPU use, available temperature readings,
and registered subsystem health. A status request should attach a fresh, bounded
snapshot and sample timestamp to the orchestrator context, so the LLM can explain the
readings conversationally without inventing missing values. Report unsupported sensors
as unavailable; do not infer temperature or component health. Telemetry collection must
not grant the model a general process, file, or host-inspection capability.

**Phase 22 acceptance:** test wake-word and two-clap paths independently, microphone
opt-in and shutdown behavior, allowed/denied gestures and camera cleanup, and a status
answer whose CPU/temperature/health fields match the snapshot (including unavailable
and stale readings).

## Phase 23: organism and reflex layer

### Ambient acoustic recognition

Run an optional local classifier for a user-configured set of room cues such as
footsteps, knocks, and doors. Emit a label, confidence, and timestamp—not retained audio.
Use confidence thresholds, cooldowns, and an enable/disable control to reduce false
events. This detector is separate from wake-word and command transcription paths.

### Gaze-aware presence and power states

Use local face detection/tracking from `/dev/video0` only to estimate desk presence;
do not identify people. Presence may drive configurable HSX session lock/unlock and
power-state policies. Default behavior should lock the HSX session, not the operating
system login; OS-level locking requires an explicit administrator-configured adapter.
Never unlock a protected session solely on face presence.

### Tactile hardware reflexes

Support a dedicated GPIO or microcontroller panic input that reaches an independent
reflex controller. On activation, immediately cancel active audio/video capture and
playback without waiting for the API, orchestrator, or LLM; acknowledge the trigger,
enter a safe latched state, and audit the event. Reset requires an explicit local action.
The panic path must remain available when the network, model, or main HSX process is
unresponsive. Validate electrical polarity, debounce, startup state, and disconnect
behavior for each supported board; fail safe if the controller state is unknown.

### Proactive situational dialogue

Allow configured thresholds over repeated compiler failures and actionable subsystem
alerts to create a proposal for a spoken or displayed intervention. Require cooldowns,
deduplication, quiet hours, and an owner-configurable enable switch. Proactive messages
are informational and must not trigger tools, modify files, flash firmware, or bypass
consent. Record the triggering event and delivery decision without retaining sensor
content.

**Phase 23 acceptance:** verify ambient cue thresholds and cooldowns, presence-based HSX
locking without face identification or automatic unlock, panic cancellation while the
main process is unavailable, explicit latched reset, and proactive alert rate limits
and quiet hours.

## Cross-cutting requirements

- **Privacy:** sensors are opt-in, have an observable active state, process locally, and
  discard raw samples unless the owner explicitly configures a separate retention
  policy.
- **Authorization:** hardware writes, session changes, and actuator actions use
  capability-scoped policy decisions. Detection and reporting do not imply authority
  to act.
- **Least privilege:** the Debian service remains unprivileged. Device access is
  provisioned per interface; privileged setup belongs to an administrator or the
  existing policy broker.
- **Reliability:** device loss, stale telemetry, unsupported sensors, malformed bridge
  messages, and model unavailability are explicit states, never success-shaped
  fallbacks.
- **Auditability:** record policy decisions, device identity, action outcome, and
  relevant timestamps without logging credentials, raw audio, or video frames.
- **Safety priority:** local stop/panic paths preempt queued work and ordinary actions;
  the LLM is never in the critical path for stopping an actuator.
