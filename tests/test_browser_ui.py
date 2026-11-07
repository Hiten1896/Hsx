import base64
import json
import threading
import time
import wave
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

import server
from agent.conversations import ConversationStore
from agent.feedback import FeedbackStore
from agent.notifier import Notifier

BROWSER_MUSIC_STREAM_IDS: list[str] = []
BROWSER_CHAT_MESSAGES: list[str] = []
BROWSER_RESUME_REQUESTS: list[tuple[str, str]] = []


class BrowserFakeHandler(server.HsxHandler):
    def do_GET(self):
        path = self._request_path()
        if (
            path.startswith("/api/")
            and not path.startswith("/api/music/stream/")
            and not self._api_access_allowed()
        ):
            return
        if path == "/api/activity/image":
            image = base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l3sAAAAASUVORK5CYII="
            )
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(image)))
            self.end_headers()
            self.wfile.write(image)
            return
        if path == "/api/camera/snapshot":
            image = base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l3sAAAAASUVORK5CYII="
            )
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(image)))
            self.end_headers()
            self.wfile.write(image)
            return
        if self._request_path() == "/api/files/preview":
            self._json({"content": "one\ntwo\nthree"})
            return
        if self._request_path() == "/api/files/content":
            content = b"%PDF-1.4"
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return
        if self._request_path() == "/api/outputs":
            self._json({"files": [{"name": "report.txt", "path": "Hsx Work/report.txt", "type": "text"}]})
            return
        if self._request_path() == "/api/files/download":
            content = b"browser-authenticated-download"
            self.send_response(200)
            self._security_headers()
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Disposition", 'attachment; filename="report.txt"')
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return
        return super().do_GET()

    def do_POST(self):
        if not self._api_access_allowed():
            return
        if self._request_path() == "/api/feedback":
            self._json({"success": True, "saved": True}, 201)
            return
        if self._request_path() != "/api/chat/stream":
            return super().do_POST()
        payload = self._body()
        BROWSER_CHAT_MESSAGES.append(str(payload.get("message", "")))
        if payload.get("message") == "resume once":
            BROWSER_RESUME_REQUESTS.append((
                str(payload.get("client_message_id", "")),
                self.headers.get("Last-Event-ID", ""),
            ))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            if len(BROWSER_RESUME_REQUESTS) == 1:
                self.wfile.write(
                    b'id: 1\nevent: message_start\ndata: {"run_id":"resume-run"}\n\n'
                    b'id: 2\nevent: job\ndata: {"id":"resume-job","state":"running"}\n\n'
                )
            else:
                self.wfile.write(
                    b'id: 3\nevent: text_delta\ndata: {"text":"Reconnected successfully."}\n\n'
                    b'id: 4\nevent: job\ndata: {"id":"resume-job","state":"done","result":"Reconnected successfully."}\n\n'
                    b'id: 5\nevent: done\ndata: {}\n\n'
                )
            self.wfile.flush()
            return
        if payload.get("message") == "cause provider failure":
            self._json({"error": "The provider is temporarily unavailable."}, 502)
            return
        if payload.get("message") == "slow response":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b'event: message_start\ndata: {"run_id":"browser-fake-run"}\n\n')
            self.wfile.flush()
            try:
                while True:
                    time.sleep(0.05)
                    self.wfile.write(b": active\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if payload.get("message") == "activity trace":
            events = [("message_start", {"run_id": "activity-trace-run"})]
            for index in range(7):
                tool = "create_text_file" if index == 0 else "run_code"
                call_id = f"trace-{index}"
                target = (
                    "Hsx Work/a-very-long-report-name-that-is-truncated-without-widening-the-360-pixel-layout.txt"
                    if index == 0 else f"python step_{index}.py"
                )
                events.append(("tool_start", {
                    "tool_call_id": call_id, "tool": tool, "icon": "pencil" if index == 0 else "terminal",
                    "present": "Editing" if index == 0 else "Running", "past": "Edited" if index == 0 else "Ran",
                    "target": target, "file_path": "Hsx Work/report.txt" if index == 0 else "",
                    "arguments": {"path": "Hsx Work/report.txt", "content": "[REDACTED]"} if index == 0 else {"command": target},
                    "status": "running",
                }))
                completed = {
                    "tool_call_id": call_id, "tool": tool, "icon": "pencil" if index == 0 else "terminal",
                    "present": "Editing" if index == 0 else "Running", "past": "Edited" if index == 0 else "Ran",
                    "target": target, "file_path": "Hsx Work/report.txt" if index == 0 else "",
                    "status": "completed", "duration_ms": 1200, "output": "Completed successfully.",
                    "details": {"arguments": {}, "command": target, "output_lines": [], "exit_status": 0},
                    "images": [{"id": f"image-{image}", "path": f"image-{image}.png"} for image in range(6)] if index == 0 else [],
                }
                if index == 0:
                    completed["diff"] = {
                        "path": "Hsx Work/report.txt", "added": 1, "removed": 1, "hunks": [{
                            "header": "@@ -1,3 +1,3 @@", "lines": [
                                {"kind": "context", "old_line": 1, "new_line": 1, "text": "first"},
                                {"kind": "removed", "old_line": 2, "text": "old"},
                                {"kind": "added", "new_line": 2, "text": "new"},
                                {"kind": "context", "old_line": 3, "new_line": 3, "text": "last"},
                            ],
                        }],
                    }
                events.append(("tool_end", completed))
            events.extend([
                ("text_delta", {"text": "Activity trace complete."}),
                ("job", {"id": "activity-trace-job", "state": "done", "result": "Activity trace complete."}),
                ("done", {}),
            ])
        elif payload.get("message") == "activity failure":
            events = [
                ("message_start", {"run_id": "activity-failure-run"}),
                ("tool_start", {
                    "tool_call_id": "failed-step", "tool": "shell", "icon": "terminal",
                    "present": "Running", "past": "Ran", "target": "python failing.py",
                    "arguments": {"command": "python failing.py"}, "status": "running",
                }),
                ("tool_end", {
                    "tool_call_id": "failed-step", "tool": "shell", "icon": "terminal",
                    "present": "Running", "past": "Ran", "target": "python failing.py",
                    "arguments": {"command": "python failing.py"}, "status": "failed",
                    "duration_ms": 15, "output": "Process returned an error.",
                    "details": {"command": "python failing.py", "exit_status": 1, "output_lines": ["failed"], "arguments": {"command": "python failing.py"}},
                    "images": [],
                }),
                ("text_delta", {"text": "The step failed."}),
                ("job", {"id": "activity-failure-job", "state": "done", "result": "The step failed."}),
                ("done", {}),
            ]
        elif payload.get("message") == "activity binary":
            events = [
                ("message_start", {"run_id": "activity-binary-run"}),
                ("tool_start", {
                    "tool_call_id": "binary-step", "tool": "create_text_file", "icon": "pencil",
                    "present": "Editing", "past": "Edited", "target": "report.pdf",
                    "file_path": "Hsx Work/report.pdf", "status": "running",
                }),
                ("tool_end", {
                    "tool_call_id": "binary-step", "tool": "create_text_file", "icon": "pencil",
                    "present": "Editing", "past": "Edited", "target": "report.pdf",
                    "file_path": "Hsx Work/report.pdf", "status": "completed", "duration_ms": 5,
                    "diff": {"path": "Hsx Work/report.pdf", "binary": True, "size_bytes": 2048},
                    "details": {"arguments": {}}, "images": [],
                }),
                ("text_delta", {"text": "Binary file ready."}),
                ("job", {"id": "activity-binary-job", "state": "done", "result": "Binary file ready."}),
                ("done", {}),
            ]
        elif payload.get("message") == "camera inline":
            events = [
                ("message_start", {"run_id": "camera-inline-run"}),
                ("camera_snapshot", {"source": "/api/camera/snapshot"}),
                ("job", {
                    "id": "camera-inline-run",
                    "state": "done",
                    "result": "Here is the current camera view.",
                }),
                ("done", {}),
            ]
        elif payload.get("message") == "show capabilities":
            events = [
                ("message_start", {"run_id": "capabilities-run"}),
                ("tool_start", {
                    "tool_call_id": "capabilities-step", "tool": "capabilities", "icon": "gear",
                    "present": "Checking", "past": "Checked", "target": "capabilities",
                    "arguments": {}, "status": "running",
                }),
                ("tool_end", {
                    "tool_call_id": "capabilities-step", "tool": "capabilities", "icon": "gear",
                    "present": "Checking", "past": "Checked", "target": "capabilities",
                    "status": "completed", "duration_ms": 8, "details": {"arguments": {}},
                    "capabilities": {"groups": [{
                        "name": "Providers",
                        "items": [{
                            "name": "Gemini", "status": "works now",
                            "detail": "A no-tool health request succeeded.",
                        }],
                    }, {
                        "name": "Known gaps",
                        "items": [{
                            "name": "OCR", "status": "not possible yet",
                            "detail": "Add the OCR skill.",
                        }],
                    }]},
                    "images": [],
                }),
                ("text_delta", {"text": "Hsx is ready for these tasks."}),
                ("job", {"id": "capabilities-job", "state": "done", "result": "Hsx is ready for these tasks."}),
                ("done", {}),
            ]
        elif payload.get("message") == "music player":
            events = [
                ("message_start", {"run_id": "music-player-run"}),
                ("tool_start", {
                    "tool_call_id": "music-player-step", "tool": "music_play", "icon": "music",
                    "present": "Preparing", "past": "Prepared", "target": "music",
                    "status": "running",
                }),
                ("tool_end", {
                    "tool_call_id": "music-player-step", "tool": "music_play", "icon": "music",
                    "present": "Preparing", "past": "Prepared", "target": "music",
                    "status": "completed", "duration_ms": 10, "output": "Prepared two tracks.",
                    "details": {"arguments": {}}, "images": [],
                    "music_player": {
                        "type": "music_play", "stream_id": BROWSER_MUSIC_STREAM_IDS[0],
                        "title": "Public-domain song.ogg", "artist": "Example Artist",
                        "license": "Public domain", "attribution_required": True,
                        "queue": [
                            {
                                "type": "music_play", "stream_id": stream_id,
                                "title": f"Track {index + 1}.ogg", "artist": "Example Artist",
                                "license": "Public domain", "attribution_required": True,
                            }
                            for index, stream_id in enumerate(BROWSER_MUSIC_STREAM_IDS)
                        ],
                    },
                }),
                ("text_delta", {"text": "The selected free audio is ready."}),
                ("job", {"id": "music-player-job", "state": "done", "result": "The selected free audio is ready."}),
                ("done", {}),
            ]
        elif payload.get("message") == "resolver plan" or payload.get("message", "").startswith(
            "Update the yt-dlp music engine"
        ):
            events = [
                ("message_start", {"run_id": "resolver-plan-run"}),
                ("job", {
                    "id": "resolver-plan-job",
                    "state": "waiting_for_approval",
                    "approval_id": "resolver-approval",
                    "approval": {
                        "request_type": "package_install",
                        "summary": "Hsx wants to: install yt-dlp==2026.10.26 in its own Python environment.",
                        "why": "Adds music search and audio downloads; about 20 MB and needs the internet.",
                        "details": "Project virtual environment only. No sudo or system packages. Hsx will import the package to self-test it, then continue your request.",
                    },
                }),
                ("done", {}),
            ]
        else:
            events = [
                ("message_start", {"run_id": "browser-fake-run"}),
                ("text_delta", {"text": "Test answer from fake provider."}),
                ("job", {"id": "browser-fake-job", "state": "done", "result": "Test answer from fake provider."}),
                ("done", {}),
            ]
        for name, data in events:
            self.wfile.write(f"event: {name}\ndata: {json.dumps(data)}\n\n".encode())
        self.wfile.flush()


@pytest.fixture

def browser_app(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "CONVERSATIONS", ConversationStore(tmp_path / "conversations.sqlite"))
    monkeypatch.setattr(server, "FEEDBACK_STORE", FeedbackStore(tmp_path / "feedback.sqlite"))
    monkeypatch.setattr(server, "NOTIFIER", Notifier(tmp_path / "notifications.sqlite"))
    monkeypatch.setattr(server, "AUTH_TOKEN", "browser-testing-token")
    host = ThreadingHTTPServer(("127.0.0.1", 0), BrowserFakeHandler)
    monkeypatch.setattr(server, "ALLOWED_ORIGINS", (f"http://127.0.0.1:{host.server_port}",))
    thread = threading.Thread(target=host.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{host.server_port}", tmp_path / "conversations.sqlite"
    host.shutdown()
    thread.join(timeout=3)
    host.server_close()


@pytest.fixture

def browser_page():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900}, accept_downloads=True)
        page.route("https://cdn.jsdelivr.net/**", lambda route: route.abort())
        yield browser, page
        browser.close()


