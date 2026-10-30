from __future__ import annotations

import hmac
from collections.abc import Callable


class AuthService:
    def __init__(self, token: str, origins: tuple[str, ...]):
        self.token = token
        self.origins = origins

    def allowed(self, handler, respond: Callable[[dict, int], None]) -> bool:
        origin = handler.headers.get("Origin")
        if origin and origin not in self.origins:
            respond({"error": "Origin is not allowed."}, 403)
            return False
        if self.token:
            supplied = handler.headers.get("Authorization", "")
            if not hmac.compare_digest(supplied, f"Bearer {self.token}"):
                respond({"error": "Authentication required."}, 401)
                return False
        return True
