import os

from server import HsxHandler, TOOLS
from http.server import ThreadingHTTPServer


if __name__ == "__main__":
    port = int(os.getenv("HSX_PORT", "8000"))
    print(f"Hsx ready. Loaded {len(TOOLS)} skills at http://127.0.0.1:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), HsxHandler).serve_forever()