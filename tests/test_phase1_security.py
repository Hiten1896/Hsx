import json
import threading
import urllib.error
import urllib.request
from pathlib import Path
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import pytest

import server
from api.auth import AuthService
from agent.security import safe_path


def request(server_url: str, path: str, *, origin: str | None = None, authorization: str | None = None):
    headers = {}
    if origin:
        headers["Origin"] = origin
    if authorization:
        headers["Authorization"] = authorization
    request = urllib.request.Request(server_url + path, headers=headers)
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_safe_path_rejects_traversal_and_symlink(tmp_path: Path):
    root = tmp_path / "allowed"
    root.mkdir()
    assert safe_path(root, "file.txt") == root / "file.txt"
    with pytest.raises(ValueError):
        safe_path(root, "../outside.txt")
    link = root / "link"
    try:
        link.symlink_to(tmp_path / "outside", target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable on this Windows environment")
    with pytest.raises(ValueError):
        safe_path(root, "link/file.txt")


def test_api_rejects_foreign_origin(monkeypatch):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.HsxHandler)
    monkeypatch.setattr(
        server,
        "AUTH_SERVICE",
        AuthService(
            (f"http://127.0.0.1:{httpd.server_port}",),
            (f"127.0.0.1:{httpd.server_port}",),
            "test-token",
        ),
    )
    thread = threading.Thread(target=httpd.handle_request)
    thread.start()
    status, payload = request(
        f"http://127.0.0.1:{httpd.server_port}", "/api/skills",
        origin="https://foreign.example",
    )
    thread.join(timeout=2)
    assert status == 403
    assert "origin" in payload["error"].lower()


def test_api_requires_token_when_origin_is_missing(monkeypatch):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.HsxHandler)
    token = "test-token"
    monkeypatch.setattr(
        server,
        "AUTH_SERVICE",
        AuthService(
            (f"http://127.0.0.1:{httpd.server_port}",),
            (f"127.0.0.1:{httpd.server_port}",),
            token,
        ),
    )
    thread = threading.Thread(target=httpd.handle_request)
    thread.start()
    status, payload = request(f"http://127.0.0.1:{httpd.server_port}", "/api/skills")
    thread.join(timeout=2)
    assert status == 401
    httpd.server_close()

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.HsxHandler)
    monkeypatch.setattr(
        server,
        "AUTH_SERVICE",
        AuthService(
            (f"http://127.0.0.1:{httpd.server_port}",),
            (f"127.0.0.1:{httpd.server_port}",),
            token,
        ),
    )
    thread = threading.Thread(target=httpd.handle_request)
    thread.start()
    status, payload = request(
        f"http://127.0.0.1:{httpd.server_port}",
        "/api/skills",
        authorization=f"Bearer {token}",
    )
    thread.join(timeout=2)
    assert status == 200
    assert "skills" in payload
    httpd.server_close()


def test_health_accepts_requests_without_api_auth(monkeypatch):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.HsxHandler)
    monkeypatch.setattr(
        server,
        "AUTH_SERVICE",
        AuthService((), (f"127.0.0.1:{httpd.server_port}",)),
    )
    thread = threading.Thread(target=httpd.handle_request)
    thread.start()
    status, payload = request(
        f"http://127.0.0.1:{httpd.server_port}", "/health"
    )
    thread.join(timeout=2)
    assert status == 200
    assert payload == {"status": "ok"}


def test_non_loopback_requests_remain_blocked(monkeypatch):
    monkeypatch.setattr(server, "is_loopback_host", lambda _host: False)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.HsxHandler)
    thread = threading.Thread(target=httpd.handle_request)
    thread.start()
    status, payload = request(f"http://127.0.0.1:{httpd.server_port}", "/api/skills")
    thread.join(timeout=2)
    httpd.server_close()
    assert status == 403
    assert "local" in payload["error"].lower()


