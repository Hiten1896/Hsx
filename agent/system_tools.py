from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
from datetime import datetime
from pathlib import Path

import psutil

from config import WORKSPACE_DIR, WORK_DIR


def _cpu_model() -> str:
    if os.name != "nt":
        cpuinfo = Path("/proc/cpuinfo")
        if cpuinfo.is_file():
            for line in cpuinfo.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.casefold().startswith(("model name", "hardware")) and ":" in line:
                    return line.split(":", 1)[1].strip()
    return platform.processor() or platform.machine() or "Unknown"


def system_info() -> dict:
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage(os.path.abspath(os.sep))
    now = datetime.now().astimezone()
    return {
        "hostname": socket.gethostname(),
        "cpu": {
            "model": _cpu_model(),
            "logical_cores": psutil.cpu_count(logical=True) or os.cpu_count() or 1,
            "physical_cores": psutil.cpu_count(logical=False),
        },
        "memory": {"total_bytes": memory.total, "available_bytes": memory.available},
        "disk": {"path": os.path.abspath(os.sep), "total_bytes": disk.total, "free_bytes": disk.free},
        "os": {"system": platform.system(), "release": platform.release(), "version": platform.version()},
        "uptime_seconds": max(0, int(datetime.now().timestamp() - psutil.boot_time())),
        "local_time": now.isoformat(timespec="seconds"),
    }


def disk_usage() -> dict:
    path = Path(os.path.abspath(os.sep))
    usage = shutil.disk_usage(path)
    return {
        "path": str(path),
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
    }


def network_status() -> dict:
    stats = psutil.net_if_stats()
    interfaces = {}
    for name, addresses in psutil.net_if_addrs().items():
        interfaces[name] = {
            "is_up": bool(stats.get(name) and stats[name].isup),
            "addresses": [
                {"family": str(address.family), "address": address.address}
                for address in addresses
                if address.address
            ],
        }
    return {"hostname": socket.gethostname(), "interfaces": interfaces}


def list_usb() -> dict:
    devices = []
    usb_root = Path("/sys/bus/usb/devices")
    if usb_root.is_dir():
        for item in sorted(usb_root.iterdir()):
            vendor = item / "idVendor"
            product = item / "idProduct"
            if vendor.is_file() and product.is_file():
                devices.append({
                    "name": item.name,
                    "vendor_id": vendor.read_text(encoding="ascii").strip(),
                    "product_id": product.read_text(encoding="ascii").strip(),
                    "manufacturer": (item / "manufacturer").read_text(encoding="utf-8", errors="replace").strip()
                    if (item / "manufacturer").is_file() else "",
                    "product": (item / "product").read_text(encoding="utf-8", errors="replace").strip()
                    if (item / "product").is_file() else "",
                })
        return {"source": "local sysfs", "devices": devices}
    if shutil.which("lsusb"):
        completed = subprocess.run(
            ["lsusb"], capture_output=True, text=True, timeout=10, check=False
        )
        return {
            "source": "local lsusb",
            "devices": [line for line in completed.stdout.splitlines() if line.strip()],
            "success": completed.returncode == 0,
        }
    return {"source": "local host", "devices": [], "available": False}


def _inside_workspace(target: Path) -> bool:
    try:
        resolved = target.resolve(strict=False)
        roots = (WORKSPACE_DIR.resolve(), WORK_DIR.resolve())
        return any(resolved == root or root in resolved.parents for root in roots)
    except (OSError, RuntimeError):
        return False


def create_text_file(path: str, content: str) -> dict:
    if not path or not isinstance(content, str):
        raise ValueError("A workspace path and text content are required.")
    requested = Path(path).expanduser()
    target = requested if requested.is_absolute() else WORKSPACE_DIR / requested
    parent = target.parent
    if not _inside_workspace(target):
        raise ValueError("The destination must remain inside the workspace.")
    parent.mkdir(parents=True, exist_ok=True)
    if not _inside_workspace(target):
        raise ValueError("The destination must remain inside the workspace.")
    if target.exists() and target.is_dir():
        raise ValueError("The destination must be a file path.")

    stem, suffix = target.stem, target.suffix
    candidate = target
    sequence = 1
    while True:
        if not _inside_workspace(candidate):
            raise ValueError("The destination must remain inside the workspace.")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(candidate, flags, 0o600)
        except FileExistsError:
            candidate = target.with_name(f"{stem} ({sequence}){suffix}")
            sequence += 1
            continue
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as output:
            output.write(content)
        return {"success": True, "path": str(candidate), "bytes_written": len(content.encode("utf-8"))}
