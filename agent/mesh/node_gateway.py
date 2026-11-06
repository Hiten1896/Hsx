from __future__ import annotations

import hmac
import ipaddress
import json
import logging
import os
import re
import secrets
import ssl
import stat
import socket
import threading
import time
from collections import OrderedDict
from email.message import Message
from pathlib import Path

from agent.ml.storage import atomic_write_json
from config import ALLOWED_ORIGINS, DATA_DIR, is_loopback_host

JOB_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
DISCOVERY_GROUP = "239.255.42.99"
DISCOVERY_PORT = 45800
DISCOVERY_REQUEST = b"HSX_DISCOVER_V1"
LOGGER = logging.getLogger("hsx.mesh.discovery")
PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "169.254.0.0/16",
        "fc00::/7",
        "fe80::/10",
    )
)


def _is_private_lan_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return any(address.version == network.version and address in network for network in PRIVATE_NETWORKS)
GET_ROUTES = {
    "/api/mesh/info",
    "/api/mesh/telemetry",
    "/api/mesh/camera/status",
    "/api/mesh/camera/stream",
}


class MeshGatewayConfigurationError(ValueError):
    """Raised when an unsafe or incomplete mesh listener is configured."""


class MeshGateway:
    """TLS-protected, token-authenticated subset of the local HSX API."""

    def __init__(
        self,
        host: str,
        token: str,
        certificate: Path,
        private_key: Path,
        *,
        port: int = 8000,
        node_id_path: Path | None = None,
    ):
        try:
            address = ipaddress.ip_address(host)
        except ValueError as error:
            raise MeshGatewayConfigurationError(
                "The mesh listener must bind to a private IP address, not a wildcard or hostname."
            ) from error
        if address.version != 4:
            raise MeshGatewayConfigurationError("The mesh listener currently requires a private IPv4 address.")
        if address.is_loopback or address.is_unspecified or not _is_private_lan_address(address):
            raise MeshGatewayConfigurationError("The mesh listener must bind to a private, non-loopback IP.")
        if len(token.encode("utf-8")) < 32 or token.strip() != token:
            raise MeshGatewayConfigurationError("HSX_MESH_TOKEN must contain at least 32 non-padded characters.")
        if not 1 <= port <= 65535:
            raise MeshGatewayConfigurationError("Mesh port must be between 1 and 65535.")
        self.host = host
        self.port = port
        self._token = token
        self.certificate = Path(certificate).expanduser().resolve()
        self.private_key = Path(private_key).expanduser().resolve()
        self.node_id_path = Path(node_id_path or DATA_DIR / "mesh_node.json")

    @classmethod
    def from_environment(cls) -> "MeshGateway | None":
        host = os.getenv("HSX_MESH_HOST", "").strip()
        if not host:
            return None
        if is_loopback_host(host):
            raise MeshGatewayConfigurationError("HSX_MESH_HOST must be a private LAN address.")
        token = os.getenv("HSX_MESH_TOKEN", "")
        certificate = os.getenv("HSX_MESH_TLS_CERT", "")
        private_key = os.getenv("HSX_MESH_TLS_KEY", "")
        if not token or not certificate or not private_key:
            raise MeshGatewayConfigurationError(
                "HSX_MESH_HOST requires HSX_MESH_TOKEN, HSX_MESH_TLS_CERT, and HSX_MESH_TLS_KEY."
            )
        return cls(
            host,
            token,
            Path(certificate),
            Path(private_key),
            port=int(os.getenv("HSX_MESH_PORT", "8443")),
        )

    @property
    def node_id(self) -> str:
        if self.node_id_path.exists():
            try:
                data = json.loads(self.node_id_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise MeshGatewayConfigurationError("The persisted mesh node identity is invalid.") from error
            node_id = data.get("node_id") if isinstance(data, dict) else None
            if isinstance(node_id, str) and re.fullmatch(r"[a-f0-9]{32}", node_id):
                return node_id
            raise MeshGatewayConfigurationError("The persisted mesh node identity is invalid.")
        node_id = secrets.token_hex(16)
        atomic_write_json(self.node_id_path, {"node_id": node_id})
        return node_id

    def node_info(self) -> dict[str, object]:
        return {
            "node_id": self.node_id,
            "api_version": 1,
            "host": self.host,
            "port": self.port,
            "transport": "https",
        }

    def tls_context(self) -> ssl.SSLContext:
        if not self.certificate.is_file() or not self.private_key.is_file():
            raise MeshGatewayConfigurationError("Mesh TLS certificate or private key is not a readable file.")
        if os.name == "posix" and stat.S_IMODE(self.private_key.stat().st_mode) & 0o077:
            raise MeshGatewayConfigurationError("Mesh TLS private key permissions must not allow group or other access.")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(self.certificate, self.private_key)
        return context

    def wrap_server(self, http_server) -> None:
        http_server.socket = self.tls_context().wrap_socket(http_server.socket, server_side=True)

    @staticmethod
    def _route_allowed(method: str, path: str) -> bool:
        if method == "GET":
            return path in GET_ROUTES or (
                path.startswith("/api/mesh/jobs/")
                and JOB_ID.fullmatch(path.removeprefix("/api/mesh/jobs/")) is not None
            )
        return method == "POST" and path == "/api/mesh/tasks"

    def authorize(
        self,
        method: str,
        path: str,
        headers: Message,
        client_address: str,
    ) -> tuple[bool, int, str]:
        try:
            peer = ipaddress.ip_address(client_address)
        except ValueError:
            return False, 403, "Mesh clients must use a private IP address."
        if peer.is_loopback or peer.is_unspecified or not _is_private_lan_address(peer):
            return False, 403, "Mesh clients must use a private IP address."
        if method == "OPTIONS":
            requested_method = headers.get("Access-Control-Request-Method", "").upper()
            origin = headers.get("Origin", "")
            if origin not in ALLOWED_ORIGINS or not self._route_allowed(requested_method, path):
                return False, 403, "Mesh preflight request is not allowed."
            return True, 200, ""
        if not self._route_allowed(method, path):
            return False, 403, "This endpoint is not available through the mesh gateway."
        authorization = headers.get("Authorization", "")
        scheme, _, supplied = authorization.partition(" ")
        if scheme.casefold() != "bearer" or not hmac.compare_digest(supplied, self._token):
            return False, 401, "A valid mesh bearer token is required."
        return True, 200, ""


class MeshDiscoveryService:
    """Reply to small IPv4 multicast discovery probes with non-sensitive node metadata."""

    def __init__(self, gateway: MeshGateway, port: int = DISCOVERY_PORT):
        if ipaddress.ip_address(gateway.host).version != 4:
            raise MeshGatewayConfigurationError("Mesh discovery currently requires an IPv4 listener.")
        if not 1 <= port <= 65535:
            raise ValueError("Discovery port must be between 1 and 65535.")
        self.gateway = gateway
        self.port = port
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._socket: socket.socket | None = None
        self._last_reply: OrderedDict[str, float] = OrderedDict()
        self._lock = threading.Lock()

    def response_for(self, payload: bytes, client_address: str) -> bytes | None:
        if payload != DISCOVERY_REQUEST:
            return None
        try:
            peer = ipaddress.ip_address(client_address)
        except ValueError:
            return None
        if peer.is_loopback or peer.is_unspecified or not _is_private_lan_address(peer):
            return None
        now = time.monotonic()
        with self._lock:
            last_reply = self._last_reply.get(client_address, 0)
            if now - last_reply < 1:
                return None
            self._last_reply[client_address] = now
            self._last_reply.move_to_end(client_address)
            while len(self._last_reply) > 128:
                self._last_reply.popitem(last=False)
        return json.dumps(
            {
                "service": "hsx",
                "api_version": 1,
                "node_id": self.gateway.node_id,
                "host": self.gateway.host,
                "port": self.gateway.port,
                "transport": "https",
            },
            separators=(",", ":"),
        ).encode("utf-8")

    def start(self) -> bool:
        if self._thread is not None and self._thread.is_alive():
            return False
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("", self.port))
            membership = socket.inet_aton(DISCOVERY_GROUP) + socket.inet_aton(self.gateway.host)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
            sock.settimeout(1.0)
        except OSError:
            sock.close()
            raise
        self._socket = sock
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._serve,
            name="hsx-mesh-discovery",
            daemon=True,
        )
        self._thread.start()
        return True

    def _serve(self) -> None:
        sock = self._socket
        if sock is None:
            raise RuntimeError("Mesh discovery socket was not initialized.")
        while not self._stop.is_set():
            try:
                payload, (address, port) = sock.recvfrom(128)
            except socket.timeout:
                continue
            except OSError:
                if not self._stop.is_set():
                    LOGGER.exception("Mesh discovery listener failed.")
                return
            response = self.response_for(payload, address)
            if response is not None:
                try:
                    sock.sendto(response, (address, port))
                except OSError:
                    if not self._stop.is_set():
                        LOGGER.exception("Mesh discovery reply failed.")
                    return

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        sock = self._socket
        self._socket = None
        if sock is not None:
            sock.close()
        if self._thread is not None:
            self._thread.join(timeout)