def test_browser_uses_same_origin_without_a_page_token(browser_app, browser_page):
    base_url, _ = browser_app
    _, page = browser_page
    page.goto(f"{base_url}/web/")
    page.locator('body[data-ready="true"]').wait_for(timeout=10000)
    assert page.locator("#auth-dialog").count() == 0
    assert page.evaluate("sessionStorage.getItem('hsx-api-token')") is None
    page.locator("#prompt-input").fill("Runs without a webpage token")
    page.locator("#send-button").click()
    page.get_by_text("Test answer from fake provider.").wait_for()


def test_browser_theme_switching_brand_center_and_artifact_scrolling(browser_app, browser_page):
    base_url, _ = browser_app
    _, page = browser_page
    page.emulate_media(color_scheme="light")
    page.goto(f"{base_url}/web/")
    page.locator('body[data-ready="true"]').wait_for(timeout=10000)

    page.locator("#settings-toggle").click()
    theme = page.locator("#theme-setting")
    theme.select_option("light")
    assert page.locator("html").get_attribute("data-theme") == "light"
    assert page.evaluate("localStorage.getItem('hsx-theme')") == "light"
    theme.select_option("dark")
    assert page.locator("html").get_attribute("data-theme") == "dark"
    theme.select_option("system")
    assert page.locator("html").get_attribute("data-theme") == "light"
    page.emulate_media(color_scheme="dark")
    page.wait_for_timeout(50)
    assert page.locator("html").get_attribute("data-theme") == "dark"

    brand = page.locator(".brand").bounding_box()
    sidebar = page.locator("#app-sidebar").bounding_box()
    assert abs((brand["x"] + brand["width"] / 2) - (sidebar["x"] + sidebar["width"] / 2)) < 1

    page.evaluate("""() => {
      const list = document.getElementById('artifacts-list');
      list.replaceChildren(...Array.from({ length: 24 }, (_, index) => {
        const item = document.createElement('div');
        item.className = 'artifact-item';
        item.textContent = `Generated artifact ${index}`;
        return item;
      }));
      list.scrollTop = list.scrollHeight;
    }""")
    assert page.locator("#artifacts-list").evaluate("node => node.scrollHeight > node.clientHeight")
    heading_top = page.locator("#artifacts-title").evaluate("node => node.getBoundingClientRect().top")
    page.locator("#artifacts-list").evaluate("node => { node.scrollTop = 0; }")
    assert page.locator("#artifacts-title").evaluate(
        "(node, top) => Math.abs(node.getBoundingClientRect().top - top) < 1",
        heading_top,
    )


