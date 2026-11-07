import shutil
import subprocess
import os
import time
from pathlib import Path

from agent.workspace import Workspace
from config import WORKSPACE_DIR, WORK_DIR


WORKSPACE = Workspace(WORKSPACE_DIR, WORK_DIR)


def host_audio_backend() -> tuple[str, str | None]:
    pactl = shutil.which("pactl")
    if pactl:
        pulse = subprocess.run(
            [pactl, "list", "short", "sinks"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if pulse.returncode == 0 and any(line.strip() for line in pulse.stdout.splitlines()):
            return "pulse", os.getenv("HSX_PULSE_AUDIO_DEVICE") or None

    aplay = shutil.which("aplay")
    if not aplay:
        raise RuntimeError(
            "No host audio route is available: install alsa-utils (aplay) or "
            "pulseaudio-utils (pactl) and configure an output sink."
        )
    alsa = subprocess.run(
        [aplay, "-l"],
        capture_output=True,
        text=True,
        timeout=3,
        check=False,
    )
    if alsa.returncode != 0:
        detail = (alsa.stderr or alsa.stdout).strip()
        raise RuntimeError(
            "ALSA output could not be checked."
            + (f" {detail[:300]}" if detail else "")
        )
    if not any(line.lstrip().startswith("card ") for line in alsa.stdout.splitlines()):
        raise RuntimeError(
            "No ALSA sound cards are available, and no active PulseAudio sink was found."
        )
    return "alsa", os.getenv("HSX_AUDIO_DEVICE", "alsa/default")


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
        raise RuntimeError(
            "Local playback is unavailable because mpv is not installed. "
            "Install the reviewed host audio packages: mpv, alsa-utils, and pulseaudio-utils."
        )
    backend, device = host_audio_backend()
    command = [
        player,
        f"--ao={backend}",
        "--no-video",
        "--force-window=no",
        "--really-quiet",
        "--",
        source,
    ]
    if device:
        audio_device = f"pulse/{device}" if backend == "pulse" else device
        command.insert(2, f"--audio-device={audio_device}")
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    time.sleep(0.25)
    if process.poll() is not None:
        _stdout, stderr = process.communicate(timeout=1)
        detail = (stderr or "").replace(source, "[audio source]").strip()
        raise RuntimeError(
            "mpv exited before playback could start."
            + (f" {detail[:500]}" if detail else "")
        )
    return {
        "success": True,
        "target_device": "local_host",
        "pid": process.pid,
        "backend": backend,
        "message": f"mpv started on the {backend} output route.",
    }
