import hmac
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from agent.memory import MemoryManager
from agent.orchestrator import run_agent
from agent.skill_loader import load_skills
from config import ALLOWED_ORIGINS, AUTH_TOKEN, MEMORY_DB, OUTPUTS_DIR, UPLOADS_DIR
from video_tools.pipeline import create_video_job, get_video_job, list_video_jobs

ROOT = Path(__file__).resolve().parent
TOOLS, SKILL_MAP = load_skills(str(ROOT / "skills"))
HISTORY = []
MEMORY = MemoryManager(MEMORY_DB)


class HsxHandler(BaseHTTPRequestHandler):
    def _request_path(self) -> str:
        return urlsplit(self.path).path

    def _api_access_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        if origin and origin not in ALLOWED_ORIGINS:
            self._json({"error": "Origin is not allowed."}, 403)
            return False
        if AUTH_TOKEN:
            supplied = self.headers.get("Authorization", "")
            expected = f"Bearer {AUTH_TOKEN}"
            if not hmac.compare_digest(supplied, expected):
                self._json({"error": "Authentication required."}, 401)
                return False
        return True

    def _json(self, payload: dict, status: int = 200) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        origin = self.headers.get("Origin")
        if origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self) -> None:
        path = self._request_path()
        if path.startswith("/api/") and not self._api_access_allowed():
            return
        if path == "/health":
            self._json({"status": "ok"})
        elif path == "/api/skills":
            self._json({"skills": [tool["name"] for tool in TOOLS]})
        elif path == "/api/memory":
            self._json({"facts": MEMORY.list_facts()})
        elif self.path == "/api/outputs":
            files = [{"name": p.name, "path": str(p), "type": p.suffix[1:]} for p in OUTPUTS_DIR.iterdir() if p.is_file()]
            self._json({"files": files})
        elif self.path == "/api/video/jobs":
            self._json({"jobs": list_video_jobs()})
        elif path.startswith("/api/video/jobs/"):
            job_id = path.removeprefix("/api/video/jobs/")
            job = get_video_job(job_id)
            if job is None:
                self._json({"error": "Job not found."}, 404)
                return
            self._json({"job": job})
        elif self.path == "/api/state":
            self._json({
                "skills": [tool["name"] for tool in TOOLS],
                "memory": MEMORY.list_facts(),
                "outputs": self._recent_outputs(),
                "uploads": [{"name": p.name, "type": p.suffix[1:]} for p in UPLOADS_DIR.iterdir() if p.is_file()],
                "video_jobs": list_video_jobs(),
            })
        elif path.startswith("/api/outputs/"):
            filename = Path(path.removeprefix("/api/outputs/")).name
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
        path = self._request_path()
        if path.startswith("/api/") and not self._api_access_allowed():
            return
        if path == "/api/chat":
            payload = self._body()
            message = str(payload.get("message", "")).strip()
            if not message:
                self._json({"error": "Message is required."}, 400)
                return
            response, _ = run_agent(message, HISTORY, TOOLS, SKILL_MAP)
            self._json({"response": response, "outputs": self._recent_outputs()})
        elif path == "/api/memory":
            payload = self._body()
            try:
                fact = MEMORY.add_fact(
                    str(payload.get("text", payload.get("fact", ""))),
                    str(payload.get("category", "general")),
                    pinned=bool(payload.get("pinned", False)),
                )
            except ValueError as error:
                self._json({"error": str(error)}, 400)
                return
            self._json({"fact": fact, "facts": MEMORY.list_facts()})
        elif path.startswith("/api/memory/"):
            try:
                fact_id = int(path.removeprefix("/api/memory/"))
                payload = self._body()
                fact = MEMORY.update_fact(
                    fact_id,
                    str(payload.get("text", "")),
                    str(payload.get("category", "general")),
                    bool(payload.get("pinned", False)),
                )
            except (ValueError, KeyError) as error:
                self._json({"error": str(error)}, 400 if isinstance(error, ValueError) else 404)
                return
            self._json({"fact": fact})
        elif path == "/api/upload":
            payload = self._body()
            filename = Path(str(payload.get("filename", "upload.txt"))).name
            content = str(payload.get("content", ""))
            if not content or not filename:
                self._json({"error": "A file is required."}, 400)
                return
            destination = UPLOADS_DIR / filename
            destination.write_text(content, encoding="utf-8")
            self._json({"success": True, "name": destination.name, "type": destination.suffix[1:]})
        elif path == "/api/video/jobs":
            payload = self._body()
            project_id = str(payload.get("project_id", "project_video")).strip() or "project_video"
            prompt = str(payload.get("prompt", "")).strip()
            source_files = payload.get("source_files", [])
            if not prompt:
                self._json({"error": "A prompt is required for a video job."}, 400)
                return
            job = create_video_job(
                project_id=project_id,
                prompt=prompt,
                source_files=source_files if isinstance(source_files, list) else [source_files],
                output_filename=str(payload.get("output_filename", "edited_video.mp4")).strip() or "edited_video.mp4",
            )
            self._json({"job": job})
        else:
            self._json({"error": "Not found."}, 404)

    def do_OPTIONS(self) -> None:
        if self._request_path().startswith("/api/"):
            origin = self.headers.get("Origin")
            if origin and origin not in ALLOWED_ORIGINS:
                self._json({"error": "Origin is not allowed."}, 403)
                return
        self.send_response(204)
        origin = self.headers.get("Origin")
        if origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_DELETE(self) -> None:
        path = self._request_path()
        if path.startswith("/api/") and not self._api_access_allowed():
            return
        if not path.startswith("/api/memory/"):
            self._json({"error": "Not found."}, 404)
            return
        try:
            fact_id = int(path.removeprefix("/api/memory/"))
            MEMORY.delete_fact(fact_id)
        except ValueError:
            self._json({"error": "Memory id must be an integer."}, 400)
            return
        self._json({"success": True, "facts": MEMORY.list_facts()})

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