def test_voice_dictation_deduplicates_results_and_does_not_send(browser_app, browser_page):
    base_url, _ = browser_app
    _, page = browser_page
    page.add_init_script(
        "window.SpeechRecognition = class { "
        "constructor() { window.testRecognition = this; } "
        "start() { this.onstart?.(); } stop() { this.onend?.(); } };"
    )
    page.add_init_script("localStorage.setItem('hsx-voice-language', 'hi-IN');")
    page.goto(f"{base_url}/web/")
    page.locator('body[data-ready="true"]').wait_for(timeout=10000)
    before = len(BROWSER_CHAT_MESSAGES)
    page.locator("#voice-button").click()
    result = page.evaluate(
        """() => {
          const item = Object.assign([{ transcript: 'hello there' }], { isFinal: true });
          const event = { resultIndex: 0, results: [item] };
          window.testRecognition.onresult(event);
          window.testRecognition.onresult(event);
          return {
            text: document.querySelector('#prompt-input').value,
            language: window.testRecognition.lang,
            sendEnabled: !document.querySelector('#send-button').disabled,
          };
        }"""
    )
    assert result == {"text": "hello there", "language": "hi-IN", "sendEnabled": True}
    assert len(BROWSER_CHAT_MESSAGES) == before


def test_browser_resumes_chat_stream_with_same_client_message_id(browser_app, browser_page):
    base_url, _ = browser_app
    _, page = browser_page
    BROWSER_RESUME_REQUESTS.clear()
    page.goto(f"{base_url}/web/")
    page.locator('body[data-ready="true"]').wait_for(timeout=10000)
    page.locator("#prompt-input").fill("resume once")
    page.locator("#send-button").click()
    page.get_by_text("Reconnected successfully.", exact=True).wait_for()

    assert len(BROWSER_RESUME_REQUESTS) == 2
    assert BROWSER_RESUME_REQUESTS[0][0]
    assert BROWSER_RESUME_REQUESTS[0][0] == BROWSER_RESUME_REQUESTS[1][0]
    assert BROWSER_RESUME_REQUESTS[0][1] == ""
    assert BROWSER_RESUME_REQUESTS[1][1] == "2"


