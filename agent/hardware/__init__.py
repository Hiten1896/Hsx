"""Low-overhead hardware services with explicit opt-ins where appropriate."""

from agent.hardware.audio_alerts import AUDIO_ALERTS, AudioAlerts
from agent.hardware.camera import CAMERA, CameraService
from agent.hardware.camera_stream import CAMERA_STREAM, CameraStream
from agent.hardware.motion_monitor import MOTION_MONITOR, MotionMonitor
from agent.hardware.microphone import MICROPHONE, MicrophoneService, microphone_session_owner
from agent.hardware.speaker import SPEAKER, Speaker

__all__ = [
    "AUDIO_ALERTS",
    "CAMERA",
    "CAMERA_STREAM",
    "MOTION_MONITOR",
    "SPEAKER",
    "AudioAlerts",
    "CameraService",
    "CameraStream",
    "MotionMonitor",
    "MICROPHONE",
    "MicrophoneService",
    "microphone_session_owner",
    "Speaker",
]
