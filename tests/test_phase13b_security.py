import threading
import urllib.error
import urllib.request
import shutil
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import server
from agent.approval import ApprovalGuard
from agent.llm_client import call_llm
from skills.run_code.skill import run


def test_wrong_pin_locks_out(tmp_path: Path):
    guard = ApprovalGuard(tmp_path / "owner.pin", "correct", max_attempts=2, lockout_seconds=60)
    assert not guard.verify("wrong")
    assert not guard.verify("wrong")
    assert not guard.verify("correct")
    assert guard.remaining_lockout() > 0


def test_run_code_cannot_call_approval_endpoint(monkeypatch):
    monkeypatch.setattr("skills.run_code.skill.SANDBOX", "subprocess")
    result = run(
        "import urllib.request\n"
        "urllib.request.urlopen('http://127.0.0.1:8000/api/improvements/x/approve', timeout=1)"
    )
    assert not result["success"]
    assert "network disabled" in result["stderr"]


def test_run_code_subprocess_is_deterministic(monkeypatch):
    monkeypatch.setattr("skills.run_code.skill.SANDBOX", "subprocess")
    result = run("print(1 + 1)")
    assert result["success"]
    assert result["stdout"].strip() == "2"
    assert result["sandbox"] == "subprocess"


@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker is unavailable")
def test_run_code_docker():
    result = run("print(1 + 1)")
    assert result["success"]
    assert result["stdout"].strip() == "2"
    assert result["sandbox"] == "docker"


def test_api_rejects_missing_token():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.HsxHandler)
    thread = threading.Thread(target=httpd.handle_request)
    thread.start()
    request = urllib.request.Request(f"http://127.0.0.1:{httpd.server_port}/api/skills")
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request)
    thread.join(timeout=2)
    assert error.value.code == 401


def test_sensitive_request_skips_panel(monkeypatch):
    class Router:
        def panel(self, providers):
            raise AssertionError("sensitive requests must not create a panel")

        def generate(self, request):
            return iter(())

    monkeypatch.setattr("agent.llm_client.default_router", lambda: Router())
    assert call_llm([], [], "<hsx-untrusted-memory>private</hsx-untrusted-memory>", sensitive=True) is not None