def test_browser_renders_camera_snapshot_inline_in_the_active_message(browser_app, browser_page):
    base_url, _ = browser_app
    _, page = browser_page
    page.goto(f"{base_url}/web/")
    page.locator('body[data-ready="true"]').wait_for(timeout=10000)
    page.locator("#prompt-input").fill("camera inline")
    page.locator("#send-button").click()
    snapshot = page.locator(".message-inline-camera-snapshot")
    snapshot.wait_for(timeout=5000)
    assert snapshot.get_attribute("alt") == "Camera snapshot"
    assert snapshot.evaluate("image => image.naturalWidth") == 1
    assert page.get_by_text("Here is the current camera view.").is_visible()


def test_artifact_download_uses_authenticated_fetch(browser_app, browser_page):
    base_url, _ = browser_app
    _, page = browser_page
    page.goto(f"{base_url}/web/")
    page.locator('body[data-ready="true"]').wait_for(timeout=10000)
    link = page.get_by_role("link", name="Download report.txt")
    link.wait_for()
    with page.expect_download() as download_info:
        link.click()
    download = download_info.value
    assert download.suggested_filename == "report.txt"


def test_browser_new_chat_saves_thread_and_does_not_save_empty_chats(browser_app, browser_page):
    base_url, _ = browser_app
    browser, page = browser_page
    page.set_default_timeout(5000)
    page.goto(f"{base_url}/web/")
    page.locator('body[data-ready="true"]').wait_for(timeout=10000)
    composer = page.locator("#prompt-input")
    composer.fill("A saved browser conversation")
    page.locator("#send-button").click()
    page.locator('.message-card[data-role="assistant"]').last.get_by_text("Test answer from fake provider.").wait_for()
    page.locator("#new-chat-button").click()
    assert composer.evaluate("element => document.activeElement === element")
    assert page.locator("#chat-stream .message-card").count() == 0
    assert page.get_by_role("button", name="Open chat: A saved browser conversation").count() == 1
    page.locator("#new-chat-button").click()
    page.locator("#new-chat-button").click()
    assert page.locator(".chat-history-row").count() == 1
    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")


def test_browser_music_player_is_tap_to_play_and_exposes_queue(browser_app, browser_page, tmp_path, monkeypatch):
    from agent.music_catalog import MusicCatalog

    cache = tmp_path / "music-cache"
    cache.mkdir()
    audio_path = cache / "commons.wav"
    with wave.open(str(audio_path), "wb") as audio_file:
        audio_file.setnchannels(1)
        audio_file.setsampwidth(2)
        audio_file.setframerate(8000)
        audio_file.writeframes(b"\0\0" * 24_000)
    catalog = MusicCatalog(cache)
    stream_ids = [
        catalog._create_stream_ticket(audio_path),
        catalog._create_stream_ticket(audio_path),
    ]
    monkeypatch.setattr(server, "MUSIC_CATALOG", catalog)
    monkeypatch.setattr("tests.test_browser_ui.BROWSER_MUSIC_STREAM_IDS", stream_ids)
    base_url, _ = browser_app
    _, page = browser_page
    page.set_default_timeout(5000)
    page.goto(f"{base_url}/web/")
    page.locator('body[data-ready="true"]').wait_for(timeout=10000)
    page.locator("#prompt-input").fill("music player")
    page.locator("#send-button").click()
    step = page.locator(".activity-step").last
    step.locator(".activity-row-toggle").click()
    player = step.locator(".activity-music-player")
    player.wait_for()
    audio = player.locator("audio")
    assert audio.evaluate("element => element.controls")
    assert audio.evaluate("element => element.paused")
    assert player.locator(".activity-music-status").inner_text() == "Tap Play to start. Playback never starts automatically."
    assert player.locator(".activity-music-queue li").count() == 2
    player.get_by_role("button", name="Track 2.ogg").click()
    page.wait_for_function(
        "() => document.querySelector('.activity-music-status')?.textContent === 'Tap Play to start. Playback never starts automatically.'"
    )
    cookie = next(item for item in page.context.cookies() if item["name"] == "hsx_audio")
    assert cookie["httpOnly"]
    assert cookie["path"] == "/api/music/stream/"
    page.set_viewport_size({"width": 360, "height": 800})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_browser_package_resolver_shows_one_plain_approval_plan(browser_app, browser_page):
    base_url, _ = browser_app
    _, page = browser_page
    page.set_default_timeout(5000)
    page.goto(f"{base_url}/web/")
    page.locator('body[data-ready="true"]').wait_for(timeout=10000)
    page.locator("#prompt-input").fill("resolver plan")
    page.locator("#send-button").click()
    card = page.locator(".approval-card")
    card.wait_for()
    assert page.locator(".approval-card").count() == 1
    assert card.get_by_role("heading", name="Hsx can add this ability").is_visible()
    assert "install yt-dlp==2026.10.26" in card.inner_text()
    assert "about 20 MB" in card.inner_text()
    card.get_by_text("Details").click()
    assert "virtual environment only" in card.locator("pre").inner_text()
    assert "No sudo" in card.locator("pre").inner_text()
    assert card.get_by_role("button", name="Allow", exact=True).is_visible()
    assert card.get_by_role("button", name="Always allow this").is_visible()
    assert card.get_by_role("button", name="Not now").is_visible()
    assert not card.inner_text().lstrip().startswith("{")


