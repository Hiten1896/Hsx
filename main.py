import os

from server import HsxHandler, TOOLS
from http.server import ThreadingHTTPServer
from config import HOST, PORT


if __name__ == "__main__":
    print(f"Hsx ready. Loaded {len(TOOLS)} skills at http://{HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), HsxHandler).serve_forever()