def discover_nodes(timeout_seconds: float = 1.5) -> list[dict[str, object]]:
    """Discover HSX nodes advertising on the local IPv4 multicast group."""
    if not 0.1 <= timeout_seconds <= 10:
        raise ValueError("timeout_seconds must be between 0.1 and 10.")
    found: dict[str, dict[str, object]] = {}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as sock:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        sock.settimeout(min(timeout_seconds, 0.5))
        sock.sendto(DISCOVERY_REQUEST, (DISCOVERY_GROUP, DISCOVERY_PORT))
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                payload, _ = sock.recvfrom(512)
            except socket.timeout:
                continue
            try:
                node = json.loads(payload)
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if (
                isinstance(node, dict)
                and node.get("service") == "hsx"
                and node.get("api_version") == 1
                and isinstance(node.get("node_id"), str)
                and re.fullmatch(r"[a-f0-9]{32}", node["node_id"])
                and isinstance(node.get("host"), str)
                and isinstance(node.get("port"), int)
                and 1 <= node["port"] <= 65535
                and node.get("transport") == "https"
            ):
                try:
                    advertised_host = ipaddress.ip_address(node["host"])
                except ValueError:
                    continue
                if _is_private_lan_address(advertised_host) and not advertised_host.is_loopback:
                    found[node["node_id"]] = node
    return list(found.values())
