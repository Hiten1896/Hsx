# HSX Chat Interface

The browser chat and terminal client share the interface contract in
`design-tokens.json` and `api-contract.json`. Keep those files as the source of
truth for colors, typography, layout dimensions, backend routes, and stream
events.

## Shared design language

The implementation follows the product-wide design system documented in
[design.md](../docs/design.md). The relevant principles are:

- Name the product `Hsx`
- Use a dark theme by default with light and auto modes
- Standardize wording: Chat, Files, Artifacts, Jobs, Settings
- Standardize status chips: Ready, Thinking, Using a tool, Waiting for you,
  Offline, Reconnecting
- Keep approval cards and ask-user prompts in plain English

## Structure

Both clients use the same interaction flow:

1. A status header shows connection state, active model/provider, router
   confidence, token counts, and request latency.
2. The central scrollable message stream presents user and assistant cards.
   Assistant responses include good/bad feedback controls.
3. A sticky composer provides prompt submission and task cancellation.

The web implementation is `index.html`, `chat.css`, and `chat.js`. The terminal
implementation is `../agent/tui.py`; it consumes the same JSON contracts and
uses the same chat, feedback, telemetry, ask-user, and stop endpoints.

## Visual system

Use the deep-slate palette with electric-blue and teal accents defined in
`design-tokens.json` and mirrored in the design doc. Narrative text uses the
shared sans-serif stack, while code and telemetry use the shared monospace
stack. Message cards use raised surfaces and subtle borders. Keep layouts
responsive and preserve keyboard and screen-reader access when adding controls.

Token counts are marked with `*` when estimated because the active provider did
not return usage metadata.

## Local commands

Run the browser client through the HSX server at `/web/`. Run the terminal
client with `python -m agent.tui`; both clients connect to the configured local
HSX server and require the same auth token.

The backend uses the repository's standard-library HTTP server. Both interfaces
use the same API contract rather than duplicating chat execution logic.
