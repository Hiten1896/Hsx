import os
import urllib.parse
import urllib.request


class TelegramBot:
    def __init__(self, token: str | None = None, allowed_user_ids: set[int] | None = None):
        self.token = token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.allowed_user_ids = allowed_user_ids or {
            int(value) for value in os.getenv("TELEGRAM_ALLOWED_USER_IDS", "").split(",") if value.strip()
        }

    def handle_update(self, update: dict, reply) -> bool:
        message = update.get("message", {})
        user_id = message.get("from", {}).get("id")
        text = message.get("text", "")
        if not isinstance(user_id, int) or user_id not in self.allowed_user_ids:
            return False
        reply(text, user_id)
        return True

    def send_message(self, chat_id: int, text: str) -> None:
        if not self.token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured.")
        payload = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{self.token}/sendMessage", data=payload, method="POST"
        )
        with urllib.request.urlopen(request, timeout=15):
            pass
