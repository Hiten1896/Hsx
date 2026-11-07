from __future__ import annotations

import html
import importlib
import json
import math
import re
import secrets
import threading
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from config import WORK_DIR

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
COMMONS_HOST = "upload.wikimedia.org"
MAX_RESULTS = 10
MAX_DURATION_SECONDS = 10 * 60
MAX_CACHE_BYTES = 500 * 1024 * 1024
CACHE_TTL_SECONDS = 24 * 60 * 60
STREAM_TTL_SECONDS = 30 * 60
AUDIO_EXTENSIONS = {".flac", ".m4a", ".mp3", ".oga", ".ogg", ".opus", ".wav", ".webm"}
_TAGS = re.compile(r"<[^>]*>")


def _plain(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("value", "")
    return html.unescape(_TAGS.sub("", str(value or ""))).strip()


def _is_free_license(label: str) -> bool:
    normalized = label.casefold()
    if re.search(r"\b(?:noncommercial|nc)\b", normalized):
        return False
    return (
        "creative commons" in normalized
        or "cc by" in normalized
        or re.search(r"\bcc[- ]?(?:by|0)\b", normalized) is not None
        or "public domain" in normalized
    )


class MusicCatalog:
    def __init__(
        self,
        cache_dir: Path | None = None,
        *,
        api_request=None,
        youtube_dl_factory=None,
        max_cache_bytes: int = MAX_CACHE_BYTES,
        cache_ttl_seconds: int = CACHE_TTL_SECONDS,
        stream_ttl_seconds: int = STREAM_TTL_SECONDS,
    ):
        self.cache_dir = Path(cache_dir or WORK_DIR / ".cache" / "audio").resolve()
        self._api_request = api_request or self._request_api
        self._youtube_dl_factory = youtube_dl_factory
        self.max_cache_bytes = max_cache_bytes
        self.cache_ttl_seconds = cache_ttl_seconds
        self.stream_ttl_seconds = stream_ttl_seconds
        self._lock = threading.RLock()
        self._stream_tickets: dict[str, tuple[Path, str, float]] = {}

    @staticmethod
    def _request_api(parameters: dict[str, str]) -> dict:
        query = urllib.parse.urlencode(parameters)
        request = urllib.request.Request(
            f"{COMMONS_API}?{query}",
            headers={
                "Accept": "application/json",
                "User-Agent": "Hsx/1.0 (https://github.com/Hiten1896/Hsx)",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                data = response.read(2 * 1024 * 1024 + 1)
        except OSError as error:
            raise RuntimeError(f"Wikimedia Commons search failed: {error}") from error
        if len(data) > 2 * 1024 * 1024:
            raise RuntimeError("Wikimedia Commons returned an oversized response.")
        try:
            payload = json.loads(data)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise RuntimeError("Wikimedia Commons returned invalid JSON.") from error
        if not isinstance(payload, dict) or payload.get("error"):
            message = payload.get("error", {}).get("info", "Invalid API response.") if isinstance(payload, dict) else "Invalid API response."
            raise RuntimeError(f"Wikimedia Commons search failed: {message}")
        return payload

    def _page_details(self, *, titles: str = "", page_id: int | None = None) -> list[dict]:
        params = {
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "prop": "imageinfo",
            "iiprop": "url|mime|size|extmetadata",
        }
        if page_id is not None:
            params["pageids"] = str(page_id)
        else:
            params["titles"] = titles
        response = self._api_request(params)
        pages = response.get("query", {}).get("pages", [])
        if isinstance(pages, dict):
            return list(pages.values())
        return pages if isinstance(pages, list) else []

    @staticmethod
    def _licensed_audio(page: dict) -> dict | None:
        if page.get("ns") != 6:
            return None
        images = page.get("imageinfo")
        if not isinstance(images, list) or not images or not isinstance(images[0], dict):
            return None
        image = images[0]
        mime = str(image.get("mime", "")).casefold()
        license_metadata = image.get("extmetadata", {})
        license_name = _plain(license_metadata.get("LicenseShortName", ""))
        if (
            not mime.startswith("audio/")
            or not _is_free_license(license_name)
            or not isinstance(image.get("url"), str)
        ):
            return None
        return {
            "file_id": int(page["pageid"]),
            "title": _plain(page.get("title", "")),
            "artist": _plain(license_metadata.get("Artist", "")),
            "license": license_name,
            "license_url": _plain(license_metadata.get("LicenseUrl", "")),
            "attribution": _plain(license_metadata.get("Credit", "")),
            "attribution_required": _plain(license_metadata.get("AttributionRequired", "")).casefold() == "true",
            "description_url": str(image.get("descriptionurl", "")),
            "size": int(image.get("size", 0)),
            "mime": mime,
            "url": image["url"],
        }

    def search(self, query: str) -> dict:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("Enter a music search term.")
        query = query.strip()[:160]
        response = self._api_request({
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "list": "search",
            "srnamespace": "6",
            "srsearch": query,
            "srlimit": str(MAX_RESULTS),
        })
        hits = response.get("query", {}).get("search", [])
        titles = [item.get("title") for item in hits if isinstance(item, dict) and isinstance(item.get("title"), str)]
        if not titles:
            return {"success": True, "query": query, "results": [], "message": "No freely licensed audio matched that search."}
        pages = self._page_details(titles="|".join(titles[:MAX_RESULTS]))
        results = []
        for page in pages:
            try:
                item = self._licensed_audio(page)
            except (KeyError, TypeError, ValueError):
                item = None
            if item is not None:
                results.append({key: value for key, value in item.items() if key != "url"})
        return {
            "success": True,
            "query": query,
            "results": results[:MAX_RESULTS],
            "message": f"Found {len(results[:MAX_RESULTS])} freely licensed audio files on Wikimedia Commons.",
        }

    def _youtube_dl(self):
        if self._youtube_dl_factory is not None:
            return self._youtube_dl_factory
        try:
            return importlib.import_module("yt_dlp").YoutubeDL
        except ImportError as error:
            raise RuntimeError("Music playback needs the approved yt-dlp package in Hsx's project environment.") from error

    def _clean_cache(self, required_bytes: int = 0, protected_path: Path | None = None) -> None:
        if required_bytes < 0 or required_bytes > self.max_cache_bytes:
            raise ValueError("The audio file exceeds the 500 MB music cache limit.")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        protected = protected_path.resolve() if protected_path is not None else None
        now = time.time()
        files = []
        for path in self.cache_dir.iterdir():
            if path.is_symlink() or not path.is_file() or path.suffix.casefold() not in AUDIO_EXTENSIONS:
                continue
            stat = path.stat()
            if path.resolve() != protected and now - stat.st_mtime > self.cache_ttl_seconds:
                path.unlink()
                continue
            files.append((stat.st_mtime, path, stat.st_size))
        total = sum(size for _, _, size in files)
        for _, path, size in sorted(files):
            if total + required_bytes <= self.max_cache_bytes:
                break
            if path.resolve() == protected:
                continue
            path.unlink()
            total -= size
        if total + required_bytes > self.max_cache_bytes:
            raise ValueError("The audio file exceeds the 500 MB music cache limit.")

    def _create_stream_ticket(self, path: Path) -> str:
        path = path.resolve()
        try:
            path.relative_to(self.cache_dir)
        except ValueError as error:
            raise ValueError("Music files must stay inside the audio cache.") from error
        if not path.is_file() or path.suffix.casefold() not in AUDIO_EXTENSIONS:
            raise FileNotFoundError("The cached audio file is unavailable.")
        stream_id = uuid.uuid4().hex
        secret = secrets.token_urlsafe(32)
        with self._lock:
            self._stream_tickets[stream_id] = (
                path,
                secret,
                time.time() + self.stream_ttl_seconds,
            )
        return stream_id

    def authorize_stream(self, stream_id: str) -> str | None:
        with self._lock:
            ticket = self._stream_tickets.get(stream_id)
            if ticket is None:
                return None
            _, secret, expires = ticket
            if expires <= time.time():
                self._stream_tickets.pop(stream_id, None)
                return None
            return secret

    def resolve_stream(self, stream_id: str, secret: str) -> Path | None:
        if not re.fullmatch(r"[a-f0-9]{32}", stream_id):
            return None
        with self._lock:
            ticket = self._stream_tickets.get(stream_id)
            if ticket is None:
                return None
            path, expected, expires = ticket
            if expires <= time.time() or not secrets.compare_digest(expected, secret):
                if expires <= time.time():
                    self._stream_tickets.pop(stream_id, None)
                return None
            try:
                path.resolve().relative_to(self.cache_dir)
            except (OSError, ValueError):
                return None
            if not path.is_file() or path.suffix.casefold() not in AUDIO_EXTENSIONS:
                return None
            return path

    def _resolve_media(self, file_id: int) -> dict:
        if isinstance(file_id, bool) or not isinstance(file_id, int) or file_id < 1:
            raise ValueError("Choose a music file from the search results.")
        pages = self._page_details(page_id=file_id)
        page = next((item for item in pages if item.get("pageid") == file_id), None)
        if page is None:
            raise ValueError("That Wikimedia Commons file no longer exists.")
        item = self._licensed_audio(page)
        if item is None:
            raise ValueError("Playback is limited to audio files with a verified free license.")
        parsed_url = urllib.parse.urlsplit(item["url"])
        if parsed_url.scheme != "https" or parsed_url.hostname != COMMONS_HOST or parsed_url.username or parsed_url.password:
            raise ValueError("The Commons media URL failed host validation.")
        if item["size"] <= 0 or item["size"] > self.max_cache_bytes:
            raise ValueError("The audio file exceeds the 500 MB music cache limit.")
        return item

    def _download_media(self, item: dict) -> tuple[Path, float]:
        with self._lock:
            self._clean_cache(item["size"])
            prefix = f"{item['file_id']}-{uuid.uuid4().hex}"
            template = self.cache_dir / f"{prefix}.%(ext)s"
            downloader_type = self._youtube_dl()
            with downloader_type({
                "format": "bestaudio/best",
                "outtmpl": str(template),
                "max_filesize": self.max_cache_bytes,
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True,
                "cachedir": False,
            }) as downloader:
                metadata = downloader.extract_info(item["url"], download=False)
                duration = metadata.get("duration") if isinstance(metadata, dict) else None
                if (
                    not isinstance(duration, (int, float))
                    or not math.isfinite(duration)
                    or duration <= 0
                    or duration > MAX_DURATION_SECONDS
                ):
                    raise ValueError("Music tracks must have a known duration of 10 minutes or less.")
                extension = str(metadata.get("ext", "")).casefold()
                if f".{extension}" not in AUDIO_EXTENSIONS:
                    raise ValueError("The selected Commons item is not in a supported audio format.")
                expected_path = Path(downloader.prepare_filename(metadata)).resolve()
                try:
                    expected_path.relative_to(self.cache_dir)
                except ValueError as error:
                    raise ValueError("The downloader attempted to leave the audio cache.") from error
                downloader.download([item["url"]])
            if not expected_path.is_file():
                candidates = [
                    path for path in self.cache_dir.glob(f"{prefix}.*")
                    if not path.is_symlink() and path.is_file() and path.suffix.casefold() in AUDIO_EXTENSIONS
                ]
                if len(candidates) != 1:
                    raise RuntimeError("yt-dlp did not produce exactly one audio file.")
                expected_path = candidates[0]
            size = expected_path.stat().st_size
            if size <= 0 or size > self.max_cache_bytes:
                expected_path.unlink(missing_ok=True)
                raise ValueError("Downloaded audio exceeded the 500 MB music cache limit.")
            self._clean_cache(protected_path=expected_path)
            return expected_path, float(duration)

    @staticmethod
    def _player_track(item: dict, stream_id: str, duration: float) -> dict:
        return {
            "type": "music_play",
            "stream_id": stream_id,
            "title": item["title"],
            "artist": item["artist"],
            "license": item["license"],
            "license_url": item["license_url"],
            "attribution": item["attribution"],
            "attribution_required": item["attribution_required"],
            "duration": duration,
        }

    def play(self, file_id: int, queue: list[int] | None = None) -> dict:
        if queue is not None and (not isinstance(queue, list) or len(queue) > 9):
            raise ValueError("A music queue can contain at most 10 tracks.")
        file_ids = [file_id, *(queue or [])]
        if any(isinstance(item_id, bool) or not isinstance(item_id, int) or item_id < 1 for item_id in file_ids):
            raise ValueError("Choose music files from the search results.")
        if len(set(file_ids)) != len(file_ids):
            raise ValueError("A music queue cannot contain the same file more than once.")
        items = [self._resolve_media(item_id) for item_id in file_ids]
        total_size = sum(item["size"] for item in items)
        if total_size > self.max_cache_bytes:
            raise ValueError("This music queue exceeds the 500 MB audio cache limit.")
        self._clean_cache(total_size)
        tracks = []
        for item in items:
            path, duration = self._download_media(item)
            tracks.append(self._player_track(item, self._create_stream_ticket(path), duration))
        first = tracks[0]

        return {
            "success": True,
            "browser_action": {**first, "queue": tracks},
            "message": f"Prepared {len(tracks)} freely licensed track{'s' if len(tracks) != 1 else ''} for browser playback.",
        }


MUSIC_CATALOG = MusicCatalog()
