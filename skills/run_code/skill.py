import subprocess
import sys
import uuid
from pathlib import Path

from config import SCRATCH_DIR


BLOCKED = ("os.system", "subprocess.", "shutil.rmtree", "rm -rf", "curl ", "wget ", "requests.")


def run(code: str) -> dict:
    if any(pattern in code.lower() for pattern in BLOCKED):
        return {"success": False, "error": "This code was blocked by the local safety guard."}
    script = SCRATCH_DIR / f"run_{uuid.uuid4().hex}.py"
    try:
        script.write_text(code, encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(script)], capture_output=True, text=True, timeout=10, cwd=SCRATCH_DIR
        )
        return {"success": result.returncode == 0, "stdout": result.stdout, "stderr": result.stderr, "returncode": result.returncode}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Code execution timed out after 10 seconds."}
    finally:
        script.unlink(missing_ok=True)