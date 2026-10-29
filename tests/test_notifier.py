import json
from pathlib import Path

import pytest

from agent.notifier import Notifier, PushResponseError


def test_notification_routes_deduplicate_and_store_titles_only(tmp_path: Path):
    sent = []
    notifier = Notifier(
        tmp_path / "notifications.db",
        routes={"job_done": ["inapp", "webpush"]},
        push_sender=lambda subscription, payload: sent.append((subscription, json.loads(payload))),
    )
    notifier.subscribe(
        {
            "endpoint": "https://fcm.googleapis.com/fcm/send/subscription",
            "keys": {"p256dh": "public", "auth": "secret"},
        },
        "Laptop",
    )
    notifier.notify("job_done", "Report ready", job_id="job-1", result_path="Hsx Work/report.pdf")
    duplicate = notifier.notify("job_done", "Report ready", job_id="job-1", result_path="Hsx Work/report.pdf")

    notifications, unread = notifier.list_notifications()
    assert duplicate["deduplicated"]
    assert unread == 1
    assert notifications[0]["title"] == "Report ready"
    assert set(sent[0][1]) == {"title", "job_id"}
    assert "report.pdf" not in json.dumps(sent[0][1]).lower()


def test_quiet_hours_keep_inapp_but_suppress_external_delivery(tmp_path: Path):
    sent = []
    notifier = Notifier(
        tmp_path / "notifications.db",
        routes={"needs_input": ["inapp", "webpush"]},
        quiet_hours="00:00-23:59",
        push_sender=lambda subscription, payload: sent.append(payload),
    )
    notifier.subscribe(
        {"endpoint": "https://updates.push.services.mozilla.com/wpush/v2/a", "keys": {"p256dh": "a", "auth": "b"}},
        "Phone",
    )
    result = notifier.notify("needs_input", "Hsx needs a choice", job_id="job-2")
    assert result["sent"] == ["inapp"]
    assert sent == []


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://fcm.googleapis.com/send/a",
        "https://evil.example/fcm",
        "https://fcm.googleapis.com.evil.example/send/a",
        "https://user@fcm.googleapis.com/send/a",
        "https://127.0.0.1/send/a",
    ],
)
def test_webpush_rejects_untrusted_endpoints(tmp_path: Path, endpoint: str):
    notifier = Notifier(tmp_path / "notifications.db")
    with pytest.raises(ValueError):
        notifier.subscribe({"endpoint": endpoint, "keys": {"p256dh": "a", "auth": "b"}}, "Phone")


def test_expired_webpush_subscriptions_are_removed(tmp_path: Path):
    notifier = Notifier(
        tmp_path / "notifications.db",
        routes={"job_done": ["webpush"]},
        push_sender=lambda subscription, payload: (_ for _ in ()).throw(PushResponseError(410)),
    )
    notifier.subscribe(
        {"endpoint": "https://web.push.apple.com/a", "keys": {"p256dh": "a", "auth": "b"}},
        "Phone",
    )
    notifier.notify("job_done", "Task done")
    assert notifier.list_subscriptions() == []


def test_webpush_transport_does_not_follow_redirects(tmp_path: Path, monkeypatch):
    import requests
    from requests import Response

    calls = []

    class RedirectAdapter(requests.adapters.BaseAdapter):
        def send(self, request, **kwargs):
            calls.append(request.url)
            response = Response()
            response.status_code = 302
            response.headers["Location"] = "http://127.0.0.1/private"
            response.url = request.url
            response.request = request
            response._content = b""
            return response

        def close(self):
            pass

    def fake_webpush(**kwargs):
        session = kwargs["requests_session"]
        session.mount("https://", RedirectAdapter())
        response = session.post("https://fcm.googleapis.com/push", allow_redirects=True)
        assert response.status_code == 302

    import pywebpush

    monkeypatch.setattr(pywebpush, "webpush", fake_webpush)
    notifier = Notifier(
        tmp_path / "notifications.db",
        vapid_private_key="test-key",
    )

    notifier._push(
        {"endpoint": "https://fcm.googleapis.com/push", "keys": {"p256dh": "a", "auth": "b"}},
        "{}",
    )

    assert calls == ["https://fcm.googleapis.com/push"]


def test_recent_device_mode_sends_only_to_most_recent_device(tmp_path: Path):
    sent = []
    notifier = Notifier(
        tmp_path / "notifications.db",
        routes={"job_done": ["webpush"]},
        target_mode="recent",
        push_sender=lambda subscription, payload: sent.append(subscription["endpoint"]),
    )
    phone = notifier.subscribe(
        {"endpoint": "https://web.push.apple.com/phone", "keys": {"p256dh": "a", "auth": "b"}},
        "Phone",
    )
    laptop = notifier.subscribe(
        {"endpoint": "https://fcm.googleapis.com/send/laptop", "keys": {"p256dh": "c", "auth": "d"}},
        "Laptop",
    )
    with notifier._connect() as connection:
        connection.execute("UPDATE webpush_subscriptions SET last_active = '2026-01-01T00:00:00+00:00' WHERE id = ?", (phone["id"],))
        connection.execute("UPDATE webpush_subscriptions SET last_active = '2026-01-02T00:00:00+00:00' WHERE id = ?", (laptop["id"],))
    notifier.notify("job_done", "Task done")
    assert sent == ["https://fcm.googleapis.com/send/laptop"]
