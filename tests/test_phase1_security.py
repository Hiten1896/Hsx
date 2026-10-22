import json
import threading
import urllib.error
import urllib.request
from pathlib import Path
from http.server import ThreadingHTTPServer

import pytest

import server
from agent.security import safe_path


def request(server_url: str, path: str, *, origin: str | None = None, token: str | None = None):
    headers = {}
    if origin:
        headers["Origin"] = origin
    if token:
        headers["Authorization"] = f"Bearer {token}"
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


def test_api_rejects_foreign_origin():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.HsxHandler)
    thread = threading.Thread(target=httpd.handle_request)
    thread.start()
    status, payload = request(
        f"http://127.0.0.1:{httpd.server_port}", "/api/skills",
        origin="https://foreign.example",
    )
    thread.join(timeout=2)
    assert status == 403
    assert "Origin" in payload["error"]


def test_api_requires_bearer_token(monkeypatch):
    monkeypatch.setattr(server, "AUTH_TOKEN", "test-token")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.HsxHandler)
    thread = threading.Thread(target=httpd.handle_request)
    thread.start()
    status, payload = request(f"http://127.0.0.1:{httpd.server_port}", "/api/skills")
    thread.join(timeout=2)
    assert status == 401
    assert "Authentication" in payload["error"]


def test_health_is_available_without_api_auth():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.HsxHandler)
    thread = threading.Thread(target=httpd.handle_request)
    thread.start()
    status, payload = request(f"http://127.0.0.1:{httpd.server_port}", "/health")
    thread.join(timeout=2)
    assert status == 200
    assert payload == {"status": "ok"}


def test_run_code_does_not_use_substring_blocklist():
    from skills.run_code.skill import run

    result = run("print(__import__('math').sqrt(9))")
    assert result["success"]
    assert result["stdout"].strip() == "3.0"
