from http.server import ThreadingHTTPServer

from config import AUTH_TOKEN, HOST, PORT
from server import APPROVAL_TOKEN, TOOLS, HsxHandler


if __name__ == "__main__":
    print(f"Hsx ready. Loaded {len(TOOLS)} skills at http://{HOST}:{PORT}")
    print(f"Bearer token: {AUTH_TOKEN}")
    print(f"Approval token (keep private): {APPROVAL_TOKEN}")
    ThreadingHTTPServer((HOST, PORT), HsxHandler).serve_forever()