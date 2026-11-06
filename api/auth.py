from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlsplit


class AuthService:
    def __init__(
        self,
        origins: tuple[str, ...],
        hosts: tuple[str, ...],
        bearer_token: str = "",
    ):
        self.origins = {self._origin_key(origin) for origin in origins}
        self.hosts = {host.strip().lower() for host in hosts if host.strip()}
        self.bearer_token = bearer_token

    @staticmethod
    def _origin_key(value: str) -> tuple[str, str, int] | None:
        try:
            parsed = urlsplit(value)
            if (
                parsed.scheme.lower() not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.path
                or parsed.query
                or parsed.fragment
            ):
                return None
            default_port = 443 if parsed.scheme.lower() == "https" else 80
            return parsed.scheme.lower(), parsed.hostname.lower(), parsed.port or default_port
        except ValueError:
            return None

    def allowed(
        self,
        handler,
        respond: Callable[[dict, int], None],
        *,
        require_no_origin_bearer: bool = True,
    ) -> bool:
        host = handler.headers.get("Host", "").strip().lower()
        if not host or host not in self.hosts:
            respond({"error": "This Hsx host name is not allowed."}, 403)
            return False
        origin = handler.headers.get("Origin")
        path = handler.path.split("?", 1)[0]
        if origin is not None:
            origin_key = self._origin_key(origin)
            if origin_key is None or origin_key not in self.origins:
                respond({"error": "This page origin is not allowed. Open Hsx from its configured address."}, 403)
                return False
        elif path.startswith("/api/") and require_no_origin_bearer:
            scheme, separator, supplied_token = handler.headers.get("Authorization", "").partition(" ")
            import hmac

            if (
                not self.bearer_token
                or scheme.casefold() != "bearer"
                or not separator
                or not hmac.compare_digest(supplied_token, self.bearer_token)
            ):
                respond({"error": "A valid bearer token is required for requests without an Origin header."}, 401)
                return False
        return True
