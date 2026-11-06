import pytest

from agent.sandbox import SandboxError, execute_python, run_command


def test_sandbox_validates_limits_and_argv():
    with pytest.raises(ValueError, match="timeout_seconds"):
        execute_python("print(1)", timeout_seconds=0)
    with pytest.raises(ValueError, match="memory_bytes"):
        execute_python("print(1)", memory_bytes=1024)
    with pytest.raises(ValueError, match="argument"):
        run_command(["python", ""], working_directory=__import__("pathlib").Path.cwd())


def test_sandbox_fails_closed_when_network_isolation_is_unavailable(monkeypatch):
    monkeypatch.setattr("agent.sandbox.executor.linux_network_sandbox_available", lambda: False)
    with pytest.raises(SandboxError, match="network"):
        execute_python("print('blocked')")


def test_sandbox_uses_network_namespace_and_returns_bounded_output(tmp_path, monkeypatch):
    calls = []

    class FakeProcess:
        def __init__(self, command, **kwargs):
            calls.append(command)
            kwargs["stdout"].write(b"safe output\n")
            kwargs["stderr"].write(b"")

        def wait(self, timeout):
            return 0

    monkeypatch.setattr("agent.sandbox.executor.linux_network_sandbox_available", lambda: True)
    monkeypatch.setattr("agent.sandbox.executor.shutil.which", lambda _: "unshare")
    monkeypatch.setattr("agent.sandbox.executor.subprocess.Popen", FakeProcess)
    monkeypatch.setattr("agent.sandbox.executor.SCRATCH_DIR", tmp_path)

    result = execute_python("print('safe output')")

    assert result.returncode == 0
    assert result.stdout == "safe output\n"
    assert calls[0][:3] == ["unshare", "--net", "--"]
    assert not list(tmp_path.iterdir())
