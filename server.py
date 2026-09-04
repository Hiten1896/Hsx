import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from agent.orchestrator import run_agent
from agent.skill_loader import load_skills
from config import MEMORY_DIR, OUTPUTS_DIR, UPLOADS_DIR

ROOT = Path(__file__).resolve().parent
TOOLS, SKILL_MAP = load_skills(str(ROOT / "skills"))
HISTORY = []


def read_memory() -> list:
    path = MEMORY_DIR / "facts.json"
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def write_memory(facts: list) -> None:
    (MEMORY_DIR / "facts.json").write_text(json.dumps(facts[-40:], indent=2), encoding="utf-8")


class HsxHandler(BaseHTTPRequestHandler):
    def _json(self, payload: dict, status: int = 200) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self) -> None:
        if self.path == "/api/skills":
            self._json({"skills": [tool["name"] for tool in TOOLS]})
        elif self.path == "/api/memory":
            self._json({"facts": read_memory()})
        elif self.path == "/api/outputs":
            files = [{"name": p.name, "path": str(p), "type": p.suffix[1:]} for p in OUTPUTS_DIR.iterdir() if p.is_file()]
            self._json({"files": files})
        elif self.path == "/api/state":
            self._json({
                "skills": [tool["name"] for tool in TOOLS],
                "memory": read_memory(),
                "outputs": self._recent_outputs(),
                "uploads": [{"name": p.name, "type": p.suffix[1:]} for p in UPLOADS_DIR.iterdir() if p.is_file()],
            })
        elif self.path.startswith("/api/outputs/"):
            filename = Path(self.path.removeprefix("/api/outputs/")).name
            output = (OUTPUTS_DIR / filename).resolve()
            if OUTPUTS_DIR.resolve() not in output.parents or not output.is_file():
                self._json({"error": "Artifact not found."}, 404)
                return
            data = output.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", f'attachment; filename="{output.name}"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self._serve_frontend()

    def do_POST(self) -> None:
        if self.path == "/api/chat":
            payload = self._body()
            message = str(payload.get("message", "")).strip()
            if not message:
                self._json({"error": "Message is required."}, 400)
                return
            response, _ = run_agent(message, HISTORY, TOOLS, SKILL_MAP)
            self._json({"response": response, "outputs": self._recent_outputs()})
        elif self.path == "/api/memory":
            fact = str(self._body().get("fact", "")).strip()
            facts = read_memory()
            if fact:
                facts.append(fact)
                write_memory(facts)
            self._json({"facts": facts})
        elif self.path == "/api/upload":
            payload = self._body()
            filename = Path(str(payload.get("filename", "upload.txt"))).name
            content = str(payload.get("content", ""))
            if not content or not filename:
                self._json({"error": "A file is required."}, 400)
                return
            destination = UPLOADS_DIR / filename
            destination.write_text(content, encoding="utf-8")
            self._json({"success": True, "name": destination.name, "type": destination.suffix[1:]})
        else:
            self._json({"error": "Not found."}, 404)

    def _recent_outputs(self) -> list:
        return [{"name": p.name, "path": str(p), "type": p.suffix[1:]} for p in OUTPUTS_DIR.iterdir() if p.is_file()]

    def _serve_frontend(self) -> None:
        request_path = self.path.split("?", 1)[0]
        cleaned_path = request_path.removeprefix("/web/").removeprefix("/web").lstrip("/")
        if not cleaned_path:
            cleaned_path = "index.html"
        asset = (ROOT / "web" / cleaned_path).resolve()
        if ROOT / "web" not in asset.parents or not asset.is_file():
            self._json({"error": "Not found."}, 404)
            return
        page = asset.read_bytes()
        content_type = {
            ".css": "text/css",
            ".js": "text/javascript",
            ".html": "text/html",
            ".svg": "image/svg+xml",
            ".json": "application/json",
            ".png": "image/png",
            ".ico": "image/x-icon",
        }.get(asset.suffix.lower(), "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8" if "text" in content_type or "javascript" in content_type else content_type)
        self.send_header("Content-Length", str(len(page)))
        self.end_headers()
        self.wfile.write(page)


if __name__ == "__main__":
    print(f"Hsx ready. Loaded {len(TOOLS)} skills: {', '.join(tool['name'] for tool in TOOLS)}")
    ThreadingHTTPServer(("127.0.0.1", 8000), HsxHandler).serve_forever()