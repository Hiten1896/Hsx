import os
import signal
import sys
import threading
from http.server import ThreadingHTTPServer

from config import AUTH_TOKEN, AUTH_TOKEN_GENERATED, HOST, PORT
from server import APPROVAL_GUARD, TOOLS, HsxHandler


if __name__ == "__main__":
    print(f"Hsx ready. Loaded {len(TOOLS)} skills at http://{HOST}:{PORT}")
    if AUTH_TOKEN_GENERATED:
        print(f"Generated bearer token (save it; it is only shown once): {AUTH_TOKEN}")
    if APPROVAL_GUARD.generated_pin:
        print(f"Generated owner PIN (save it; it is only shown once): {APPROVAL_GUARD.generated_pin}")
    http_server = ThreadingHTTPServer((HOST, PORT), HsxHandler)

    if hasattr(signal, "SIGUSR1"):
        def reload_after_update(_signum, _frame):
            threading.Thread(target=http_server.shutdown, daemon=True).start()

        signal.signal(signal.SIGUSR1, reload_after_update)

    http_server.serve_forever()
    http_server.server_close()
    if hasattr(signal, "SIGUSR1"):
        os.execv(sys.executable, [sys.executable, *sys.argv])
