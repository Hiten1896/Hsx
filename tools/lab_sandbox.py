import os
import subprocess
from pathlib import Path

LAB_ROOT = Path.home() / "hsx" / "lab_repos"
LAB_ROOT.mkdir(parents=True, exist_ok=True)

class LabSandbox:
    @staticmethod
    def run_isolated(command: list[str], workdir: Path | None = None, timeout: int = 120) -> dict:
        """Runs untrusted tools safely inside an ephemeral Docker container on the HP server."""
        try:
            container_cmd = [
                "docker", "run", "--rm",
                "--network", "none",
                "--memory=1g",
                "--cpus=1.0",
                "-v", f"{LAB_ROOT}:/workspace",
                "-w", f"/workspace/{workdir.name}" if workdir else "/workspace",
                "python:3.11-slim",
                "bash", "-c", " && ".join(command)
            ]
            result = subprocess.run(
                container_cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False
            )
            return {
                "success": result.returncode == 0,
                "stdout": result.stdout[:10000],
                "stderr": result.stderr[:5000],
                "exit_code": result.returncode
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "Execution timed out in sandbox."}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    path_root = LAB_ROOT