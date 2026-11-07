import argparse
import asyncio
import json
import os
import urllib.error
from datetime import datetime
from urllib.parse import quote
import urllib.request
import uuid
from typing import Any

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual import on
from textual.widgets import Button, Footer, Input, Markdown, Static
from textual.worker import WorkerFailed

from config import BASE_DIR, HOST, PORT

_WEB_DIR = BASE_DIR / "web"
_TOKENS = json.loads((_WEB_DIR / "design-tokens.json").read_text(encoding="utf-8"))
_API = json.loads((_WEB_DIR / "api-contract.json").read_text(encoding="utf-8"))
_COLORS = _TOKENS["colors"]
_LAYOUT = _TOKENS["layout"]
_TUI_CONTENT_WIDTH = max(80, _LAYOUT["content_width"] // 8)


def _build_css() -> str:
    return f"""
    Screen {{
        background: {_COLORS["background"]};
        color: {_COLORS["text"]};
    }}
    #status-header {{
        height: {max(3, _LAYOUT["header_height"] // 12)};
        padding: 1 2;
        background: {_COLORS["surface"]};
        border-bottom: solid {_COLORS["border"]};
        align: left middle;
    }}
    #brand {{
        width: 1fr;
        color: {_COLORS["accent"]};
        text-style: bold;
    }}
    #connection {{
        width: 14;
        color: {_COLORS["success"]};
    }}
    .metric {{
        width: 18;
        color: {_COLORS["muted"]};
        text-style: bold;
    }}
    #metric-provider {{
        width: 32;
    }}
    #conversation {{
        width: 100%;
        max-width: {_TUI_CONTENT_WIDTH};
        height: 1fr;
        align: center top;
    }}
    #message-stream {{
        height: 1fr;
        padding: 1 2;
        scrollbar-color: {_COLORS["border"]};
    }}
    .welcome-card {{
        margin: 4 2;
        padding: 2;
        border: round {_COLORS["border"]};
        background: {_COLORS["surface"]};
        color: {_COLORS["muted"]};
        text-align: center;
    }}
    .message-card {{
        height: auto;
        margin: 1 0;
        padding: 1 2;
        border: round {_COLORS["border"]};
        background: {_COLORS["surface"]};
    }}
    .message-heading {{
        height: 1;
        color: {_COLORS["accent"]};
        text-style: bold;
    }}
    .message-body {{
        height: auto;
        margin-top: 1;
        color: {_COLORS["text"]};
    }}
    .feedback-row {{
        height: 3;
        margin-top: 1;
        padding-top: 1;
        border-top: solid {_COLORS["border"]};
        align: left middle;
    }}
    .feedback-row Button {{
        min-width: 10;
        height: 3;
        margin-right: 1;
        border: round {_COLORS["border"]};
        background: {_COLORS["surface_raised"]};
        color: {_COLORS["text"]};
    }}
    .feedback-status {{
        width: 1fr;
        color: {_COLORS["success"]};
    }}
    #composer-dock {{
        width: 100%;
        max-width: {_TUI_CONTENT_WIDTH};
        height: auto;
        align: center top;
        padding: 1 2;
        border-top: solid {_COLORS["border"]};
        background: {_COLORS["background"]};
    }}
    #composer-row {{
        height: 5;
        align: center middle;
    }}
    #prompt-input {{
        width: 1fr;
        height: 3;
        border: round {_COLORS["border"]};
        background: {_COLORS["surface_input"]};
    }}
    #composer-row Button {{
        min-width: 12;
        height: 3;
        margin-left: 1;
        border: round {_COLORS["accent"]};
        background: {_COLORS["accent"]};
        color: {_COLORS["background"]};
        text-style: bold;
    }}
    #composer-row #stop-button {{
        border: round {_COLORS["border"]};
        background: {_COLORS["surface"]};
        color: {_COLORS["muted"]};
    }}
    #privacy-note {{
        height: 1;
        color: {_COLORS["muted"]};
        text-align: center;
    }}
    #workspace-activity {{
        height: auto;
        max-height: 3;
        padding: 0 2;
        color: {_COLORS["muted"]};
        background: {_COLORS["surface"]};
    }}
    Footer {{
        background: {_COLORS["surface"]};
        color: {_COLORS["muted"]};
    }}
    """


class MessageCard(Vertical):
    def __init__(self, role: str, content: str, job_id: str = "", *, card_id: str):
        super().__init__(id=card_id, classes="message-card")
        self.role = role
        self.content = content
        self.job_id = job_id
        self._counter = uuid.uuid4().hex[:8]
        self._feedback_status = ""

    def compose(self) -> ComposeResult:
        yield Static("YOU" if self.role == "user" else "HSX", classes="message-heading")
        yield Markdown(self.content, classes="message-body")
        if self.role == "assistant" and self.job_id:
            with Horizontal(classes="feedback-row"):
                yield Button("👍 Good", name=f"{self.job_id}:good", id=f"good-{self._counter}")
                yield Button("👎 Bad", name=f"{self.job_id}:bad", id=f"bad-{self._counter}")
                yield Static(self._feedback_status, classes="feedback-status")

    def set_content(self, content: str) -> None:
        self.content = content
        self.query_one(".message-body", Markdown).update(content)


class HsxTUI(App):
    TITLE = "HSX Workspace"
    CSS = _build_css()
    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
        ("ctrl+l", "clear_chat", "Clear"),
    ]

    def __init__(self, base_url: str):
        super().__init__()
        self.base_url = base_url.rstrip("/")
        self.session_id = "terminal"
        self.pending_question: dict[str, Any] | None = None
        self._message_count = 0
        self._stream_card: MessageCard | None = None
        self._stream_text = ""
        self._stream_job_id = ""
        self._request_active = False

    def compose(self) -> ComposeResult:
        with Horizontal(id="status-header"):
            yield Static("◆  HSX  /  WORKSPACE", id="brand")
            yield Static("●  CONNECTING", id="connection")
            yield Static("MODEL  —", id="metric-provider", classes="metric")
            yield Static("ROUTER  —", id="metric-confidence", classes="metric")
            yield Static("TOKENS*  0 / 0", id="metric-tokens", classes="metric")
            yield Static("LATENCY  —", id="metric-latency", classes="metric")
        with Vertical(id="conversation"):
            with VerticalScroll(id="message-stream"):
                yield Static(
                    "",
                    id="welcome-card",
                    classes="welcome-card",
                )
            yield Static("Agent tasks: checking…  ·  Generated files: checking…", id="workspace-activity")
            with Vertical(id="composer-dock"):
                with Horizontal(id="composer-row"):
                    yield Input(placeholder="Message HSX…", id="prompt-input")
                    yield Button("Stop", id="stop-button")
                    yield Button("Send  ↗", id="send-button")
                yield Static("Enter to send  ·  Your prompts stay in your local workspace.", id="privacy-note")
        yield Footer()

    async def on_mount(self) -> None:
        self.set_interval(2.5, self.refresh_telemetry)
        self.set_interval(5, self.refresh_workspace_activity)
        self.run_worker(self.refresh_greeting(), name="initial-greeting")
        self.run_worker(self.refresh_telemetry(), name="initial-telemetry")
        self.run_worker(self.refresh_workspace_activity(), name="initial-workspace-activity")

    def _fetch_greeting(self) -> str:
        hour = datetime.now().astimezone().hour
        time_of_day = "morning" if hour < 12 else "afternoon" if hour < 18 else "evening"
        response = self._api_request(
            _API["endpoints"]["greeting"], {"time_of_day": time_of_day}
        )
        return str(response.get("greeting", "")).strip()

    async def refresh_greeting(self) -> None:
        try:
            worker = self.run_worker(
                self._fetch_greeting,
                thread=True,
                name="welcome-greeting",
                exit_on_error=False,
            )
            greeting = await worker.wait()
        except (RuntimeError, ValueError, LookupError, WorkerFailed):
            greeting = ""
        if not self.is_running:
            return
        if not greeting:
            greeting = "What would you like to work on?"
        self.query_one("#welcome-card", Static).update(
            f"HSX WORKSPACE READY\n\n{greeting}"
        )

    def _fetch_workspace_activity(self) -> tuple[dict, dict]:
        try:
            activity = self._api_request(
                f"{_API['endpoints']['activity']}?session_id={quote(self.session_id)}"
            )
            outputs = self._api_request(_API["endpoints"]["outputs"])
            return activity, outputs
        except (RuntimeError, ValueError, LookupError) as error:
            return {"error": str(error)}, {}

    async def refresh_workspace_activity(self) -> None:
        try:
            worker = self.run_worker(
                self._fetch_workspace_activity,
                thread=True,
                name="workspace-activity",
                exit_on_error=False,
            )
            result = await worker.wait()
            if result is None:
                return
            activity, outputs = result
            if activity.get("error"):
                raise RuntimeError(activity["error"])
            tasks = activity.get("tasks", [])
            active = [
                task for task in tasks
                if task.get("state") in {"queued", "running", "waiting_for_user", "waiting_for_approval"}
            ]
            files = outputs.get("files", [])
            if active:
                task = active[0]
                summary = " ".join(str(task.get("summary") or "Agent task").split())[:55]
                label = f"Agent: {str(task.get('state', 'running')).replace('_', ' ')} — {summary}"
                if len(active) > 1:
                    label += f" (+{len(active) - 1} more)"
            else:
                label = "Agent tasks: none"
            filenames = [str(item.get("name", "file")) for item in files[:3]]
            if filenames:
                file_label = ", ".join(filenames)
                if len(files) > len(filenames):
                    file_label += f" (+{len(files) - len(filenames)} more)"
            else:
                file_label = "none"
            label += f"  ·  Files: {file_label}"
            self.query_one("#workspace-activity", Static).update(label)
        except (RuntimeError, ValueError, LookupError) as error:
            self.query_one("#workspace-activity", Static).update(
                f"Agent activity unavailable: {str(error)[:80]}"
            )

    def _fetch_telemetry(self) -> dict:
        try:
            return self._api_request(_API["endpoints"]["telemetry"])
        except (RuntimeError, ValueError, LookupError) as error:
            return {"error": str(error)}

    def _api_request(self, endpoint: str, payload: dict | None = None) -> dict:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json"}
        request = urllib.request.Request(
            self.base_url + endpoint,
            data=data,
            headers=headers,
            method="POST" if payload is not None else "GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read().decode("utf-8")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            try:
                message = json.loads(detail).get("error", detail)
            except json.JSONDecodeError:
                message = detail
            raise RuntimeError(f"HTTP {error.code}: {message}") from error
        except OSError as error:
            raise RuntimeError(f"Could not reach HSX server: {error}") from error

    async def refresh_telemetry(self) -> None:
        try:
            worker = self.run_worker(
                self._fetch_telemetry,
                thread=True,
                name="telemetry",
                exit_on_error=False,
            )
            telemetry = await worker.wait()
            if telemetry is None:
                return
            if telemetry.get("error"):
                self.query_one("#connection", Static).update(
                    f"●  {str(telemetry['error'])[:28]}"
                )
                return
            self.query_one("#metric-provider", Static).update(
                f"MODEL  {telemetry.get('model', '—')} · {telemetry.get('provider', '—')}"
            )
            confidence = telemetry.get("router_confidence")
            confidence_text = "—" if confidence is None else f"{float(confidence) * 100:.0f}%"
            self.query_one("#metric-confidence", Static).update(f"ROUTER  {confidence_text}")
            self.query_one("#metric-tokens", Static).update(
                f"TOKENS{'*' if telemetry.get('token_usage_estimated', True) else ''}  "
                f"{telemetry.get('input_tokens', 0)} / {telemetry.get('output_tokens', 0)}"
            )
            latency = telemetry.get("latency_ms")
            latency_text = "—" if latency is None else f"{float(latency):.0f} ms"
            self.query_one("#metric-latency", Static).update(f"LATENCY  {latency_text}")
            self.query_one("#connection", Static).update("●  CONNECTED")
        except (RuntimeError, ValueError, LookupError) as error:
            self.query_one("#connection", Static).update(f"●  {str(error)[:28]}")

    def _new_message(self, role: str, content: str, job_id: str = "") -> MessageCard:
        self._message_count += 1
        card = MessageCard(role, content, job_id, card_id=f"message-{self._message_count}")
        if self.query("#welcome-card"):
            self.query_one("#welcome-card").remove()
        self.query_one("#message-stream", VerticalScroll).mount(card)
        self.query_one("#message-stream", VerticalScroll).scroll_end(animate=False)
        return card

    @on(Input.Submitted, "#prompt-input")
    async def submit_input(self, event: Input.Submitted) -> None:
        await self._submit_prompt(event.value)

    async def _submit_prompt(self, message: str) -> None:
        message = message.strip()
        if not message or self._request_active:
            return
        self.query_one("#prompt-input", Input).value = ""
        self._new_message("user", message)
        self._request_active = True
        self.run_worker(self._submit_or_answer(message), name="chat-request", exit_on_error=False)

    async def _submit_or_answer(self, message: str) -> None:
        try:
            if self.pending_question:
                job_id = self.pending_question["id"]
                endpoint = _API["endpoints"]["ask_answer"].replace("{job_id}", job_id)
                worker = self.run_worker(
                    lambda: self._api_request(endpoint, {"answer": message}),
                    thread=True,
                    name="answer-question",
                )
                await worker.wait()
                await self._poll_question(job_id)
            else:
                worker = self.run_worker(
                    lambda: self._stream_chat(message),
                    thread=True,
                    name="chat-stream",
                )
                await worker.wait()
        except (RuntimeError, ValueError) as error:
            self._new_message("assistant", f"Request failed: {error}")
        finally:
            self._request_active = False
            await self.refresh_telemetry()
            await self.refresh_workspace_activity()

    def _stream_chat(self, message: str) -> None:
        endpoint = _API["endpoints"]["chat_stream"]
        request = urllib.request.Request(
            self.base_url + endpoint,
            data=json.dumps({
                "message": message,
                "session_id": self.session_id,
                "target_device": "local_host",
            }).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
            method="POST",
        )
        try:
            response_context = urllib.request.urlopen(request, timeout=180)
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            try:
                message = json.loads(detail).get("error", detail)
            except json.JSONDecodeError:
                message = detail
            raise RuntimeError(f"HTTP {error.code}: {message}") from error
        with response_context as response:
            current_event = "message"
            data_lines: list[str] = []
            for raw_line in response:
                line = raw_line.decode("utf-8").rstrip("\r\n")
                if not line:
                    if data_lines:
                        self._dispatch_stream_event(current_event, json.loads("\n".join(data_lines)))
                    current_event, data_lines = "message", []
                elif line.startswith("event:"):
                    current_event = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())
            if data_lines:
                self._dispatch_stream_event(current_event, json.loads("\n".join(data_lines)))

    def _dispatch_stream_event(self, event: str, payload: dict) -> None:
        self.call_from_thread(self._handle_stream_event, event, payload)

    def _handle_stream_event(self, event: str, payload: dict) -> None:
        if event == "job" and payload.get("id"):
            self._stream_job_id = payload["id"]
            if payload.get("state") == "waiting_for_user":
                self.pending_question = payload
                self._new_message("assistant", payload.get("question", "I need more information."), payload["id"])
            elif payload.get("state") in {"done", "failed", "cancelled"}:
                text = self._stream_text or payload.get("result") or payload.get("error") or "Task finished."
                if self._stream_card:
                    self._stream_card.remove()
                self._stream_card = self._new_message("assistant", str(text), payload["id"])
                self._stream_card = None
                self._stream_text = ""
        elif event == "text_delta":
            self._stream_text += str(payload.get("text", ""))
            if self._stream_card is None:
                self._stream_card = self._new_message("assistant", self._stream_text)
            else:
                self._stream_card.set_content(self._stream_text)
            self.query_one("#message-stream", VerticalScroll).scroll_end(animate=False)
        elif event == "error":
            self._new_message("assistant", str(payload.get("message", "The request failed.")), self._stream_job_id)

    async def _poll_question(self, job_id: str) -> None:
        endpoint = _API["endpoints"]["ask_job"].replace("{job_id}", job_id)
        while True:
            await asyncio.sleep(1)
            worker = self.run_worker(
                lambda: self._api_request(endpoint),
                thread=True,
                name="poll-ask-job",
            )
            job = await worker.wait()
            if not job or not job.get("job"):
                raise RuntimeError(job.get("error", "Could not read the waiting task."))
            current = job["job"]
            if current["state"] == "waiting_for_user":
                self.pending_question = current
                self._new_message(
                    "assistant",
                    current.get("question", "I need more information."),
                    job_id,
                )
                return
            if current["state"] in {"done", "failed", "cancelled"}:
                self.pending_question = None
                self._new_message(
                    "assistant",
                    current.get("result") or current.get("error") or "Task finished.",
                    job_id,
                )
                return

    @on(Button.Pressed)
    async def handle_button(self, event: Button.Pressed) -> None:
        button = event.button
        if button.id == "send-button":
            prompt = self.query_one("#prompt-input", Input)
            await self._submit_prompt(prompt.value)
            return
        if button.id == "stop-button":
            worker = self.run_worker(
                lambda: self._api_request(_API["endpoints"]["stop"], {}),
                thread=True,
                name="stop-task",
                exit_on_error=False,
            )
            await worker.wait()
            return
        if button.name and ":" in button.name:
            job_id, feedback = button.name.rsplit(":", 1)
            await self._send_feedback(button, job_id, feedback)

    async def _send_feedback(self, button: Button, job_id: str, feedback: str) -> None:
        row = button.parent
        try:
            worker = self.run_worker(
                lambda: self._api_request(
                    _API["endpoints"]["feedback"],
                    {"job_id": job_id, "feedback": feedback, "client": "cli"},
                ),
                thread=True,
                name="feedback",
            )
            result = await worker.wait()
            if not result or not result.get("success"):
                raise RuntimeError("The server did not save this feedback.")
            if row:
                status = "Thanks!" if result.get("saved") else "Saving is off."
                row.query_one(".feedback-status", Static).update(status)
        except (RuntimeError, LookupError) as error:
            if row:
                row.query_one(".feedback-status", Static).update(str(error)[:40])

    async def action_clear_chat(self) -> None:
        stream = self.query_one("#message-stream", VerticalScroll)
        await stream.remove_children()
        self._new_message(
            "assistant",
            "HSX WORKSPACE READY\n\nWhat can I help you with?",
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="HSX terminal chat client")
    parser.add_argument("--url", default=os.getenv("HSX_SERVER_URL", f"http://{HOST}:{PORT}"))
    args = parser.parse_args()
    HsxTUI(args.url).run()


if __name__ == "__main__":
    main()
