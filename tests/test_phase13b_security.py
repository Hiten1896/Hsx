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


def test_local_api_requires_bearer_token_when_origin_is_missing(monkeypatch):
    from tests.test_files_panel import _request

    token = "local-testing-only"
    monkeypatch.setattr(server, "AUTH_TOKEN", token, raising=False)
    status, _, body = _request("/api/skills", tokenless=True)
    assert status == 401
    assert "bearer token" in body.decode().lower()

    status, _, _ = _request(
        "/api/skills",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert status == 200


def test_local_api_rejects_an_invalid_bearer_token(monkeypatch):
    from tests.test_files_panel import _request

    monkeypatch.setattr(server, "AUTH_TOKEN", "local-testing-only", raising=False)
    status, _, _ = _request(
        "/api/skills",
        headers={"Authorization": "Bearer incorrect-testing-token"},
    )
    assert status == 401


def test_same_origin_fetch_can_use_the_ui_without_a_bearer_token(monkeypatch):
    from tests.test_files_panel import _request

    monkeypatch.setattr(server, "ALLOWED_ORIGINS", ("http://127.0.0.1:8000",))
    status, _, _ = _request(
        "/api/skills",
        headers={"Sec-Fetch-Site": "same-origin", "Host": "127.0.0.1:8000"},
        tokenless=True,
    )
    assert status == 200

    status, _, _ = _request(
        "/api/skills",
        headers={"Sec-Fetch-Site": "same-origin", "Host": "attacker.invalid"},
        tokenless=True,
    )
    assert status == 401


def test_api_accepts_only_an_exact_configured_origin(monkeypatch):
    from tests.test_files_panel import _request

    monkeypatch.setattr(server, "ALLOWED_ORIGINS", ("https://hsx.example",))
    status, _, _ = _request(
        "/api/skills",
        headers={"Origin": "https://hsx.example"},
        tokenless=True,
    )
    assert status == 200

    status, headers, body = _request(
        "/api/skills",
        headers={"Origin": "https://hsx.example.attacker.invalid"},
        tokenless=True,
    )
    assert status == 403
    assert "origin" in body.decode().lower()
    assert "Access-Control-Allow-Origin" not in headers


def test_api_rejects_opaque_origin_and_preflight_allows_authorization(monkeypatch):
    from tests.test_files_panel import _request

    monkeypatch.setattr(server, "ALLOWED_ORIGINS", ("https://hsx.example",))
    status, _, _ = _request(
        "/api/skills",
        headers={"Origin": "null"},
        tokenless=True,
    )
    assert status == 403

    status, headers, _ = _request(
        "/api/skills",
        method="OPTIONS",
        headers={
            "Origin": "https://hsx.example",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
        tokenless=True,
    )
    assert status == 204
    assert headers["Access-Control-Allow-Origin"] == "https://hsx.example"
    assert "authorization" in headers["Access-Control-Allow-Headers"].lower()

    status, _, _ = _request(
        "/api/skills",
        method="OPTIONS",
        headers={
            "Origin": "https://hsx.example.attacker.invalid",
            "Access-Control-Request-Method": "GET",
        },
        tokenless=True,
    )
    assert status == 403


def test_auth_token_is_generated_persisted_and_validated(tmp_path):
    from agent.auth import load_or_create_auth_token

    token_file = tmp_path / "auth.token"
    generated = load_or_create_auth_token(token_file)
    assert len(generated) >= 43
    assert token_file.read_text(encoding="utf-8").strip() == generated
    assert load_or_create_auth_token(token_file) == generated
    configured = "configured-test-token-value-is-long-enough"
    assert load_or_create_auth_token(tmp_path / "unused.token", configured) == configured
    with pytest.raises(ValueError, match="at least 32 characters"):
        load_or_create_auth_token(tmp_path / "short.token", "too-short")
    (tmp_path / "corrupt.token").write_text("short", encoding="ascii")
    with pytest.raises(ValueError, match="at least 32 characters"):
        load_or_create_auth_token(tmp_path / "corrupt.token")


@pytest.mark.skipif(__import__("os").name == "nt", reason="POSIX file modes are unavailable on Windows")
def test_auth_token_file_permissions_and_symlinks_are_hardened(tmp_path):
    import secrets
    import stat

    from agent.auth import load_or_create_auth_token

    token_file = tmp_path / "auth.token"
    token_file.write_text(secrets.token_urlsafe(32), encoding="ascii")
    token_file.chmod(0o644)
    load_or_create_auth_token(token_file)
    assert stat.S_IMODE(token_file.stat().st_mode) == 0o600

    symlink = tmp_path / "linked.token"
    symlink.symlink_to(token_file)
    with pytest.raises(ValueError, match="symbolic link"):
        load_or_create_auth_token(symlink)


def test_http_access_logs_never_include_query_tokens(caplog):
    import logging

    handler = object.__new__(server.HsxHandler)
    handler.client_address = ("127.0.0.1", 8000)
    query_marker = "private-query-marker"
    with caplog.at_level(logging.INFO, logger="hsx.server"):
        handler.log_message(
            '"%s" %s %s',
            f"GET /api/skills?token={query_marker} HTTP/1.1",
            "200",
            "1",
        )
    assert query_marker not in caplog.text


def test_server_reports_port_conflict_without_traceback(monkeypatch, capsys):
    import errno

    def address_in_use(*_args):
        raise OSError(errno.EADDRINUSE, "address already in use")

    monkeypatch.setattr(server, "HOST", "127.0.0.1")
    monkeypatch.setattr(server, "PORT", 8123)
    monkeypatch.setattr(server, "ThreadingHTTPServer", address_in_use)

    with pytest.raises(SystemExit) as raised:
        server.create_http_server()

    assert raised.value.code == 2
    assert "port 8123 is already in use" in capsys.readouterr().err


def test_phase6_deployment_health_and_rotation_configuration():
    root = Path(server.ROOT)
    service = (root / "deploy" / "hsx.service").read_text(encoding="utf-8")
    rotation = (root / "deploy" / "hsx.logrotate").read_text(encoding="utf-8")
    check_script = (root / "scripts" / "check.sh").read_text(encoding="utf-8")
    service_worker = (root / "web" / "service-worker.js").read_text(encoding="utf-8")

    assert "Restart=on-failure" in service
    assert "MemoryMax=" in service
    assert "copytruncate" in rotation
    assert "health endpoint" in check_script and "/healthz" in check_script
    assert "hsx-shell-v16" in service_worker


def test_notification_sse_resumes_from_last_event_id_and_sends_heartbeat(monkeypatch):
    from tests.test_files_panel import _request

    class NotifierStub:
        def __init__(self, with_notification):
            self.with_notification = with_notification
            self.calls = []

        def wait_for_notifications(self, last_id, timeout):
            self.calls.append((last_id, timeout))
            if self.with_notification and len(self.calls) == 1:
                return [{"id": 6, "event": "job_done", "title": "Finished"}]
            if not self.with_notification and len(self.calls) == 1:
                return []
            raise BrokenPipeError

    notifier = NotifierStub(with_notification=True)
    monkeypatch.setattr(server, "NOTIFIER", notifier)
    status, _, body = _request(
        "/api/notifications/stream",
        headers={"Last-Event-ID": "5"},
        stop_event="notification",
    )
    assert status == 200
    assert b"id: 6\n" in body
    assert b"id: 5\n" not in body
    assert notifier.calls[0][0] == 5

    monkeypatch.setattr(server, "NOTIFIER", NotifierStub(with_notification=False))
    status, _, body = _request("/api/notifications/stream")
    assert status == 200
    assert b": keepalive\n\n" in body


def test_sensitive_request_skips_panel(monkeypatch):
    class Router:
        def panel(self, providers):
            raise AssertionError("sensitive requests must not create a panel")

        def generate(self, request):
            return iter(())

    monkeypatch.setattr("agent.llm_client.default_router", lambda: Router())
    assert call_llm([], [], "<hsx-untrusted-memory>private</hsx-untrusted-memory>", sensitive=True) is not None
