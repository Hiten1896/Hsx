import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import server
from agent.security import safe_path


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


def test_api_accepts_a_configured_origin_from_remote_address():
    handler = server.HsxHandler.__new__(server.HsxHandler)
    handler.client_address = ("192.168.1.2", 1234)
    handler.command = "GET"
    handler.path = "/api/skills"
    handler.headers = {"Origin": server.ALLOWED_ORIGINS[0]}
    assert handler._api_access_allowed()


def test_health_accepts_requests_without_api_auth():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.HsxHandler)
    thread = threading.Thread(target=httpd.handle_request)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{httpd.server_port}/health") as response:
            assert response.status == 200
            assert json.loads(response.read()) == {"status": "ok"}
    finally:
        thread.join(timeout=2)
        httpd.server_close()


def test_run_code_does_not_use_substring_blocklist(monkeypatch):
    from agent.sandbox import linux_network_sandbox_available

    if not linux_network_sandbox_available():
        pytest.skip("Linux network sandbox is unavailable")
    monkeypatch.setattr("skills.run_code.skill.SANDBOX", "subprocess")
    from skills.run_code.skill import run

    result = run("print(__import__('math').sqrt(9))")
    assert result["success"]
    assert result["stdout"].strip() == "3.0"
