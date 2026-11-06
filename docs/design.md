# Hsx shared design language

Hsx is the product name for both the browser workspace and terminal client. The
browser layout is inspired by the quiet, focused chat patterns common to desktop and
mobile assistants, with Hsx-specific branding and only controls backed by this repo's
API.

## Brand and layout

- Hsx uses a subdued forest-and-stone dark theme with a custom Hsx mark.
- Desktop uses a navigation and connection-status rail, a central chat, and a
  right-hand host-camera panel.
- On narrow screens a menu button opens the navigation and connection-status drawer,
  while a camera button opens the host feed, camera timing details, and generated
  artifacts. Both drawers leave the chat composer available when closed; the host
  camera is never substituted with the phone's camera.
- The navigation only presents chat and file attachment actions supported by the
  current web client. The Artifacts panel lists generated files from the host's
  `Hsx Work` output directory and links to download them. The Agent activity panel
  sits below New chat in the navigation rail and reports tasks persisted for the
  current session; it does not invent sub-agent statuses when no such work is
  reported by the backend.
- The connection rail reports API health, last-used LLM provider, host camera health,
  the current Tailnet access path, and the browser's network-online hint. “Standby”
  means there has not yet been a provider call; it is not a provider health check.

## Palette tokens

The semantic browser tokens and terminal colors are maintained in
[`web/design-tokens.json`](../web/design-tokens.json):

- background: `#111513`
- surface: `#1c231e`
- raised surface: `#242d28`
- text: `#edf2eb`
- muted: `#9aa79d`
- accent: `#b2d8b7`
- success: `#87c99a`
- warning: `#e6bd77`
- danger: `#e28e88`

The browser uses the same semantic values in CSS. Focus indicators, touch-sized
controls, responsive sizing, and reduced-motion behavior are part of the design.

## Chat and attachments

- Enter sends on desktop; Shift+Enter inserts a newline. The textarea grows to a
  bounded height and remains visible above the mobile keyboard.
- The empty chat centers one generated, time-aware greeting and the composer; after the first turn the
  composer animates to the bottom below the scrollable transcript. User messages use
  a subtle right-aligned bubble while assistant replies remain unboxed and unlabeled.
- The welcome greeting is requested once from a configured provider with the browser's
  local time-of-day context. The web heading stays empty until the generated line is
  ready, preventing an initial placeholder from being replaced. CLI clients use the
  same greeting endpoint; they show a brief ready prompt if generation is unavailable.
- The file picker accepts any file type up to 1 MB. Uploads go through `/api/upload`
  and preserve file bytes inside the Hsx workspace. Uploads live under
  `Hsx Work/Uploads` and are excluded from generated artifacts.
- Uploaded file paths are included with the prompt so workspace tools can read the
  uploaded files. Draft text and attachment references are scoped to the current
  browser tab.
- Secure browsers with SpeechRecognition expose a microphone control. Dictation adds
  text to the draft for review and never submits it automatically; unsupported
  browsers do not show a nonfunctional microphone control.
- Assistant output is sanitized before Markdown display. Copy uses the Clipboard API;
  assistant actions include browser read-aloud, job feedback, and retry. User actions
  copy or place the original prompt back into the composer for editing.
- Stop uses the existing server stop endpoint. A new chat creates a new session ID;
  it does not claim to provide a server-side conversation-history browser.

## Host camera and connections

The camera panel reads the host camera status and a timestamped MJPEG stream from
authenticated API endpoints. It never requests browser camera permission and clearly
shows Offline when the host capture service or device is unavailable. The overlay shows
the capture time for the displayed frame; the metrics show local current time and
estimated delay, corrected using the server clock response. If the tab is hidden, the
network drops, or frames stop arriving, the client aborts the old stream and reconnects
with bounded backoff to the latest host frame.
The camera heading includes a manual refresh button. Connections sit below the
sidebar's local-access controls so the live status list is at the bottom of the rail.

The Agent activity panel reads `/api/activity` for the current session and displays
persisted orchestration job status and a redacted prompt summary. The terminal client
offers `/activity` and `/artifacts`; the Textual terminal UI shows both in its activity
line. This reports Hsx jobs, not individual specialist/sub-agent execution because the
current backend does not persist per-sub-agent progress.

The primary API has no bearer-token prompt or requirement. The Tailnet indicator describes whether the page is currently served from a `.ts.net`
hostname; the browser network indicator uses `navigator.onLine` and is not a host-side
internet reachability test.

Browser chat requests explicitly target `local_host`, so tools execute on the Hsx
server. Remote callers must first pass the existing Tailscale bearer or mesh
authorization; action policy and approval gates are still enforced server-side. The
desktop/mobile notification bell reads the persisted in-app feed over authenticated,
reconnecting SSE and opens related approval or task cards without exposing raw event
payloads. Sources are real task, approval, camera-motion, anomaly, and resource events.
Camera motion is opt-in; the resource monitor can be disabled with
`HSX_ENABLE_SYSTEM_MONITOR=0`.

## Approval and prompts

Approvals and ask-user prompts use plain English and do not expose raw JSON or internal
payloads. The same approval language is used by the terminal client. Yes-like answers
authorize only the pending request; no-like answers decline it.
