import os
import shutil
import signal
import sys
import threading
from http.server import ThreadingHTTPServer

from agent.ml.anomaly import ANOMALY_MONITOR, SystemMetrics
from agent.ml.forecaster import FORECASTER
from config import DATA_DIR, HOST, PORT
from server import APPROVAL_GUARD, TOOLS, HsxHandler


def _record_resource_sample(metrics: SystemMetrics) -> None:
    FORECASTER.record_resource_sample(
        disk_used_bytes=shutil.disk_usage(DATA_DIR).used,
        ram_percent=metrics.ram_percent,
    )


if __name__ == "__main__":
    print(f"Hsx ready. Loaded {len(TOOLS)} skills at http://{HOST}:{PORT}")
    if APPROVAL_GUARD.generated_pin:
        print(f"Generated owner PIN (save it; it is only shown once): {APPROVAL_GUARD.generated_pin}")
    http_server = ThreadingHTTPServer((HOST, PORT), HsxHandler)

    if hasattr(signal, "SIGUSR1"):
        def reload_after_update(_signum, _frame):
            threading.Thread(target=http_server.shutdown, daemon=True).start()

        signal.signal(signal.SIGUSR1, reload_after_update)

    ANOMALY_MONITOR.on_sample = _record_resource_sample
    ANOMALY_MONITOR.start()
    try:
        http_server.serve_forever()
    finally:
        ANOMALY_MONITOR.stop(timeout=5)
        http_server.server_close()
    if hasattr(signal, "SIGUSR1"):
        os.execv(sys.executable, [sys.executable, *sys.argv])
