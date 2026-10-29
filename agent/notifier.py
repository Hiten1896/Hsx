import hashlib
import json
import os
import sqlite3
import stat
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit


EVENTS = {"job_done", "needs_input", "needs_approval", "job_failed", "health_alert", "heartbeat"}
PUSH_HOSTS = {
    "fcm.googleapis.com",
    "updates.push.services.mozilla.com",
    "web.push.apple.com",
}
DEFAULT_ROUTES = {
    "job_done": ("inapp", "webpush"),
    "needs_input": ("inapp", "webpush"),
    "needs_approval": ("inapp", "webpush"),
    "job_failed": ("inapp", "webpush"),
    "health_alert": ("inapp", "webpush", "telegram"),
    "heartbeat": ("telegram",),
}


class PushResponseError(RuntimeError):
    def __init__(self, status_code: int):
        self.status_code = status_code


class Notifier:
    def __init__(
        self,
        database_path: Path,
        routes: dict[str, list[str] | tuple[str, ...]] | None = None,
        quiet_hours: str | None = None,
        push_sender=None,
        target_mode: str | None = None,
        vapid_private_key: str | None = None,
        vapid_public_key: str | None = None,
    ):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.routes = self._load_routes(routes)
        self.quiet_hours = quiet_hours if quiet_hours is not None else os.getenv("HSX_QUIET_HOURS", "")
        self.target_mode = (target_mode or os.getenv("HSX_WEBPUSH_TARGET_MODE", "all")).strip().lower()
        if self.target_mode not in {"all", "recent"}:
            raise ValueError("Web Push target mode must be 'all' or 'recent'.")
        self.vapid_private_key = vapid_private_key or os.getenv("HSX_VAPID_PRIVATE_KEY", "")
        self.vapid_private_key_file = os.getenv(
            "HSX_VAPID_PRIVATE_KEY_FILE", str(self.database_path.parent / "vapid-private.pem")
        )
        self.vapid_public_key = vapid_public_key or os.getenv("HSX_VAPID_PUBLIC_KEY", "")
        self.push_sender = push_sender
        self._condition = threading.Condition()
        self._initialize()

    @staticmethod
    def _load_routes(routes):
        selected = {}
        for event in EVENTS:
            default = routes.get(event, DEFAULT_ROUTES[event]) if routes else DEFAULT_ROUTES[event]
            env_key = f"HSX_NOTIFY_{event.upper()}"
            if routes is None and env_key in os.environ:
                default = tuple(item.strip() for item in os.getenv(env_key, "").split(",") if item.strip())
            unknown = set(default) - {"inapp", "webpush", "telegram"}
            if unknown:
                raise ValueError(f"Unknown notification channel(s) for {event}.")
            selected[event] = tuple(dict.fromkeys(default))
        return selected

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT, event TEXT NOT NULL, title TEXT NOT NULL,
                job_id TEXT NOT NULL DEFAULT '', result_path TEXT NOT NULL DEFAULT '',
                created TEXT NOT NULL, read INTEGER NOT NULL DEFAULT 0, dedupe_key TEXT NOT NULL)"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS notification_dedupe (
                dedupe_key TEXT PRIMARY KEY, created TEXT NOT NULL)"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS webpush_subscriptions (
                id TEXT PRIMARY KEY, endpoint TEXT NOT NULL UNIQUE, keys TEXT NOT NULL,
                device_name TEXT NOT NULL, last_active TEXT NOT NULL, created TEXT NOT NULL)"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS notification_deliveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT, channel TEXT NOT NULL, created TEXT NOT NULL)"""
            )

    @staticmethod
    def validate_endpoint(endpoint: str) -> str:
        if not isinstance(endpoint, str) or len(endpoint) > 2048:
            raise ValueError("Invalid Web Push endpoint.")
        parsed = urlsplit(endpoint)
        host = (parsed.hostname or "").rstrip(".").lower()
        if (
            parsed.scheme != "https"
            or not host
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
            or parsed.query == ""
            and not parsed.path
        ):
            raise ValueError("Web Push endpoints must be HTTPS URLs on an approved push service.")
        allowed = host in PUSH_HOSTS or host.endswith(".notify.windows.com")
        if not allowed:
            raise ValueError("Web Push endpoint host is not an approved push service.")
        return endpoint

    def subscribe(self, subscription: dict, device_name: str) -> dict:
        if not isinstance(subscription, dict) or not isinstance(subscription.get("keys"), dict):
            raise ValueError("A valid Web Push subscription is required.")
        endpoint = self.validate_endpoint(subscription.get("endpoint", ""))
        keys = subscription["keys"]
        if not all(isinstance(keys.get(name), str) and keys[name] for name in ("p256dh", "auth")):
            raise ValueError("The Web Push subscription requires p256dh and auth keys.")
        device_name = device_name.strip()
        if not device_name or len(device_name) > 100:
            raise ValueError("Device name must contain 1 to 100 characters.")
        now = datetime.now(UTC).isoformat()
        identifier = hashlib.sha256(endpoint.encode()).hexdigest()[:32]
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO webpush_subscriptions(id, endpoint, keys, device_name, last_active, created)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(endpoint) DO UPDATE SET keys = excluded.keys,
                device_name = excluded.device_name, last_active = excluded.last_active""",
                (identifier, endpoint, json.dumps(keys), device_name, now, now),
            )
            row = connection.execute(
                "SELECT id, device_name, last_active FROM webpush_subscriptions WHERE endpoint = ?",
                (endpoint,),
            ).fetchone()
        return dict(row)

    def unsubscribe(self, subscription_id: str) -> bool:
        with self._connect() as connection:
            changed = connection.execute(
                "DELETE FROM webpush_subscriptions WHERE id = ?", (subscription_id,)
            ).rowcount
        return bool(changed)

    def list_subscriptions(self) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, device_name, last_active FROM webpush_subscriptions ORDER BY last_active DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def touch_subscription(self, subscription_id: str) -> bool:
        with self._connect() as connection:
            changed = connection.execute(
                "UPDATE webpush_subscriptions SET last_active = ? WHERE id = ?",
                (datetime.now(UTC).isoformat(), subscription_id),
            ).rowcount
        return bool(changed)

    @property
    def public_key(self) -> str:
        return self.vapid_public_key

    def send_test(self, subscription_id: str | None = None) -> None:
        with self._connect() as connection:
            if subscription_id:
                rows = connection.execute(
                    "SELECT id, endpoint, keys FROM webpush_subscriptions WHERE id = ?", (subscription_id,)
                ).fetchall()
            else:
                rows = connection.execute("SELECT id, endpoint, keys FROM webpush_subscriptions").fetchall()
        if not rows:
            raise ValueError("No matching Web Push subscription is registered.")
        payload = json.dumps({"title": "Hsx notification test", "job_id": ""}, separators=(",", ":"))
        for row in rows:
            subscription = {"endpoint": row["endpoint"], "keys": json.loads(row["keys"])}
            try:
                self._push(subscription, payload)
            except PushResponseError as error:
                if error.status_code in {404, 410}:
                    self.unsubscribe(row["id"])
                    continue
                raise RuntimeError("Web Push test delivery failed.") from None

    @staticmethod
    def _is_quiet(quiet_hours: str, now: datetime | None = None) -> bool:
        if not quiet_hours:
            return False
        try:
            start_text, end_text = quiet_hours.split("-", 1)
            start_hour, start_minute = (int(value) for value in start_text.split(":"))
            end_hour, end_minute = (int(value) for value in end_text.split(":"))
            start = start_hour * 60 + start_minute
            end = end_hour * 60 + end_minute
        except (ValueError, TypeError):
            raise ValueError("Quiet hours must use HH:MM-HH:MM format.")
        current = now or datetime.now().astimezone()
        minute = current.hour * 60 + current.minute
        return start <= minute < end if start <= end else minute >= start or minute < end

    def _record_delivery(self, channel: str) -> bool:
        now = datetime.now(UTC)
        cutoff = (now - timedelta(minutes=1)).isoformat()
        with self._connect() as connection:
            connection.execute("DELETE FROM notification_deliveries WHERE created < ?", ((now - timedelta(days=1)).isoformat(),))
            count = connection.execute(
                "SELECT COUNT(*) FROM notification_deliveries WHERE created >= ?", (cutoff,)
            ).fetchone()[0]
            if count >= 30:
                return False
            connection.execute(
                "INSERT INTO notification_deliveries(channel, created) VALUES (?, ?)",
                (channel, now.isoformat()),
            )
        return True

    def notify(
        self,
        event: str,
        title: str,
        *,
        job_id: str = "",
        result_path: str = "",
        dedupe_key: str = "",
    ) -> dict:
        if event not in EVENTS:
            raise ValueError("Unknown notification event.")
        clean_title = " ".join(str(title).split())[:120]
        if not clean_title:
            raise ValueError("Notification title is required.")
        key_value = dedupe_key or f"{event}\0{job_id}\0{clean_title}"
        dedupe = hashlib.sha256(key_value.encode("utf-8")).hexdigest()
        now = datetime.now(UTC)
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT created FROM notification_dedupe WHERE dedupe_key = ?", (dedupe,)
            ).fetchone()
            if existing and datetime.fromisoformat(existing["created"]) > now - timedelta(minutes=5):
                return {"sent": [], "deduplicated": True}
            connection.execute(
                """INSERT INTO notification_dedupe(dedupe_key, created) VALUES (?, ?)
                ON CONFLICT(dedupe_key) DO UPDATE SET created = excluded.created""",
                (dedupe, now.isoformat()),
            )

        channels = self.routes[event]
        if "inapp" in channels:
            with self._connect() as connection:
                connection.execute(
                    """INSERT INTO notifications(event, title, job_id, result_path, created, dedupe_key)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    (event, clean_title, job_id, result_path, now.isoformat(), dedupe),
                )
            with self._condition:
                self._condition.notify_all()

        sent = ["inapp"] if "inapp" in channels else []
        if not self._is_quiet(self.quiet_hours):
            for channel in channels:
                if channel == "inapp":
                    continue
                if channel == "webpush":
                    if not self._record_delivery(channel):
                        continue
                    self._send_webpush(clean_title, job_id)
                    sent.append(channel)
                elif channel == "telegram":
                    if self._send_telegram(clean_title):
                        sent.append(channel)
        return {"sent": sent, "deduplicated": False}

    def _send_webpush(self, title: str, job_id: str) -> None:
        with self._connect() as connection:
            query = "SELECT id, endpoint, keys, device_name FROM webpush_subscriptions ORDER BY last_active DESC"
            subscriptions = connection.execute(query).fetchall()
        if self.target_mode == "recent":
            subscriptions = subscriptions[:1]
        if not subscriptions:
            return
        payload = json.dumps({"title": title, "job_id": job_id}, separators=(",", ":"))
        for row in subscriptions:
            subscription = {"endpoint": row["endpoint"], "keys": json.loads(row["keys"])}
            try:
                self._push(subscription, payload)
            except PushResponseError as error:
                if error.status_code in {404, 410}:
                    self.unsubscribe(row["id"])
                    continue
                raise RuntimeError("Web Push delivery failed.") from None

    def _push(self, subscription: dict, payload: str) -> None:
        if self.push_sender is not None:
            self.push_sender(subscription, payload)
            return
        try:
            import requests
            from pywebpush import WebPushException, webpush
        except ImportError as error:
            raise RuntimeError("pywebpush is required to send Web Push notifications.") from error

        class NoRedirectSession(requests.Session):
            def post(self, url, *args, **kwargs):
                kwargs["allow_redirects"] = False
                return super().post(url, *args, **kwargs)

        private_key = self.vapid_private_key
        if not private_key:
            key_path = Path(self.vapid_private_key_file)
            if not key_path.is_file():
                raise RuntimeError("The VAPID private key is not configured.")
            if os.name != "nt" and stat.S_IMODE(key_path.stat().st_mode) & 0o027:
                raise PermissionError("VAPID private key permissions must not grant group write or other access.")
            private_key = str(key_path)
        try:
            webpush(
                subscription_info=subscription,
                data=payload,
                vapid_private_key=private_key,
                vapid_claims={"sub": os.getenv("HSX_VAPID_SUBJECT", "mailto:admin@localhost")},
                requests_session=NoRedirectSession(),
            )
        except WebPushException as error:
            status = getattr(getattr(error, "response", None), "status_code", 0)
            raise PushResponseError(status) from None

    def _send_telegram(self, title: str) -> bool:
        from interfaces.telegram import TelegramBot

        bot = TelegramBot()
        if not bot.token:
            return False
        delivered = False
        for chat_id in sorted(bot.allowed_user_ids):
            if self._record_delivery("telegram"):
                try:
                    bot.send_message(chat_id, title)
                except OSError:
                    raise RuntimeError("Telegram notification delivery failed.") from None
                delivered = True
        return delivered

    def list_notifications(self, limit: int = 100) -> tuple[list[dict], int]:
        limit = min(max(int(limit), 1), 200)
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT id, event, title, job_id, result_path, created, read
                FROM notifications ORDER BY id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
            unread = connection.execute("SELECT COUNT(*) FROM notifications WHERE read = 0").fetchone()[0]
        return [dict(row) for row in rows], unread

    def mark_read(self, notification_id: int | None = None) -> int:
        with self._connect() as connection:
            if notification_id is None:
                return connection.execute("UPDATE notifications SET read = 1 WHERE read = 0").rowcount
            return connection.execute(
                "UPDATE notifications SET read = 1 WHERE id = ?", (notification_id,)
            ).rowcount

    def wait_for_notifications(self, after_id: int, timeout: float = 15) -> list[dict]:
        with self._condition:
            with self._connect() as connection:
                rows = connection.execute(
                    """SELECT id, event, title, job_id, result_path, created, read
                    FROM notifications WHERE id > ? ORDER BY id""",
                    (after_id,),
                ).fetchall()
            if not rows:
                self._condition.wait(timeout)
                with self._connect() as connection:
                    rows = connection.execute(
                        """SELECT id, event, title, job_id, result_path, created, read
                        FROM notifications WHERE id > ? ORDER BY id""",
                        (after_id,),
                    ).fetchall()
        return [dict(row) for row in rows]
