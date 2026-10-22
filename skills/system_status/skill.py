import shutil

from config import BASE_DIR


def run() -> dict:
    usage = shutil.disk_usage(BASE_DIR)
    return {
        "success": True,
        "disk": {"total": usage.total, "used": usage.used, "free": usage.free},
    }
