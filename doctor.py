"""Read-only diagnostics for local Hsx installations."""

from __future__ import annotations

import importlib.util
import os
import platform
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parent
PROVIDER_KEYS = (
    "GEMINI_API_KEY",
    "GROK_API_KEY",
    "XAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GROQ_API_KEY",
    "OPENROUTER_API_KEY",
    "OPENAI_API_KEY",
)
REQUIRED_MODULES = {
    "python-dotenv": "dotenv",
    "PyYAML": "yaml",
    "anthropic": "anthropic",
    "python-docx": "docx",
    "openpyxl": "openpyxl",
    "rapidfuzz": "rapidfuzz",
    "pdfplumber": "pdfplumber",
    "reportlab": "reportlab",
    "python-pptx": "pptx",
    "pywebpush": "pywebpush",
}
OPTIONAL_MODULES = {
    "vosk": "vosk",
    "sounddevice": "sounddevice",
    "webrtcvad": "webrtcvad",
}


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str


def _check(name: str, passed: bool, detail: str, *, warning: bool = False) -> Check:
    return Check(name, "WARN" if warning else ("PASS" if passed else "FAIL"), detail)


def _module_checks() -> list[Check]:
    checks = []
    for package, module in REQUIRED_MODULES.items():
        available = importlib.util.find_spec(module) is not None
        checks.append(_check(f"dependency:{package}", available, "importable" if available else "missing"))
    for package, module in OPTIONAL_MODULES.items():
        available = importlib.util.find_spec(module) is not None
        checks.append(_check(f"optional:{package}", available, "installed" if available else "not installed", warning=True))
    return checks


def _key_checks() -> list[Check]:
    values = dotenv_values(ROOT / ".env")
    checks = []
    for key in PROVIDER_KEYS:
        configured = bool(str(os.getenv(key) or values.get(key) or "").strip())
        checks.append(_check(f"key:{key}", configured, "configured" if configured else "not configured", warning=not configured))
    return checks


def _hardware_checks() -> list[Check]:
    checks = []
    serial_devices = [Path(path) for pattern in ("/dev/ttyACM*", "/dev/ttyUSB*") for path in Path("/").glob(pattern.lstrip("/"))] if os.name != "nt" else []
    if serial_devices:
        usable = [str(path) for path in serial_devices if os.access(path, os.R_OK | os.W_OK)]
        checks.append(_check("serial", bool(usable), f"available: {', '.join(usable) or 'no writable devices'}", warning=not usable))
    else:
        checks.append(_check("serial", False, "no /dev/ttyACM* or /dev/ttyUSB* devices found", warning=True))

    model_path = os.getenv("HSX_VOICE_MODEL_PATH", "models/vosk-model-small-en-us-0.15")
    model = Path(model_path).expanduser()
    if not model.is_absolute():
        model = ROOT / model
    checks.append(_check("vosk-model", model.is_dir(), str(model), warning=not model.is_dir()))

    try:
        import sounddevice as sd

        input_device = sd.query_devices(kind="input")
        checks.append(_check("microphone", True, str(input_device.get("name", "available")), warning=False))
    except Exception as error:
        checks.append(_check("microphone", False, str(error), warning=True))
    return checks


def _service_checks() -> list[Check]:
    checks = []
    try:
        with socket.create_connection(("127.0.0.1", 8000), timeout=1):
            checks.append(_check("port:8000", True, "Hsx endpoint is accepting connections"))
    except OSError as error:
        checks.append(_check("port:8000", False, str(error), warning=True))

    if os.name == "nt" or shutil.which("systemctl") is None:
        checks.append(_check("systemd", False, "systemd is unavailable on this platform", warning=True))
    else:
        for unit in ("hsx.service", "hsx-voice-listener.service"):
            result = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True, timeout=3)
            active = result.returncode == 0 and result.stdout.strip() == "active"
            checks.append(_check(f"systemd:{unit}", active, result.stdout.strip() or result.stderr.strip(), warning=True))

    broker = Path(os.getenv("HSX_BROKER_SOCKET", "/run/hsx/broker.sock"))
    if os.name == "nt":
        checks.append(_check("broker", False, "Unix socket broker is unavailable on Windows", warning=True))
    elif not broker.exists():
        checks.append(_check("broker", False, f"socket not found: {broker}", warning=True))
    else:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(2)
                connection.connect(str(broker))
            checks.append(_check("broker", True, str(broker)))
        except OSError as error:
            checks.append(_check("broker", False, str(error), warning=True))
    return checks


def run_doctor() -> list[Check]:
    checks = [
        _check("python", sys.version_info >= (3, 11), platform.python_version()),
        *_module_checks(),
        *_key_checks(),
        *_hardware_checks(),
        *_service_checks(),
    ]
    return checks


def print_report(checks: list[Check]) -> int:
    print("Hsx doctor")
    for item in checks:
        print(f"{item.status:4} {item.name}: {item.detail}")
    return 1 if any(item.status == "FAIL" for item in checks) else 0


if __name__ == "__main__":
    raise SystemExit(print_report(run_doctor()))
