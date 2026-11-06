import json
import threading
import time
from http.server import ThreadingHTTPServer

import pytest
from playwright.sync_api import sync_playwright

import server
from agent.conversations import ConversationStore
from agent.notifier import Notifier


class BrowserFakeHandler(server.HsxHandler):
    def do_POST(self):
        if self._request_path() == "/api/feedback":
            self._json({"success": True, "saved": True}, 201)
            return
        if self._request_path() != "/api/chat/stream":
            return super().do_POST()
        payload = self._body()
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
    monkeypatch.setattr(server, "NOTIFIER", Notifier(tmp_path / "notifications.sqlite"))
    host = ThreadingHTTPServer(("127.0.0.1", 0), BrowserFakeHandler)
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
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.route("https://cdn.jsdelivr.net/**", lambda route: route.abort())
        yield browser, page
        browser.close()


def test_browser_new_chat_saves_thread_and_does_not_save_empty_chats(browser_app, browser_page):
    base_url, _ = browser_app
    browser, page = browser_page
    page.goto(f"{base_url}/web/")
    page.locator('body[data-ready="true"]').wait_for(timeout=10000)
    composer = page.locator("#prompt-input")
    composer.fill("A saved browser conversation")
    page.locator("#send-button").click()
    page.get_by_text("Test answer from fake provider.").wait_for()
    page.locator("#new-chat-button").click()
    assert composer.evaluate("element => document.activeElement === element")
    assert page.locator("#chat-stream .message-card").count() == 0
    assert page.get_by_role("button", name="Open chat: A saved browser conversation").count() == 1
    page.locator("#new-chat-button").click()
    page.locator("#new-chat-button").click()
    assert page.locator(".chat-history-row").count() == 1
    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")


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
    composer.press("Shift+Enter")
    assert "\n" in composer.input_value()
    composer.fill("Keyboard submits a real message")
    composer.press("Enter")
    page.get_by_text("Test answer from fake provider.").wait_for()
    assert send.is_disabled()


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
