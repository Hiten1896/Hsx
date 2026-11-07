import importlib
import subprocess
import sys

from agent.package_catalog import PACKAGE_CATALOG
from agent.policy import POLICY

IMPORT_NAMES = {
    "yt-dlp": "yt_dlp", "pillow": "PIL", "pdfplumber": "pdfplumber",
    "reportlab": "reportlab", "openpyxl": "openpyxl", "pillow-heif": "pillow_heif",
}


def run(package: str, capability: str) -> dict:
    recipe = PACKAGE_CATALOG.get(package)
    if not recipe:
        raise ValueError("That package is not in Hsx's reviewed recipe catalog.")
    requirement = f"{package}=={recipe['version']}"
    if sys.prefix == sys.base_prefix:
        POLICY.audit_event("package_install", "capability-resolver", package, "blocked_no_venv")
        return {
            "success": False,
            "error": "Hsx must be restarted from its project virtual environment before installing packages.",
        }
    POLICY.audit_event("package_install", "capability-resolver", package, "started")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-input", requirement],
            capture_output=True, text=True, timeout=600, check=False,
        )
        if result.returncode:
            raise RuntimeError((result.stderr or result.stdout or "Package installation failed.")[-1200:])
        module = importlib.import_module(IMPORT_NAMES[package])
        if package == "yt-dlp":
            module.version.__version__
    except (AttributeError, OSError, ImportError, RuntimeError, subprocess.SubprocessError) as error:
        POLICY.audit_event("package_install", "capability-resolver", package, "failed")
        return {"success": False, "error": f"I couldn't install {requirement} into the Hsx environment: {error}"}
    POLICY.audit_event("package_install", "capability-resolver", package, "self_test_passed")
    return {
        "success": True,
        "package": requirement,
        "capability": capability,
        "message": f"Installed and self-tested {requirement} in the Hsx project environment. Continuing {capability} now.",
    }
