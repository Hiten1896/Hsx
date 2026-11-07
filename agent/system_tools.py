from __future__ import annotations

import os
import json
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
    workspace_disk = psutil.disk_usage(str(WORKSPACE_DIR))
    system_disk = psutil.disk_usage(os.path.abspath(os.sep))
    now = datetime.now().astimezone()
    return {
        "hostname": socket.gethostname(),
        "ips": sorted({item.address for values in psutil.net_if_addrs().values() for item in values
                       if item.family in {socket.AF_INET, socket.AF_INET6} and item.address}),
        "tailscale": _tailscale_status(),
        "cpu": {
            "model": _cpu_model(),
            "logical_cores": psutil.cpu_count(logical=True) or os.cpu_count() or 1,
            "physical_cores": psutil.cpu_count(logical=False),
        },
        "memory": {"total_bytes": memory.total, "free_bytes": memory.available,
                   "available_bytes": memory.available},
        "disk": {
            "path": str(WORKSPACE_DIR),
            "total_bytes": workspace_disk.total,
            "free_bytes": workspace_disk.free,
        },
        "system_disk": {
            "path": os.path.abspath(os.sep),
            "total_bytes": system_disk.total,
            "free_bytes": system_disk.free,
        },
        "os": {"system": platform.system(), "release": platform.release(), "version": platform.version()},
        "uptime_seconds": max(0, int(datetime.now().timestamp() - psutil.boot_time())),
        "local_time": now.isoformat(timespec="seconds"),
    }


def _tailscale_status() -> dict:
    executable = shutil.which("tailscale")
    if not executable:
        return {"available": False, "status": "not installed"}
    try:
        result = subprocess.run([executable, "status", "--json"], capture_output=True, text=True, timeout=5, check=False)
        if result.returncode:
            return {"available": True, "connected": False, "error": result.stderr.strip() or "status command failed"}
        data = json.loads(result.stdout)
        return {"available": True, "connected": data.get("BackendState") == "Running",
                "backend_state": data.get("BackendState", "Unknown"), "self": data.get("Self", {})}
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
        return {"available": True, "connected": False, "error": str(error)}


def list_serial_ports() -> dict:
    try:
        from serial.tools import list_ports
        ports = [{"device": port.device, "description": port.description, "manufacturer": port.manufacturer}
                 for port in list_ports.comports()]
        return {"success": True, "ports": ports}
    except ImportError:
        try:
            if os.name == "nt":
                result = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", "[System.IO.Ports.SerialPort]::GetPortNames()"],
                    capture_output=True, text=True, timeout=5, check=False,
                )
                if result.returncode:
                    return {"success": False, "error": result.stderr.strip() or "Could not enumerate serial ports."}
                ports = sorted(line.strip() for line in result.stdout.splitlines() if line.strip())
            else:
                ports = sorted(
                    str(port) for pattern in ("ttyUSB*", "ttyACM*", "ttyS*") for port in Path("/dev").glob(pattern)
                )
            return {"success": True, "ports": ports, "note": "pyserial unavailable; using device names"}
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
            return {"success": False, "error": str(error)}
    except (OSError, RuntimeError, ValueError) as error:
        return {"success": False, "error": str(error)}


def disk_usage() -> dict:
    path = WORKSPACE_DIR
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
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -like 'USB*' } | "
                 "Select-Object FriendlyName,InstanceId,Status | ConvertTo-Json -Compress"],
                capture_output=True, text=True, timeout=10, check=False,
            )
            if result.returncode:
                return {"success": False, "error": result.stderr.strip() or "Could not enumerate USB devices."}
            devices = json.loads(result.stdout) if result.stdout.strip() else []
            return {"success": True, "source": "local Windows device inventory", "devices": devices}
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
            return {"success": False, "error": str(error)}
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
