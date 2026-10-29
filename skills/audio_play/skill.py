import shutil
import subprocess
import os
from pathlib import Path

from agent.workspace import Workspace
from config import WORKSPACE_DIR, WORK_DIR


WORKSPACE = Workspace(WORKSPACE_DIR, WORK_DIR)


def run(source: str, target_device: str = "local_host") -> dict:
    source = source.strip()
    if not source:
        raise ValueError("An audio file path or media URL is required.")
    if target_device == "client_browser":
        if source.startswith(("https://", "http://")):
            return {
                "success": True,
                "browser_action": {"type": "audio_play", "source_url": source},
                "message": "Audio playback is ready for confirmation in the requesting browser.",
            }
        source_path = Path(source)
        if source_path.is_absolute():
            resolved = source_path.resolve()
            if resolved != WORKSPACE.work_dir and WORKSPACE.work_dir not in resolved.parents:
                raise ValueError("Browser playback files must be inside the Hsx work directory.")
            resolved_path = resolved
        else:
            resolved_path = WORKSPACE.resolve_write(source_path)
        if not resolved_path.is_file():
            raise FileNotFoundError(f"Audio file not found: {source}")
        return {
            "success": True,
            "browser_action": {
                "type": "audio_play",
                "source_path": resolved_path.relative_to(WORKSPACE.work_dir).as_posix(),
            },
            "message": "Audio playback is ready for confirmation in the requesting browser.",
        }
    if target_device != "local_host":
        raise ValueError("target_device must be local_host or client_browser.")
    player = shutil.which("mpv")
    if not player:
        raise RuntimeError("mpv is required for local playback. Install it with: sudo apt install mpv yt-dlp")
    process = subprocess.Popen(
        [
            player,
            "--ao=alsa",
            f"--audio-device={os.getenv('HSX_AUDIO_DEVICE', 'alsa/default')}",
            "--no-video",
            "--force-window=no",
            "--really-quiet",
            "--",
            source,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return {"success": True, "target_device": "local_host", "pid": process.pid, "message": "Audio playback started."}
