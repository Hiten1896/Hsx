from http.server import ThreadingHTTPServer

from config import AUTH_TOKEN, AUTH_TOKEN_GENERATED, HOST, PORT
from server import APPROVAL_GUARD, TOOLS, HsxHandler


if __name__ == "__main__":
    print(f"Hsx ready. Loaded {len(TOOLS)} skills at http://{HOST}:{PORT}")
    if AUTH_TOKEN_GENERATED:
        print(f"Generated bearer token (save it; it is only shown once): {AUTH_TOKEN}")
    if APPROVAL_GUARD.generated_pin:
        print(f"Generated owner PIN (save it; it is only shown once): {APPROVAL_GUARD.generated_pin}")
    ThreadingHTTPServer((HOST, PORT), HsxHandler).serve_forever()
