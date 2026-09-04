from server import HsxHandler, TOOLS
from http.server import ThreadingHTTPServer


if __name__ == "__main__":
    print(f"Hsx ready. Loaded {len(TOOLS)} skills")
    ThreadingHTTPServer(("127.0.0.1", 8000), HsxHandler).serve_forever()