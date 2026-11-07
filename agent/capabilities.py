from __future__ import annotations

import importlib.util
import json
import logging
import os
import shutil
import subprocess

from agent.skill_loader import runtime_tool_registry
from config import (
    ANTHROPIC_API_KEY,
    BASE_DIR,
    GEMINI_API_KEY,
    GROK_API_KEY,
    GROQ_API_KEY,
    OPENAI_API_KEY,
    OPENROUTER_API_KEY,
    WORK_DIR,
    WORKSPACE_DIR,
)

_GAPS_PATH = BASE_DIR / "config" / "capability_gaps.json"
LOGGER = logging.getLogger("hsx.capabilities")


def _item(name: str, status: str, detail: str) -> dict[str, str]:
    return {"name": name, "status": status, "detail": detail}


def _provider_health() -> list[dict[str, str]]:
    from agent.llm.router import default_router
    from agent.llm.types import LLMRequest

    try:
        providers = default_router().providers
    except (OSError, RuntimeError, TypeError, ValueError):
        return [_item(
            "Provider health",
            "needs setup",
            "No provider is available; configure a provider key or local model.",
        )]
    request = LLMRequest(
        messages=[{"role": "user", "content": "Reply with OK."}],
        system="Reply with OK.",
    )
    results = []
    for provider in providers:
        model = str(getattr(provider, "model", "unknown"))
        try:
            events = list(provider.generate(request))
            healthy = any(
                event.type == "text_delta" and bool(event.text)
                for event in events
            )
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            healthy = False
            LOGGER.warning(
                "No-tool health request failed for %s (%s).",
                provider.name,
                type(error).__name__,
            )
        results.append(_item(
            f"{provider.name} ({model})",
            "works now" if healthy else "needs setup",
            "Answered a no-tool health request." if healthy
            else "Check this provider's key, model, quota, and network connection.",
        ))
    return results


def _camera_item() -> dict[str, str]:
    from agent.hardware import CAMERA
    from agent.tools.vision_capture import enabled

    health = CAMERA.health_check()
    if health.get("running"):
        return _item("Camera snapshots", "works now", "The shared CameraService is running.")
    if not enabled():
        return _item(
            "Camera snapshots",
            "needs setup",
            "Enable HSX_ENABLE_VISION_CAPTURE and turn on the camera in the web UI or approve a chat start.",
        )
    if not health.get("device_available"):
        return _item(
            "Camera snapshots",
            "needs setup",
            f"Camera device {CAMERA.device} is unavailable; connect it or set HSX_CAMERA_DEVICE.",
        )
    if not shutil.which("ffmpeg"):
        return _item("Camera snapshots", "needs setup", "Install ffmpeg on the Hsx host.")
    return _item(
        "Camera snapshots",
        "needs setup",
        "Turn on the camera in the web UI for this session or approve a chat start.",
    )


def _audio_items() -> list[dict[str, str]]:
    items = []
    if "audio_play" in {tool["name"] for tool in runtime_tool_registry()}:
        items.append(_item(
            "Browser audio playback",
            "works now",
            "The chat can play user-selected audio after a browser gesture.",
        ))
    try:
        player = shutil.which("mpv")
        if not player:
            raise RuntimeError("mpv is not installed.")
        from skills.audio_play.skill import host_audio_backend

        backend, _device = host_audio_backend()
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        items.append(_item("Host audio output", "needs setup", str(error)))
    else:
        items.append(_item(
            "Host audio output",
            "works now",
            f"mpv and a {backend} output route are available.",
        ))
    if importlib.util.find_spec("sounddevice") is None:
        items.append(_item(
            "Host microphone",
            "needs setup",
            "Install the optional voice dependencies and connect a microphone; this check does not capture audio.",
        ))
        return items
    import sounddevice

    try:
        input_device = sounddevice.query_devices(kind="input")
    except sounddevice.PortAudioError:
        input_device = None
    try:
        output_device = sounddevice.query_devices(kind="output")
    except sounddevice.PortAudioError:
        output_device = None
    if output_device and output_device.get("max_output_channels", 0) > 0:
        items.append(_item(
            "Host audio output",
            "works now",
            "An output device is present for local audio playback.",
        ))
    else:
        items.append(_item(
            "Host audio output",
            "needs setup",
            "Connect or select an output device for local audio playback.",
        ))
    if input_device and input_device.get("max_input_channels", 0) > 0:
        items.append(_item(
            "Host microphone",
            "works now",
            "An input device is present; grant microphone permission before starting capture.",
        ))
    else:
        items.append(_item(
            "Host microphone",
            "needs setup",
            "Connect or select an input device, then grant microphone permission before capture.",
        ))
    return items


