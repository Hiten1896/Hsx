# Hsx shared design language

This file is the source of truth for the UI system that powers both the browser client and the terminal client.

## Product name and tone

- Name: Hsx
- Default theme: dark
- Optional themes: light and auto
- Shared UI words: Chat, Files, Artifacts, Jobs, Settings
- Shared status chips: Ready, Thinking, Using a tool, Waiting for you, Offline, Reconnecting

## Palette tokens

The browser CSS variables and the terminal styles should map to the same semantic tokens:

- background: `#0b1220`
- surface: `#111b2e`
- text: `#e6f0ff`
- muted: `#8fa4bf`
- accent: `#38bdf8`
- success: `#34d399`
- warning: `#fbbf24`
- danger: `#fb7185`

These values are intentionally high-contrast and WCAG-friendly for dark mode, with a light theme that preserves the same semantics rather than a different visual language.

## Approval and user prompts

Approvals and ask-user cards must use plain English. They must not show raw JSON, internal payloads, or `waiting_approval` strings. The same wording is used in both the browser and terminal clients.

Example format:

- `HSX > I need your permission to run the tool 'tool_name'. Would you like to proceed? (Yes/No)`
- `Yes`, `Y`, `I understand, proceed`, and `Proceed` authorize.
- `No`, `N`, `Cancel`, and `Stop` reject.

## UI status behavior

- Ready: the agent is idle and waiting for the next request.
- Thinking: the agent is generating a completion or evaluating a task.
- Using a tool: a tool call is in flight.
- Waiting for you: the task is paused for clarification or approval.
- Offline: the server is unreachable.
- Reconnecting: the client is retrying the connection after a disconnect.

## Layout principles

- Mobile-first, responsive shell from 360px to desktop widths.
- Small, high-contrast chips with accessible 44px touch targets.
- Keep telemetry and internals in a Details popover rather than the primary conversation surface.
- Use the same interaction steps everywhere: ask, approve, act, then summarize.
