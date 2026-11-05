"""Opt-in wrappers for lightweight Linux hardware services."""

from agent.hardware.audio_alerts import AUDIO_ALERTS, AudioAlerts
from agent.hardware.camera_stream import CAMERA_STREAM, CameraStream
from agent.hardware.motion_monitor import MOTION_MONITOR, MotionMonitor
from agent.hardware.speaker import SPEAKER, Speaker

__all__ = [
    "AUDIO_ALERTS",
    "CAMERA_STREAM",
    "MOTION_MONITOR",
    "SPEAKER",
    "AudioAlerts",
    "CameraStream",
    "MotionMonitor",
    "Speaker",
]