@pytest.mark.parametrize("peer", ["100.64.0.1", "fd7a:115c:a1e0::1"])
def test_tailscale_peer_requires_and_accepts_bearer_token(monkeypatch, peer):
    token = "test-remote-access-token-value-long-enough"
    monkeypatch.setattr(server, "REMOTE_ACCESS_TOKEN", token)
    monkeypatch.setattr(server, "MESH_GATEWAY", None)
    monkeypatch.setattr(server, "AUTH_SERVICE", SimpleNamespace(allowed=lambda *_args: True))
    handler = server.HsxHandler.__new__(server.HsxHandler)
    handler.client_address = (peer, 1234)
    handler.command = "GET"
    handler.path = "/api/skills"
    handler.headers = {"Authorization": f"Bearer {token}"}

    assert server._is_tailscale_peer(peer)
    assert handler._api_access_allowed()


def test_tailscale_peer_can_load_public_ui_shell_before_entering_token(monkeypatch):
    monkeypatch.setattr(server, "REMOTE_ACCESS_TOKEN", "test-remote-access-token-value-long-enough")
    monkeypatch.setattr(server, "MESH_GATEWAY", None)
    handler = server.HsxHandler.__new__(server.HsxHandler)
    handler.client_address = ("100.64.0.4", 1234)
    handler.command = "GET"
    handler.path = "/web/"
    handler.headers = {}

    assert handler._api_access_allowed()


def test_tailscale_peer_rejects_missing_or_invalid_bearer_token(monkeypatch):
    token = "test-remote-access-token-value-long-enough"
    monkeypatch.setattr(server, "REMOTE_ACCESS_TOKEN", token)
    monkeypatch.setattr(server, "MESH_GATEWAY", None)
    monkeypatch.setattr(server, "AUTH_SERVICE", SimpleNamespace(allowed=lambda *_args: True))
    handler = server.HsxHandler.__new__(server.HsxHandler)
    handler.client_address = ("100.64.0.2", 1234)
    handler.command = "GET"
    handler.path = "/api/skills"
    handler.headers = {"Authorization": "Bearer wrong-token"}
    responses = []
    handler._json = lambda payload, status=200: responses.append((payload, status))

    assert not handler._api_access_allowed()
    assert responses[0][1] == 401


def test_tailscale_peer_remains_blocked_when_remote_token_is_unset(monkeypatch):
    monkeypatch.setattr(server, "REMOTE_ACCESS_TOKEN", "")
    monkeypatch.setattr(server, "MESH_GATEWAY", None)
    monkeypatch.setattr(server, "AUTH_SERVICE", SimpleNamespace(allowed=lambda *_args: True))
    handler = server.HsxHandler.__new__(server.HsxHandler)
    handler.client_address = ("100.64.0.3", 1234)
    handler.command = "GET"
    handler.path = "/api/skills"
    handler.headers = {}
    responses = []
    handler._json = lambda payload, status=200: responses.append((payload, status))

    assert not handler._api_access_allowed()
    assert responses[0][1] == 401


def test_non_tailscale_peers_are_not_authorized_by_remote_token(monkeypatch):
    monkeypatch.setattr(server, "REMOTE_ACCESS_TOKEN", "test-remote-access-token-value-long-enough")
    monkeypatch.setattr(server, "MESH_GATEWAY", None)
    handler = server.HsxHandler.__new__(server.HsxHandler)
    handler.client_address = ("192.168.1.2", 1234)
    handler.command = "GET"
    handler.path = "/api/skills"
    handler.headers = {"Authorization": "Bearer test-remote-access-token-value-long-enough"}
    responses = []
    handler._json = lambda payload, status=200: responses.append((payload, status))

    assert not handler._api_access_allowed()
    assert responses[0][1] == 403


def test_run_code_does_not_use_substring_blocklist(monkeypatch):
    from agent.sandbox import linux_network_sandbox_available

    if not linux_network_sandbox_available():
        pytest.skip("Linux network sandbox is unavailable")
    monkeypatch.setattr("skills.run_code.skill.SANDBOX", "subprocess")
    from skills.run_code.skill import run

    result = run("print(__import__('math').sqrt(9))")
    assert result["success"]
    assert result["stdout"].strip() == "3.0"
