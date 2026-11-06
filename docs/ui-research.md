# UI and terminal research notes

The interface work is based on the current official documentation for the underlying platform pieces, not on a memory-only implementation.

- Rich Live: <https://rich.readthedocs.io/en/latest/live.html>
  - Use `Live` for a low-refresh terminal display that streams text and status without heavy redrawing. Keep refresh intervals modest for slow consoles; pause or fall back to plain printing when the terminal is narrow or the user has disabled color.
- Rich Layout: <https://rich.readthedocs.io/en/latest/layout.html>
  - Layouts are composed of named regions such as header, transcript, composer, and status panels; keep the same regions in both the terminal and web product to preserve continuity.
- Rich Panel: <https://rich.readthedocs.io/en/latest/panel.html>
  - Panels are the best fit for ask-user / approval cards and artifact summaries; they provide the same visual weight without leaking raw JSON payloads.
- Rich Markdown: <https://rich.readthedocs.io/en/latest/markdown.html>
  - Markdown rendering is for transcript text and static summaries; keep code fences and tables readable at narrow widths, and collapse long logs into a concise summary when the terminal is small.
- Rich Syntax: <https://rich.readthedocs.io/en/latest/syntax.html>
  - Syntax highlighting is appropriate for code blocks, output logs, and small artifact previews in the terminal. It should be optional and degrade gracefully when `NO_COLOR` is set.
- Rich Prompt: <https://rich.readthedocs.io/en/latest/prompt.html>
  - Prompt widgets can support keyboard-first choices and selections for approval and quick actions. Keep selection delimiters and labels plain English and avoid exposing internal job objects.
- prompt_toolkit (when used): <https://python-prompt-toolkit.readthedocs.io/en/master/>
  - For line editing, history, and prefix completions, prefer a simple prompt toolkit integration over custom input handling. Keep the app resilient to terminal width changes and `Ctrl+C` semantics.
- Web Speech API: <https://developer.mozilla.org/en-US/docs/Web/API/Web_Speech_API>
  - Use speech recognition only as an optional input affordance; expose a mic button that falls back gracefully when not supported or when the browser blocks permissions. Never rely on it as the only input path.
- Server-Sent Events (SSE): <https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events>
  - Use a single typed event stream for both the browser and terminal clients, with event names such as `message_start`, `text_delta`, `tool_start`, `approval_request`, and `done`. The stream should be the contract for the UI, not a separate web-only data source.
- File API / drag-and-drop uploads: <https://developer.mozilla.org/en-US/docs/Web/API/File_API>
  - Drag-and-drop, paste events, and the File API can attach files without forcing a new page. Keep upload metadata visible and provide a remove action while preserving a lightweight upload progress indicator.
- Tailscale serve: <https://tailscale.com/kb/1242/tailscale-serve>
  - `tailscale serve` exposes a local service over the tailnet; the app should listen on the host or on `0.0.0.0` and use the tailnet URL as a publication layer, not as a substitute for local binding logic.

Implementation assumptions captured in code and tests:
- The terminal and web client both show human-readable approval cards instead of raw JSON or `waiting_approval` payloads.
- Approval actions accept yes-like and no-like answers in plain English.
- The local execution target defaults to the local host environment, and the server bind address defaults to `0.0.0.0` so external network clients can connect when the deployment needs it.
- The interface uses one shared design language: dark default with a light and auto mode; status words are standardized; and the status chips stay consistent across the browser and terminal UX.