def test_advanced_settings_update_music_engine_uses_resolver_approval(browser_app, browser_page):
    base_url, _ = browser_app
    _, page = browser_page
    page.set_default_timeout(5000)
    page.goto(f"{base_url}/web/")
    page.locator('body[data-ready="true"]').wait_for(timeout=10000)
    page.locator("#settings-toggle").click()
    page.locator("#settings-tab-advanced").click()
    page.get_by_role("button", name="Update music engine").click()
    card = page.locator(".approval-card")
    card.wait_for()
    assert "install yt-dlp==2026.10.26" in card.inner_text()
    assert page.locator("#settings-dialog").evaluate("dialog => !dialog.open")


def test_browser_history_survives_reload_and_another_browser_and_supports_actions(browser_app, browser_page):
    base_url, database = browser_app
    browser, page = browser_page
    page.goto(f"{base_url}/web/")
    page.locator('body[data-ready="true"]').wait_for(timeout=10000)
    page.locator("#prompt-input").fill("History survives the server restart")
    page.locator("#send-button").click()
    page.get_by_text("Test answer from fake provider.").wait_for()
    page.locator('.message-card[data-role="assistant"] [data-feedback="good"]').click()
    page.locator("#feedback-form button[type='submit']").click()
    page.get_by_text("Thanks for your feedback.").wait_for()
    page.wait_for_timeout(500)
    page.reload()
    page.locator('body[data-ready="true"]').wait_for(timeout=10000)
    assert page.locator("#chat-stream .message-card").count() == 0
    page.get_by_role("button", name="Open chat: History survives the server restart").click()
    page.get_by_text("Test answer from fake provider.").wait_for()
    assert page.locator('.message-card[data-role="assistant"] [data-feedback="good"]').get_attribute("aria-pressed") == "true"
    server.CONVERSATIONS = ConversationStore(database)
    second = browser.new_page(viewport={"width": 360, "height": 800})
    second.route("https://cdn.jsdelivr.net/**", lambda route: route.abort())
    second.goto(f"{base_url}/web/")
    second.locator('body[data-ready="true"]').wait_for(timeout=10000)
    second.locator("#sidebar-toggle").click()
    title = "Open chat: History survives the server restart"
    second.get_by_role("button", name=title).click()
    second.get_by_text("Test answer from fake provider.").wait_for()
    assert second.locator("#chat-stream .message-card").count() == 2
    search = second.locator("#chat-history-search")
    history_row = second.locator(".chat-history-row").first
    assert history_row.bounding_box()["height"] == 40
    assert abs(search.bounding_box()["width"] - second.locator("#chat-history-list").bounding_box()["width"]) < 1
    search.fill("does not match")
    assert second.get_by_text("No matching chats.").is_visible()
    search.fill("History survives")
    second.once("dialog", lambda dialog: dialog.accept("Renamed saved thread"))
    second.get_by_role("button", name="Rename chat: History survives the server restart").last.click()
    search.fill("")
    second.get_by_role("button", name="Open chat: Renamed saved thread").wait_for(timeout=3000)
    second.once("dialog", lambda dialog: dialog.accept())
    second.get_by_role("button", name="Delete chat: Renamed saved thread").first.click()
    assert second.locator(".chat-history-row").count() == 0
    second.close()


def test_browser_feedback_settings_can_disable_saving_and_delete_all(browser_app, browser_page):
    base_url, _ = browser_app
    _, page = browser_page
    server.FEEDBACK_STORE.record(
        conversation_id="feedback-session",
        message_id="feedback-message",
        rating="up",
        prompt_messages=[{"role": "user", "content": "A test prompt"}],
        response="A test response",
    )
    page.goto(f"{base_url}/web/")
    page.locator('body[data-ready="true"]').wait_for(timeout=10000)
    page.locator("#settings-toggle").click()
    page.locator("#settings-tab-privacy").click()
    assert page.locator("#feedback-count").inner_text() == "1 feedback entry"
    with page.expect_response(
        lambda response: response.url.endswith("/api/feedback/settings")
        and response.request.method == "POST"
    ) as settings_response:
        page.locator("#save-feedback-setting").uncheck()
    assert settings_response.value.ok
    assert server.FEEDBACK_STORE.settings()["save_feedback"] is False
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("#delete-feedback-button").click()
    page.wait_for_function(
        "() => document.querySelector('#feedback-count').textContent === '0 feedback entries'"
    )
    assert server.FEEDBACK_STORE.settings()["count"] == 0


def test_browser_settings_notifications_and_failure_keep_working(browser_app, browser_page):
    base_url, _ = browser_app
    browser, page = browser_page
    page.goto(f"{base_url}/web/")
    page.locator('body[data-ready="true"]').wait_for(timeout=10000)
    page.locator("#settings-toggle").click()
    page.get_by_role("tab", name="Voice").click()
    page.keyboard.press("Escape")
    assert not page.locator("#settings-dialog").evaluate("element => element.open")
    page.locator("#prompt-input").fill("cause provider failure")
    page.locator("#send-button").click()
    page.get_by_text("The provider is temporarily unavailable.").wait_for()
    assert page.locator("#prompt-input").input_value() == "cause provider failure"
    server.NOTIFIER.notify("job_done", "Browser test event")
    page.locator("#notifications-toggle").click()
    page.locator("#notifications-list").get_by_text("Browser test event").wait_for()
    assert page.locator("#notification-count").inner_text() == "1"
    page.locator("#mark-notifications-read").click()
    page.locator("#notification-count").wait_for(state="hidden")
    page.locator("#clear-notifications").click()
    page.locator("#notifications-list").get_by_text("No notifications yet.").wait_for()
    for width in (360, 1280):
        page.set_viewport_size({"width": width, "height": 800})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_browser_composer_keyboard_suggestions_and_empty_send_state(browser_app, browser_page):
    base_url, _ = browser_app
    browser, page = browser_page
    page.goto(f"{base_url}/web/")
    page.locator('body[data-ready="true"]').wait_for(timeout=10000)
    composer = page.locator("#prompt-input")
    send = page.locator("#send-button")
    assert send.is_disabled()
    chips = page.locator(".suggestion-chip")
    assert chips.count() == 4
    chips.first.click()
    assert "CPU" in composer.input_value()
    assert not send.is_disabled()
    send.click()
    page.get_by_text("Test answer from fake provider.").wait_for()
    composer.press("Shift+Enter")
    assert "\n" in composer.input_value()
    composer.fill("Keyboard submits a real message")
    composer.press("Enter")
    page.locator('.message-card[data-role="assistant"]').last.get_by_text("Test answer from fake provider.").wait_for()
    assert send.is_disabled()


