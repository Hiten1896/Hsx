"""Compatibility wrapper for the shared camera owner.

The older RTSP publisher opened the webcam in a second FFmpeg process. This
wrapper now leases CameraService and never starts another device reader.
"""

from __future__ import annotations

import os

from agent.hardware.camera import CAMERA


class CameraStream:
    """Legacy stream handle that delegates device ownership to CameraService."""

    def __init__(self, *, device: str | int | None = None, rtsp_url: str | None = None, **_options):
        raw_device = device if device is not None else os.getenv("HSX_CAMERA_DEVICE", "0")
        
        # Normalize device to match CameraService representation
        if isinstance(raw_device, int) or (isinstance(raw_device, str) and raw_device.isdigit()):
            selected = int(raw_device)
        else:
            selected = str(raw_device)

        if selected != CAMERA.device:
            raise ValueError("CameraService owns the configured device; set HSX_CAMERA_DEVICE to change it.")
        if rtsp_url:
            raise ValueError("Direct RTSP camera publishing was removed; consume CameraService frames instead.")
        self.device = selected

    @staticmethod
    def enabled() -> bool:
        return os.getenv("HSX_ENABLE_CAMERA_STREAM", "").strip().casefold() in {"1", "true", "yes", "on"}

    def command(self, ffmpeg: str) -> list[str]:
        return CAMERA.command(ffmpeg)

    def health_check(self) -> dict:
        return CAMERA.health_check()

    def start(self) -> bool:
        return CAMERA.acquire("legacy-stream")

    def stop(self, timeout: float = 5.0) -> None:
        CAMERA.release("legacy-stream", timeout=timeout)


CAMERA_STREAM = CameraStream()