#!/usr/bin/env python3
"""hsx-cam — tiny MJPEG camera server for the HP PC node.

Runs standalone (no Hsx needed). The camera is the switch:

  * First client connects to /stream  -> ffmpeg starts  -> camera ON
  * Last client disconnects           -> ffmpeg stops   -> camera OFF

So the Hsx webpage turns the HP PC camera on/off simply by opening or
closing the camera feed. Listens on the Tailscale IP only, so the
neighbour's Wi-Fi can never reach it.

Env vars:
  CAM_HOST    bind address (default: auto-detect Tailscale IPv4)
  CAM_PORT    default 8080
  CAM_DEVICE  default /dev/video0  (list with: v4l2-ctl --list-devices)
  CAM_TOKEN   shared secret, must match the token in the webpage URL
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

CAM_PORT = int(os.getenv("CAM_PORT", "8080"))
CAM_DEVICE = os.getenv("CAM_DEVICE", "/dev/video0")
CAM_TOKEN = os.getenv("CAM_TOKEN", "hsx-change-me")
WIDTH, HEIGHT, FPS = 640, 480, 15
MAX_FRAME_BYTES = 2 * 1024 * 1024
JPEG_START = b"\xff\xd8"
JPEG_END = b"\xff\xd9"


def tailscale_ip() -> str:
    try:
        out = subprocess.run(
            ["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=5
        )
        first = out.stdout.strip().splitlines()
        if first:
            return first[0]
    except Exception:
        pass
    return "127.0.0.1"


CAM_HOST = os.getenv("CAM_HOST") or tailscale_ip()


class CameraHub:
    """One shared ffmpeg capture; every viewer gets the latest JPEG frame."""

    def __init__(self) -> None:
        self.cond = threading.Condition()
        self.clients = 0
        self.proc: subprocess.Popen | None = None
        self.frame = b""
        self.version = 0

    def acquire(self) -> None:
        with self.cond:
            self.clients += 1
            if self.proc is None:
                self._start_locked()
            self.cond.notify_all()

    def release(self) -> None:
        with self.cond:
            self.clients = max(0, self.clients - 1)
            if self.clients == 0:
                self._stop_locked()
            self.cond.notify_all()

    def _start_locked(self) -> None:
        if shutil.which("ffmpeg") is None:
            raise RuntimeError("ffmpeg is not installed. Run: sudo apt install ffmpeg")
        if not os.path.exists(CAM_DEVICE):
            raise RuntimeError(
                f"camera device not found: {CAM_DEVICE} "
                "(list devices with: v4l2-ctl --list-devices)"
            )
        cmd = [
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
            "-f", "v4l2", "-framerate", str(FPS),
            "-video_size", f"{WIDTH}x{HEIGHT}",
            "-i", CAM_DEVICE,
            "-an", "-vf", f"scale={WIDTH}:{HEIGHT}",
            "-c:v", "mjpeg", "-q:v", "8",
            "-f", "image2pipe", "pipe:1",
        ]
        self.proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )
        threading.Thread(target=self._pump, name="hsx-cam-pump", daemon=True).start()
        print(f"[hsx-cam] camera ON ({CAM_DEVICE}, {self.clients} viewer(s))", flush=True)

    def _stop_locked(self) -> None:
        proc, self.proc = self.proc, None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        self.frame = b""
        print("[hsx-cam] camera OFF (no viewers)", flush=True)

    def _pump(self) -> None:
        pending = bytearray()
        while True:
            with self.cond:
                proc = self.proc
            if proc is None or proc.stdout is None:
                return
            chunk = proc.stdout.read(4096)
            if not chunk:
                return
            pending.extend(chunk)
            while True:
                start = pending.find(JPEG_START)
                if start < 0:
                    if len(pending) > 1:
                        del pending[:-1]
                    break
                if start:
                    del pending[:start]
                end = pending.find(JPEG_END, len(JPEG_START))
                if end < 0:
                    if len(pending) > MAX_FRAME_BYTES:
                        pending.clear()
                    break
                frame_end = end + len(JPEG_END)
                frame = bytes(pending[:frame_end])
                del pending[:frame_end]
                if 4 <= len(frame) <= MAX_FRAME_BYTES:
                    with self.cond:
                        self.version += 1
                        self.frame = frame
                        self.cond.notify_all()

    def wait_frame(self, after_version: int, timeout: float = 5.0):
        with self.cond:
            ok = self.cond.wait_for(
                lambda: self.version > after_version or self.proc is None,
                timeout=timeout,
            )
            if ok and self.version > after_version:
                return self.frame, self.version, time.time()
            return None, self.version, time.time()


CAM = CameraHub()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args) -> None:
        pass

    def _authorized(self) -> bool:
        query = parse_qs(urlparse(self.path).query)
        return query.get("token", [""])[0] == CAM_TOKEN

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if not self._authorized():
            self.send_response(401)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/status":
            body = json.dumps({
                "camera_on": CAM.proc is not None,
                "viewers": CAM.clients,
                "device": CAM_DEVICE,
                "host": CAM_HOST,
                "port": CAM_PORT,
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if path != "/stream":
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        try:
            CAM.acquire()
        except RuntimeError as error:
            body = str(error).encode()
            self.send_response(503)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=hsxframe")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        version = 0
        try:
            while True:
                frame, version, captured_at = CAM.wait_frame(version)
                if frame is None:
                    return
                self.wfile.write(
                    b"--hsxframe\r\nContent-Type: image/jpeg\r\n"
                    + f"X-Hsx-Captured-At: {captured_at:.6f}\r\n".encode("ascii")
                    + f"Content-Length: {len(frame)}\r\n\r\n".encode("ascii")
                    + frame + b"\r\n"
                )
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            CAM.release()


def shutdown(_signum, _frame):
    with CAM.cond:
        CAM.clients = 0
        CAM._stop_locked()
    raise SystemExit(0)


signal.signal(signal.SIGTERM, shutdown)
signal.signal(signal.SIGINT, shutdown)

if __name__ == "__main__":
    server = ThreadingHTTPServer((CAM_HOST, CAM_PORT), Handler)
    print(f"[hsx-cam] listening on http://{CAM_HOST}:{CAM_PORT} (Tailscale only)", flush=True)
    server.serve_forever()