def test_browser_layout_contracts_and_connection_popover(browser_app, browser_page):
    base_url, _ = browser_app
    browser, page = browser_page
    page.set_default_timeout(5000)
    page.goto(f"{base_url}/web/")
    page.locator('body[data-ready="true"]').wait_for(timeout=10000)
    assert page.locator("#composer-options-button").count() == 0
    page.set_viewport_size({"width": 360, "height": 800})
    page.locator("#connection-toggle").click()
    popover = page.locator("#connections-panel")
    assert popover.is_visible()
    bounds = popover.bounding_box()
    assert bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= 360
    assert bounds["width"] <= 336
    assert popover.locator(".connection-note").evaluate("el => el.scrollWidth <= el.clientWidth")
    page.keyboard.press("Escape")
    assert page.evaluate("document.activeElement.id") == "connection-toggle"
    page.locator("#connection-toggle").click()
    page.mouse.click(2, 2)
    assert popover.is_hidden()
    page.locator("#notifications-toggle").click()
    notifications = page.locator("#notifications-panel")
    assert notifications.is_visible()
    page.mouse.click(2, 2)
    assert notifications.is_hidden()
    assert page.locator("#composer-camera-button").count() == 0
    assert "Enter to send" not in page.locator(".composer-footer").inner_text()
    send_box = page.locator("#send-button").bounding_box()
    assert send_box["width"] == 44
    assert send_box["height"] == 44
    page.set_viewport_size({"width": 1280, "height": 900})
    assert page.locator("#send-button").bounding_box()["width"] == 48
    page.locator(".suggestion-chip").first.click()
    page.locator("#send-button").click()
    first = page.locator("#chat-stream .message-card").first
    first.wait_for()
    controls = page.locator(".chat-controls").bounding_box()
    message = first.bounding_box()
    assert controls["y"] + controls["height"] <= message["y"]
    for row in page.locator(".connection-list li, .chat-history-row").all():
        assert row.evaluate("el => el.scrollWidth <= el.clientWidth")


def test_browser_send_and_stop_share_one_composer_slot(browser_app, browser_page):
    base_url, _ = browser_app
    browser, page = browser_page
    page.goto(f"{base_url}/web/")
    page.locator('body[data-ready="true"]').wait_for(timeout=10000)
    page.locator("#prompt-input").fill("slow response")
    send = page.locator("#send-button")
    send_box = send.bounding_box()
    page.locator("#send-button").click()
    stop = page.locator("#stop-button")
    stop.wait_for(state="visible")
    assert page.locator("#send-button").is_hidden()
    stop_box = stop.bounding_box()
    assert abs(send_box["x"] - stop_box["x"]) < 5
    page.locator("#prompt-input").fill("queued draft")
    page.locator("#prompt-input").press("Enter")
    assert page.locator("#prompt-input").input_value() == "queued draft"
    stop.click()


def test_browser_layout_screenshots_at_requested_widths(browser_app, browser_page):
    base_url, database = browser_app
    browser, page = browser_page
    screenshots = Path(__file__).resolve().parents[1] / "docs" / "screens"
    screenshots.mkdir(parents=True, exist_ok=True)
    page.goto(f"{base_url}/web/")
    page.locator('body[data-ready="true"]').wait_for(timeout=10000)

    for width in (360, 768, 1280):
        page.set_viewport_size({"width": width, "height": 900})
        page.wait_for_timeout(250)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(screenshots / f"after-{width}-empty-history.png"))
        page.locator("#connection-toggle").click()
        popover = page.locator("#connections-panel")
        bounds = popover.bounding_box()
        assert bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= width
        assert bounds["y"] >= 0 and bounds["y"] + bounds["height"] <= 900
        assert bounds["width"] <= min(340, width - 24)
        for row in popover.locator(".connection-list li").all():
            assert row.evaluate("el => el.scrollWidth <= el.clientWidth")
        page.screenshot(path=str(screenshots / f"after-{width}-connections-open.png"))
        page.keyboard.press("Escape")

    store = ConversationStore(database)
    for index in range(24):
        store.save(
            f"seed-chat-{index}",
            f"A deliberately long conversation title {index:02d} about the history sidebar fitting narrow screens",
            {"messages": []},
        )
    server.CONVERSATIONS = store
    page.reload()
    page.locator('body[data-ready="true"]').wait_for(timeout=10000)
    assert page.locator(".chat-history-row").count() >= 20
    for width in (360, 768, 1280):
        page.set_viewport_size({"width": width, "height": 900})
        page.wait_for_timeout(250)
        page.screenshot(path=str(screenshots / f"after-{width}-long-titles-24-chats.png"))
        for row in page.locator(".chat-history-row").all():
            assert row.evaluate("el => el.scrollWidth <= el.clientWidth")

    page.locator("#new-chat-button").click()
    composer = page.locator("#prompt-input")
    for index in range(6):
        assistant_count = page.locator('.message-card[data-role="assistant"]').count()
        composer.fill(f"Long conversation message {index}: " + ("detail " * 80))
        page.locator("#send-button").click()
        page.locator('.message-card[data-role="assistant"]').nth(assistant_count).wait_for()
    for width in (360, 768, 1280):
        page.set_viewport_size({"width": width, "height": 900})
        page.wait_for_timeout(250)
        stream = page.locator(".message-stream").bounding_box()
        first_box = next(
            box for box in (card.bounding_box() for card in page.locator(".message-card").all())
            if box["y"] + box["height"] > stream["y"] and box["y"] < stream["y"] + stream["height"]
        )
        visible_top = max(first_box["y"], stream["y"])
        visible_bottom = min(first_box["y"] + first_box["height"], stream["y"] + stream["height"])
        first_message = {**first_box, "y": visible_top, "height": visible_bottom - visible_top}
        for selector in ("#notifications-toggle", "#connection-toggle", "#settings-toggle"):
            icon = page.locator(selector).bounding_box()
            vertical_overlap = icon["y"] < first_message["y"] + first_message["height"] and first_message["y"] < icon["y"] + icon["height"]
            horizontal_overlap = icon["x"] < first_message["x"] + first_message["width"] and first_message["x"] < icon["x"] + icon["width"]
            assert not (vertical_overlap and horizontal_overlap)
        page.screenshot(path=str(screenshots / f"after-{width}-long-conversation.png"))