def get_capabilities() -> dict:
    tools = runtime_tool_registry()
    names = {tool["name"] for tool in tools}
    camera = _camera_item()
    search_configured = "web_search" in names and bool(os.getenv("HSX_SEARCH_URL", "").strip())
    groups: list[dict] = [{
        "name": "Tools",
        "items": [
            _item(
                tool["name"],
                "needs setup" if tool["name"] == "web_search" and not search_configured
                else camera["status"] if tool["name"] in {"camera_record", "vision_capture"}
                else "works now",
                "Set HSX_SEARCH_URL to the configured search service."
                if tool["name"] == "web_search" and not search_configured
                else camera["detail"] if tool["name"] in {"camera_record", "vision_capture"}
                else str(tool.get("description", "Registered in the live tool registry.")),
            )
            for tool in tools
        ],
    }]

    configured_keys = [
        ("Anthropic", ANTHROPIC_API_KEY),
        ("Gemini", GEMINI_API_KEY),
        ("Groq", GROQ_API_KEY),
        ("Grok", GROK_API_KEY),
        ("OpenAI", OPENAI_API_KEY),
        ("OpenRouter", OPENROUTER_API_KEY),
    ]
    key_names = [name for name, value in configured_keys if value]
    if os.getenv("OLLAMA_MODEL", "").strip():
        key_names.append("Ollama")
    key_item = _item(
        "Provider credentials",
        "works now" if key_names else "needs setup",
        f"Configured providers: {', '.join(key_names)}." if key_names
        else "Add a provider key in the local environment, or configure a local provider.",
    )
    provider_health = _provider_health()
    groups.append({
        "name": "Providers and web",
        "items": [
            key_item,
            *provider_health,
            _item(
                "Web search",
                "works now" if search_configured
                else "needs setup",
                "The web_search tool and HSX_SEARCH_URL are configured; availability is checked with each search."
                if search_configured
                else "Configure HSX_SEARCH_URL and ensure web_search is registered.",
            ),
        ],
    })

    audio = _audio_items()
    groups.append({
        "name": "Camera and audio",
        "items": [
            camera,
            *audio,
            _item(
                "ffmpeg",
                "works now" if shutil.which("ffmpeg") else "needs setup",
                "Found on the Hsx host." if shutil.which("ffmpeg")
                else "Install ffmpeg on the Hsx host.",
            ),
        ],
    })

    try:
        usage = shutil.disk_usage(WORKSPACE_DIR)
    except OSError:
        disk_item = _item("Workspace disk", "needs setup", "The configured workspace disk could not be queried.")
    else:
        disk_item = _item(
            "Workspace disk",
            "works now",
            f"{usage.free / (1024 ** 3):.1f} GB free.",
        )
    writable = WORK_DIR.is_dir() and os.access(WORK_DIR, os.W_OK)
    workspace_item = _item(
        "Workspace writes",
        "works now" if writable else "needs setup",
        f"{WORK_DIR.name} is writable." if writable
        else f"Make {WORK_DIR} available and writable to the Hsx service account.",
    )
    groups.append({"name": "Workspace", "items": [disk_item, workspace_item]})

    gap_data = json.loads(_GAPS_PATH.read_text(encoding="utf-8"))
    gaps = [
        _item(str(gap["name"]), "not possible yet", str(gap["detail"]))
        for gap in gap_data["known_gaps"]
        if not set(gap["requires_tools"]).issubset(names)
    ]
    groups.append({"name": "Known gaps", "items": gaps})
    return {
        "groups": groups,
        "cannot_do": [
            {"name": gap["name"], "how_to_add": gap["detail"]}
            for gap in gaps
        ],
    }


def run() -> dict:
    return {"success": True, **get_capabilities()}
