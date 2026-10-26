"""Allowlisted action executors."""

from __future__ import annotations

from typing import Any

from .interfaces import SystemInterface


class Executor:
    def __init__(self, system: SystemInterface):
        self.system = system

    def execute(self, action: str, args: dict[str, Any]) -> str:
        if action == "system_stats":
            return "ok:" + str(self.system.stats())
        if action in {"network_status", "tailscale_status"}:
            executable = "tailscale" if action == "tailscale_status" else "ip"
            arguments = ["status"] if action == "tailscale_status" else ["-brief", "link"]
            return self._run(executable, arguments)
        if action == "journal_read":
            return self._run("journalctl", ["--no-pager", "-u", args["unit"], "-n", "100"])
        if action == "service":
            return self._run("systemctl", [args["operation"], args["unit"]])
        if action in {"apt_install", "apt_remove"}:
            operation = "install" if action == "apt_install" else "remove"
            return self._run("apt-get", ["--assume-yes", operation, args["package"]])
        if action == "pip_install":
            return self._run("/opt/hsx/.venv/bin/python", ["-m", "pip", "install", args["package"]])
        if action == "disk_cleanup":
            return self._run("/usr/local/sbin/hsx-disk-cleanup", [args["target"]])
        if action == "schedule_timer":
            return self._run(
                "systemctl",
                [args["operation"], f"{args['name']}.timer"],
            )
        if action == "reboot":
            return self._run("systemctl", ["reboot"])
        if action == "wifi_reconnect":
            return self._run("nmcli", ["radio", "wifi", "on"])
        raise ValueError("Action has no executor.")

    def _run(self, executable: str, arguments: list[str]) -> str:
        code, stdout, stderr = self.system.run(executable, arguments)
        if code:
            raise RuntimeError(stderr.strip() or f"{executable} failed with status {code}")
        return stdout.strip() or "ok"
