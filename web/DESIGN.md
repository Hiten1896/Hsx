# Hsx web workspace

The active browser client is the Hsx chat workspace. It uses
[`design-tokens.json`](design-tokens.json) and [`api-contract.json`](api-contract.json)
for shared design and server contracts.

## Current layout

- Sidebar: Hsx branding, new chat, agent activity, and live connection health.
- Center: a local-time greeting and centered composer for an empty chat; after the
  first turn the composer transitions to the bottom beneath a scrollable transcript.
  User messages are right-aligned soft bubbles, while assistant replies are unboxed
  and have no speaker label.
- Right rail on desktop: the Hsx host camera feed and its availability state.
- Mobile: focused chat and sticky composer, a navigation drawer with live connection
  states, and a camera drawer with host feed, timing information, and generated
  artifacts. Browser camera access is never requested.
- Artifacts are loaded from `/api/outputs` and link to files in the host workspace's
  `Hsx Work` output directory.
- Agent activity is loaded from `/api/activity` for the current browser session.
  Sensitive task prompts are not shown; other summaries are redacted and bounded.
- The connection panel is at the bottom of the navigation rail/drawer. The camera
  heading's refresh button requests a fresh host-camera stream.
- Browser chat commands target the Hsx host, not the phone or laptop. Remote
  local-host execution requires the existing authenticated access path; dangerous
  actions still pause for approval in the page.
- The notification bell is available on desktop and mobile. It reads Hsx's persisted
  in-app notifications, shows unread counts, and opens approval/job notifications in
  the page.

Only features backed by the current API are shown. Any file type up to 1 MB can be
attached. Uploads preserve bytes through `/api/upload`; the resulting workspace paths
are included in the chat request. The microphone is shown only in secure browsers that
support speech recognition, and recognized text remains in the draft until the user
sends it. The options menu opens the file picker or host camera panel.

The input supports Enter to send, Shift+Enter for a newline, paste, and drag-and-drop.
The Send action submits to `/api/chat/stream`, Stop calls `/api/stop`, response copy
uses the Clipboard API, and feedback uses `/api/feedback`. Remote bearer credentials
are sent through the Authorization header and are never placed in a URL.

The host camera status and still frames come from the server-side capture service.
When the device or service is unavailable, the panel accurately shows Offline rather
than displaying the user's phone or laptop camera.

See [`../docs/design.md`](../docs/design.md) for design and behavior details.
