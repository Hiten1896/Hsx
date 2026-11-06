import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

import server
from agent.approval import ApprovalGuard
from agent.llm_client import call_llm
from agent.sandbox import linux_network_sandbox_available
from skills.run_code import skill as run_code
from skills.run_code.skill import _docker_available, run


def test_wrong_pin_locks_out(tmp_path: Path):
    guard = ApprovalGuard(tmp_path / "owner.pin", "correct", max_attempts=2, lockout_seconds=60)
    assert not guard.verify("wrong")
    assert not guard.verify("wrong")
    assert not guard.verify("correct")
    assert guard.remaining_lockout() > 0


@pytest.mark.skipif(not linux_network_sandbox_available(), reason="Linux network sandbox is unavailable")
def test_run_code_cannot_call_approval_endpoint(monkeypatch):
    monkeypatch.setattr("skills.run_code.skill.SANDBOX", "subprocess")
    result = run(
        "import urllib.request\n"
        "urllib.request.urlopen('http://127.0.0.1:8000/api/improvements/x/approve', timeout=1)"
    )
    assert not result["success"]
    assert "network" in (result.get("stderr", "") + result.get("error", "")).lower()


@pytest.mark.skipif(not linux_network_sandbox_available(), reason="Linux network sandbox is unavailable")
def test_run_code_subprocess_is_deterministic(monkeypatch):
    monkeypatch.setattr("skills.run_code.skill.SANDBOX", "subprocess")
    result = run("print(1 + 1)")
    assert result["success"]
    assert result["stdout"].strip() == "2"
    assert result["sandbox"] == "subprocess"


@pytest.mark.skipif(not _docker_available(), reason="Docker daemon is unavailable")
def test_run_code_docker():
    result = run("print(1 + 1)")
    assert result["success"], result
    assert result["stdout"].strip() == "2"
    assert result["sandbox"] == "docker"


def test_docker_availability_requires_an_responding_daemon(monkeypatch):
    monkeypatch.setattr(run_code.shutil, "which", lambda _: "docker")
    monkeypatch.setattr(
        run_code.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1),
    )
    assert not _docker_available()


def test_local_api_accepts_requests_without_token():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.HsxHandler)
    thread = threading.Thread(target=httpd.handle_request)
    thread.start()
    request = urllib.request.Request(f"http://127.0.0.1:{httpd.server_port}/api/skills")
    with urllib.request.urlopen(request) as response:
        assert response.status == 200
        assert b'"skills"' in response.read()
    thread.join(timeout=2)


def test_sensitive_request_skips_panel(monkeypatch):
    class Router:
        def panel(self, providers):
            raise AssertionError("sensitive requests must not create a panel")

        def generate(self, request):
            return iter(())

    monkeypatch.setattr("agent.llm_client.default_router", lambda: Router())
    assert call_llm([], [], "<hsx-untrusted-memory>private</hsx-untrusted-memory>", sensitive=True) is not None
