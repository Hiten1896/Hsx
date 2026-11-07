from types import SimpleNamespace

import pytest

from skills.capability_resolver import skill as resolver


def test_resolver_rejects_unreviewed_packages_without_running_installer(monkeypatch):
    calls = []
    monkeypatch.setattr(resolver.subprocess, "run", lambda *args, **kwargs: calls.append(args))

    with pytest.raises(ValueError, match="not in Hsx's reviewed recipe catalog"):
        resolver.run("unreviewed-package", "install a missing utility")

    assert calls == []


def test_resolver_refuses_to_install_outside_a_virtual_environment(monkeypatch):
    calls = []
    events = []
    monkeypatch.setattr(resolver.sys, "prefix", "C:\\Python312")
    monkeypatch.setattr(resolver.sys, "base_prefix", "C:\\Python312")
    monkeypatch.setattr(resolver.subprocess, "run", lambda *args, **kwargs: calls.append(args))
    monkeypatch.setattr(
        resolver.POLICY,
        "audit_event",
        lambda *args: events.append(args),
    )

    result = resolver.run("yt-dlp", "play authorized audio")

    assert result["success"] is False
    assert "project virtual environment" in result["error"]
    assert not calls
    assert events[-1][-1] == "blocked_no_venv"


def test_resolver_installs_pinned_package_into_active_venv_and_self_tests(monkeypatch):
    calls = []
    imports = []
    events = []
    monkeypatch.setattr(resolver.sys, "prefix", "C:\\Hsx\\.venv")
    monkeypatch.setattr(resolver.sys, "base_prefix", "C:\\Python312")
    monkeypatch.setattr(resolver.sys, "executable", "C:\\Hsx\\.venv\\Scripts\\python.exe")

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def fake_import(name):
        imports.append(name)
        return SimpleNamespace(version=SimpleNamespace(__version__="2026.10.26"))

    monkeypatch.setattr(resolver.subprocess, "run", fake_run)
    monkeypatch.setattr(resolver.importlib, "import_module", fake_import)
    monkeypatch.setattr(
        resolver.POLICY,
        "audit_event",
        lambda *args: events.append(args),
    )

    result = resolver.run("yt-dlp", "play authorized audio")

    assert calls[0][0][:4] == [
        "C:\\Hsx\\.venv\\Scripts\\python.exe", "-m", "pip", "install",
    ]
    assert "yt-dlp==2026.10.26" in calls[0][0]
    assert imports == ["yt_dlp"]
    assert result["success"] is True
    assert result["capability"] == "play authorized audio"
    assert [event[-1] for event in events] == ["started", "self_test_passed"]