def test_sidebar_activity_cards_expand_only_on_request(browser_app, browser_page):
            base_url, _ = browser_app
            _, page = browser_page
            page.route(
                "**/api/activity?*",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({"tasks": [
                        {
                            "state": "running",
                            "summary": "Build a weekly report",
                            "agent_name": "Hsx",
                            "agent_role": "Assistant",
                            "task_path": "Hsx Work/weekly-report",
                            "elapsed_seconds": 18,
                            "eta_seconds": 42,
                        },
                        {"state": "done", "summary": "Run validation", "tool_name": "run_code"},
                        {"state": "done", "summary": "Edit report", "tool_name": "write_file"},
                    ]}),
                ),
            )
            page.goto(f"{base_url}/web/")
            page.locator('body[data-ready="true"]').wait_for(timeout=10000)
            page.locator("#activity-toggle").click()
            card = page.locator(".activity-item")
            card.first.wait_for()
            assert page.locator(".activity-item").count() == 3
            assert card.nth(0).locator(".activity-task-icon use").get_attribute("href") == "#activity-icon-spinner"
            assert card.nth(1).locator(".activity-task-icon use").get_attribute("href") == "#activity-icon-terminal"
            assert card.nth(2).locator(".activity-task-icon use").get_attribute("href") == "#activity-icon-pencil"
            toggle = card.first.locator(".activity-item-toggle")
            details = card.first.locator(".activity-card-details")
            assert toggle.get_attribute("aria-expanded") == "false"
            assert details.is_hidden()
            toggle.click()
            assert toggle.get_attribute("aria-expanded") == "true"
            assert details.is_visible()
            assert details.get_by_text("weekly-report").is_visible()


def test_sent_attachments_render_as_file_cards_not_query_text(browser_app, browser_page):
            base_url, _ = browser_app
            _, page = browser_page
            BROWSER_CHAT_MESSAGES.clear()
            image = base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l3sAAAAASUVORK5CYII="
            )
            page.route(
                "**/api/upload",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({"success": True, "name": "screenshot.png", "path": "Hsx Work/screenshot.png"}),
                ),
            )
            page.route(
                "**/api/files/content**",
                lambda route: route.fulfill(status=200, content_type="image/png", body=image),
            )
            page.goto(f"{base_url}/web/")
            page.locator('body[data-ready="true"]').wait_for(timeout=10000)
            page.locator("#file-input").set_input_files({
                "name": "screenshot.png", "mimeType": "image/png", "buffer": image,
            })
            page.locator(".attachment-chip").wait_for()
            page.locator("#prompt-input").fill("Describe this screenshot")
            page.locator("#send-button").click()
            message = page.locator('.message-card[data-role="user"]').last
            message.locator(".message-attachment-image img").wait_for()
            assert message.locator(".message-content").inner_text() == "Describe this screenshot"
            assert message.locator(".message-attachment-name").inner_text() == "screenshot.png"
            assert BROWSER_CHAT_MESSAGES[-1].endswith("Hsx Work/screenshot.png")
            page.locator('.message-card[data-role="assistant"]').last.wait_for()
            page.wait_for_timeout(500)
            page.reload()
            page.locator('body[data-ready="true"]').wait_for(timeout=10000)
            assert page.locator("#chat-stream .message-card").count() == 0
            page.get_by_role("button", name="Open chat: Describe this screenshot").click()
            restored = page.locator('.message-card[data-role="user"]').last
            restored.wait_for()
            assert restored.locator(".message-content").inner_text() == "Describe this screenshot"
            assert restored.locator(".message-attachment-name").inner_text() == "screenshot.png"


