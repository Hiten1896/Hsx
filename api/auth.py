from __future__ import annotations

from collections.abc import Callable


class AuthService:
    def __init__(self, origins: tuple[str, ...]):
        self.origins = origins

    def allowed(self, handler, respond: Callable[[dict, int], None]) -> bool:
        origin = handler.headers.get("Origin")
        if origin and origin not in self.origins:
            respond({"error": "Origin is not allowed."}, 403)
            return False
        return True
