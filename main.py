import os
import logging
import shutil
import signal
import sys
import threading
from http.server import ThreadingHTTPServer

from agent.hardware import AUDIO_ALERTS, CAMERA, CAMERA_STREAM, MOTION_MONITOR, SPEAKER
from agent.ml.anomaly import ANOMALY_MONITOR, SystemMetrics
from agent.ml.forecaster import FORECASTER
from agent.ml.log_recorder import LOG_RECORDER
from agent.memory.knowledge_graph import KNOWLEDGE_GRAPH
from agent.mesh import MeshDiscoveryService
from config import DATA_DIR, HOST, PORT
from server import APPROVAL_GUARD, MESH_GATEWAY, TOOLS, HsxHandler

LOGGER = logging.getLogger("hsx.main")


def _record_resource_sample(metrics: SystemMetrics) -> None:
    FORECASTER.record_resource_sample(
        disk_used_bytes=shutil.disk_usage(DATA_DIR).used,
        ram_percent=metrics.ram_percent,
    )
    try:
        ram_threshold = float(os.getenv("HSX_AUDIO_ALERT_RAM_PERCENT", "90"))
        cpu_threshold = float(os.getenv("HSX_AUDIO_ALERT_CPU_PERCENT", "95"))
    except ValueError:
        LOGGER.warning("Invalid audio alert thresholds; using 90%% RAM and 95%% CPU.")
        ram_threshold, cpu_threshold = 90.0, 95.0
    if metrics.ram_percent >= ram_threshold or metrics.cpu_percent >= cpu_threshold:
        AUDIO_ALERTS.play_async("warning")


def _record_anomaly_alert(_event: dict) -> None:
    AUDIO_ALERTS.play_async("warning")


def _start_hardware_services() -> None:
    SPEAKER.start()
    camera_enabled = CAMERA_STREAM.enabled()
    motion_enabled = MOTION_MONITOR.enabled()
    if camera_enabled and motion_enabled:
        LOGGER.error(
            "Camera streaming and Motion both need /dev/video0; enable only one service "
            "unless the camera explicitly supports concurrent capture."
        )
        return
    if not camera_enabled and not motion_enabled:
        try:
            CAMERA.start()
        except (OSError, RuntimeError, ValueError):
            LOGGER.exception("Could not start the live camera service.")
    for service in (CAMERA_STREAM, MOTION_MONITOR):
        if not service.enabled():
            continue
        try:
            service.start()
        except (OSError, RuntimeError, ValueError):
            LOGGER.exception("Could not start opt-in hardware service %s.", type(service).__name__)


if __name__ == "__main__":
    print(f"Hsx ready. Loaded {len(TOOLS)} skills at http://{HOST}:{PORT}")
    if APPROVAL_GUARD.generated_pin:
        print(f"Generated owner PIN (save it; it is only shown once): {APPROVAL_GUARD.generated_pin}")
    http_server = ThreadingHTTPServer((HOST, PORT), HsxHandler)
    mesh_server = None
    mesh_thread = None
    discovery_service = None
    if MESH_GATEWAY is not None:
        if MESH_GATEWAY.port == PORT:
            raise ValueError("HSX_MESH_PORT must differ from HSX_PORT.")
        mesh_server = ThreadingHTTPServer(
            (MESH_GATEWAY.host, MESH_GATEWAY.port),
            HsxHandler,
        )
        MESH_GATEWAY.wrap_server(mesh_server)
        mesh_thread = threading.Thread(
            target=mesh_server.serve_forever,
            name="hsx-mesh-gateway",
            daemon=True,
        )
        mesh_thread.start()
        discovery_service = MeshDiscoveryService(MESH_GATEWAY)
        print(f"Secure HSX mesh API ready at https://{MESH_GATEWAY.host}:{MESH_GATEWAY.port}")
    LOG_RECORDER.install()
    if discovery_service is not None:
        try:
            discovery_service.start()
        except OSError:
            LOGGER.exception("Could not start the opt-in LAN mesh discovery listener.")

    if hasattr(signal, "SIGUSR1"):
        def reload_after_update(_signum, _frame):
            threading.Thread(target=http_server.shutdown, daemon=True).start()
            if mesh_server is not None:
                threading.Thread(target=mesh_server.shutdown, daemon=True).start()

        signal.signal(signal.SIGUSR1, reload_after_update)

    ANOMALY_MONITOR.on_sample = _record_resource_sample
    ANOMALY_MONITOR.on_anomaly = _record_anomaly_alert
    KNOWLEDGE_GRAPH.start_build()
    _start_hardware_services()
    ANOMALY_MONITOR.start()
    try:
        http_server.serve_forever()
    finally:
        if discovery_service is not None:
            discovery_service.stop(timeout=3)
        if mesh_server is not None:
            mesh_server.shutdown()
            mesh_server.server_close()
        if mesh_thread is not None:
            mesh_thread.join(timeout=5)
        ANOMALY_MONITOR.stop(timeout=5)
        CAMERA.stop(timeout=5)
        CAMERA_STREAM.stop(timeout=5)
        MOTION_MONITOR.stop(timeout=5)
        SPEAKER.stop(timeout=3)
        AUDIO_ALERTS.stop(timeout=3)
        http_server.server_close()
        LOG_RECORDER.stop(timeout=5)
    if hasattr(signal, "SIGUSR1"):
        os.execv(sys.executable, [sys.executable, *sys.argv])