def test_browser_activity_trace_diff_images_grouping_and_failure(browser_app, browser_page):
    base_url, _ = browser_app
    browser, page = browser_page
    page.set_default_timeout(5000)
    page.goto(f"{base_url}/web/")
    page.locator('body[data-ready="true"]').wait_for(timeout=10000)
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    page.locator("#prompt-input").fill("activity trace")
    page.locator("#send-button").click()
    trace = page.locator(".activity-trace").last
    summary = trace.locator(".activity-group-summary")
    summary.wait_for()
    assert summary.inner_text() == "Completed 7 steps"
    summary.click()
    assert trace.locator(".activity-step").count() == 7
    first = trace.locator(".activity-step").first
    assert first.locator(".activity-details").is_hidden()
    assert first.locator(".activity-target-chip").get_attribute("title").startswith("Hsx Work/")
    assert first.locator(".activity-target-chip").evaluate("el => getComputedStyle(el).textOverflow") == "ellipsis"
    first.locator(".activity-target-chip").click()
    page.locator("#activity-file-viewer[open]").wait_for()
    assert page.locator("#activity-viewer-title").inner_text() == "Hsx Work/report.txt"
    page.locator("#activity-viewer-close").click()
    first.locator(".activity-verb").click()
    assert first.locator(".activity-row-toggle").get_attribute("aria-expanded") == "true", first.inner_html()
    diff = first.locator(".activity-diff-card")
    assert diff.is_visible()
    assert diff.get_by_text("+1", exact=True).is_visible()
    assert diff.get_by_text("-1", exact=True).is_visible()
    assert diff.locator(".activity-diff-line-number").count() == 4
    diff.get_by_role("button", name="Copy").click()
    assert "new" in page.evaluate("navigator.clipboard.readText()")
    gallery = first.locator(".activity-image-gallery")
    assert gallery.locator("figure").count() == 5
    assert gallery.get_by_text("+1 more").is_visible()
    gallery.locator(".activity-image-open").first.click()
    page.locator("#activity-file-viewer[open]").wait_for()
    assert page.locator("#activity-viewer-title").inner_text() == "image-0.png"
    page.locator("#activity-viewer-close").click()
    for width in (360, 768, 1280):
        page.set_viewport_size({"width": width, "height": 900})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(Path(__file__).resolve().parents[1] / "docs" / "screens" / f"after-{width}-activity-trace.png"))
    second = trace.locator(".activity-step").nth(1)
    second.locator(".activity-row-toggle").click()
    assert "python step_1.py" in second.locator(".activity-command").inner_text()
    icon_ids = page.locator("svg.activity-icon-sprite symbol").evaluate_all("items => items.map(item => item.id)")
    assert page.locator("svg.activity-icon-sprite").count() == 1
    assert len(icon_ids) == len(set(icon_ids))
    assert {
        "activity-icon-terminal", "activity-icon-pencil", "activity-icon-book", "activity-icon-images",
        "activity-icon-file-plus", "activity-icon-search", "activity-icon-globe", "activity-icon-camera",
        "activity-icon-music", "activity-icon-download", "activity-icon-list", "activity-icon-question",
        "activity-icon-shield", "activity-icon-clock", "activity-icon-package", "activity-icon-palette",
        "activity-icon-brain", "activity-icon-save", "activity-icon-gear",
    } <= set(icon_ids)

    page.locator("#prompt-input").fill("activity failure")
    page.locator("#send-button").click()
    failed = page.locator(".activity-step.is-failed").last
    failed.wait_for()
    assert failed.locator(".activity-row-toggle").get_attribute("aria-expanded") == "true"
    assert failed.get_by_role("button", name="Retry step").is_visible()


def test_browser_activity_binary_file_opens_in_the_file_viewer(browser_app, browser_page):
    base_url, _ = browser_app
    _, page = browser_page
    page.set_default_timeout(5000)
    page.goto(f"{base_url}/web/")
    page.locator('body[data-ready="true"]').wait_for(timeout=10000)
    page.locator("#prompt-input").fill("activity binary")
    page.locator("#send-button").click()
    step = page.locator(".activity-step").last
    step.locator(".activity-verb").click()
    card = step.locator(".activity-diff-card")
    card.wait_for()
    assert card.get_by_text("Binary file, 2 KB").is_visible()
    card.get_by_role("button", name="Open").click()
    page.locator("#activity-file-viewer[open]").wait_for()
    assert page.get_by_role("link", name="Open binary file in a new tab").is_visible()


def test_browser_capabilities_render_as_grouped_cards(browser_app, browser_page):
    base_url, _ = browser_app
    _, page = browser_page
    page.goto(f"{base_url}/web/")
    page.locator('body[data-ready="true"]').wait_for(timeout=10000)
    page.locator("#prompt-input").fill("show capabilities")
    page.locator("#send-button").click()
    step = page.locator(".activity-step").last
    step.locator(".activity-row-toggle").click()

    cards = step.locator(".activity-capability-card")
    assert cards.count() == 2
    assert step.get_by_role("heading", name="Providers").is_visible()
    assert step.get_by_role("heading", name="Known gaps").is_visible()
    assert cards.nth(1).locator(".activity-capability-status").inner_text() == "not possible yet"


def test_browser_empty_history_and_permissions_settings(browser_app, browser_page):
    base_url, _ = browser_app
    browser, page = browser_page
    page.goto(f"{base_url}/web/")
    page.locator('body[data-ready="true"]').wait_for(timeout=10000)
    assert page.get_by_text("No chats yet. Start with New chat.").is_visible()
    page.locator("#settings-toggle").click()
    page.locator("#settings-tab-permissions").click()
    panel = page.locator("#settings-panel-permissions")
    assert panel.is_visible()
    checkboxes = panel.locator(".permission-action-row input[type=checkbox]")
    checkboxes.first.wait_for()
    assert checkboxes.count() == 11
    assert page.get_by_role("heading", name="Always allow").is_visible()


def test_browser_stop_is_per_run_and_next_message_succeeds(browser_app, browser_page):
    base_url, _ = browser_app
    browser, page = browser_page
    page.goto(f"{base_url}/web/")
    page.locator('body[data-ready="true"]').wait_for(timeout=10000)
    page.locator("#prompt-input").fill("slow response")
    page.locator("#send-button").click()
    page.locator("#stop-button").wait_for(state="visible")
    page.locator("#stop-button").click()
    page.locator("#stop-button").wait_for(state="hidden")
    page.locator("#prompt-input").fill("a new message after stop")
    page.locator("#send-button").click()
    page.get_by_text("Test answer from fake provider.").wait_for()
