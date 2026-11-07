import os
import time
from http.cookies import SimpleCookie
from pathlib import Path

import pytest

from agent.music_catalog import MusicCatalog


def _page(pageid: int, title: str, *, mime: str, license_name: str, size: int = 1024) -> dict:
    return {
        "pageid": pageid,
        "ns": 6,
        "title": title,
        "imageinfo": [{
            "url": f"https://upload.wikimedia.org/audio/{pageid}.ogg",
            "descriptionurl": f"https://commons.wikimedia.org/wiki/{title.replace(' ', '_')}",
            "mime": mime,
            "size": size,
            "extmetadata": {
                "LicenseShortName": {"value": license_name},
                "Artist": {"value": "<b>Example Artist</b>"},
                "AttributionRequired": {"value": "true"},
            },
        }],
    }


def test_search_returns_only_audio_with_free_license_and_attribution(tmp_path):
    responses = iter([
        {"query": {"search": [
            {"pageid": 101, "title": "File:Free.ogg"},
            {"pageid": 102, "title": "File:Copyrighted.ogg"},
            {"pageid": 103, "title": "File:Image.png"},
        ]}},
        {"query": {"pages": [
            _page(101, "File:Free.ogg", mime="audio/ogg", license_name="CC BY-SA 4.0"),
            _page(102, "File:Copyrighted.ogg", mime="audio/ogg", license_name="All rights reserved"),
            _page(103, "File:Image.png", mime="image/png", license_name="Public domain"),
        ]}},
    ])
    catalog = MusicCatalog(tmp_path / "audio", api_request=lambda _params: next(responses))

    result = catalog.search("ambient piano")

    assert [item["file_id"] for item in result["results"]] == [101]
    assert result["results"][0]["license"] == "CC BY-SA 4.0"
    assert result["results"][0]["artist"] == "Example Artist"
    assert result["results"][0]["attribution_required"] is True


def test_noncommercial_creative_commons_audio_is_not_treated_as_free(tmp_path):
    catalog = MusicCatalog(
        tmp_path / "audio",
        api_request=lambda _params: {"query": {"pages": [
            _page(101, "File:Noncommercial.ogg", mime="audio/ogg", license_name="CC BY-NC 4.0"),
        ]}},
    )

    with pytest.raises(ValueError, match="free license"):
        catalog.play(101)


def test_play_uses_only_validated_commons_media_and_creates_scoped_stream_ticket(tmp_path):
    responses = iter([{"query": {"pages": [
        _page(101, "File:Free.ogg", mime="audio/ogg", license_name="CC BY-SA 4.0"),
    ]}}])
    downloads = []

    class FakeYoutubeDL:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, url, download=False):
            assert url == "https://upload.wikimedia.org/audio/101.ogg"
            assert download is False
            return {"duration": 120, "ext": "ogg", "id": "commons-101"}

        def prepare_filename(self, info):
            return self.options["outtmpl"].replace("%(ext)s", info["ext"])

        def download(self, urls):
            downloads.extend(urls)
            Path(self.prepare_filename({"ext": "ogg"})).write_bytes(b"audio")

    catalog = MusicCatalog(
        tmp_path / "audio",
        api_request=lambda _params: next(responses),
        youtube_dl_factory=FakeYoutubeDL,
    )

    result = catalog.play(101)
    action = result["browser_action"]
    secret = catalog.authorize_stream(action["stream_id"])

    assert result["success"] is True
    assert downloads == ["https://upload.wikimedia.org/audio/101.ogg"]
    assert action["type"] == "music_play"
    assert action["title"] == "File:Free.ogg"
    assert catalog.resolve_stream(action["stream_id"], secret).read_bytes() == b"audio"
    assert catalog.resolve_stream(action["stream_id"], "wrong-secret") is None


def test_play_routes_host_tracks_to_host_audio_tool(tmp_path, monkeypatch):
    class FakeYoutubeDL:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _url, download=False):
            assert download is False
            return {"duration": 120, "ext": "ogg", "id": "commons-101"}

        def prepare_filename(self, info):
            return self.options["outtmpl"].replace("%(ext)s", info["ext"])

        def download(self, _urls):
            Path(self.prepare_filename({"ext": "ogg"})).write_bytes(b"audio")

    responses = iter([{"query": {"pages": [
        _page(101, "File:Free.ogg", mime="audio/ogg", license_name="CC BY-SA 4.0"),
    ]}}])
    catalog = MusicCatalog(
        tmp_path / "audio",
        api_request=lambda _params: next(responses),
        youtube_dl_factory=FakeYoutubeDL,
    )
    playback_calls = []
    monkeypatch.setattr(
        "skills.audio_play.skill.run",
        lambda source, target_device: playback_calls.append((source, target_device)) or {
            "success": True,
            "target_device": target_device,
        },
    )

    result = catalog.play(101, target_device="local_host")

    assert result["success"] is True
    assert result["host_playback"]["target_device"] == "local_host"
    assert playback_calls[0][0].endswith(".ogg")
    assert playback_calls[0][1] == "local_host"
    assert "browser_action" not in result


def test_play_rejects_nonfree_media_and_overlong_audio(tmp_path):
    copyrighted = MusicCatalog(
        tmp_path / "copyrighted",
        api_request=lambda _params: {"query": {"pages": [
            _page(7, "File:Copyrighted.ogg", mime="audio/ogg", license_name="All rights reserved"),
        ]}},
    )
    with pytest.raises(ValueError, match="free license"):
        copyrighted.play(7)

    class OverlongYoutubeDL:
        def __init__(self, _options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _url, download=False):
            assert download is False
            return {"duration": 601, "ext": "ogg", "id": "long"}

    overlong = MusicCatalog(
        tmp_path / "overlong",
        api_request=lambda _params: {"query": {"pages": [
            _page(8, "File:Long.ogg", mime="audio/ogg", license_name="Public domain"),
        ]}},
        youtube_dl_factory=OverlongYoutubeDL,
    )
    with pytest.raises(ValueError, match="10 minutes"):
        overlong.play(8)


def test_cache_expiry_and_size_limit_remove_oldest_files(tmp_path):
    cache = tmp_path / "audio"
    cache.mkdir()
    old = cache / "old.ogg"
    new = cache / "new.ogg"
    old.write_bytes(b"old")
    new.write_bytes(b"new")
    now = time.time()
    old.touch()
    new.touch()
    os.utime(old, (now - 25 * 60 * 60, now - 25 * 60 * 60))
    catalog = MusicCatalog(cache, max_cache_bytes=3, cache_ttl_seconds=24 * 60 * 60)

    catalog._clean_cache(required_bytes=1)

    assert not old.exists()
    assert not new.exists()


def test_cache_eviction_preserves_the_file_just_downloaded(tmp_path):
    cache = tmp_path / "audio"
    cache.mkdir()
    existing = cache / "existing.ogg"
    current = cache / "current.ogg"
    existing.write_bytes(b"old!!")
    current.write_bytes(b"new data")
    catalog = MusicCatalog(cache, max_cache_bytes=10)

    catalog._clean_cache(protected_path=current)

    assert current.read_bytes() == b"new data"
    assert not existing.exists()


def test_stream_endpoint_requires_scoped_cookie_and_supports_byte_ranges(tmp_path, monkeypatch):
    import server
    from tests.test_files_panel import _request

    cache = tmp_path / "audio"
    cache.mkdir()
    path = cache / "track.ogg"
    path.write_bytes(b"0123456789")
    catalog = MusicCatalog(cache)
    stream_id = catalog._create_stream_ticket(path)
    monkeypatch.setattr(server, "MUSIC_CATALOG", catalog)

    status, _, _ = _request(f"/api/music/stream/{stream_id}")
    assert status == 404

    status, headers, _ = _request(
        "/api/music/stream-ticket",
        method="POST",
        payload={"stream_id": stream_id},
    )
    assert status == 204
    cookie = SimpleCookie()
    cookie.load(headers["Set-Cookie"])
    secret = cookie["hsx_audio"].value
    assert "HttpOnly" in headers["Set-Cookie"]
    assert "Path=/api/music/stream/" in headers["Set-Cookie"]

    status, headers, body = _request(
        f"/api/music/stream/{stream_id}",
        headers={"Cookie": f"hsx_audio={secret}", "Range": "bytes=2-5"},
    )
    assert status == 206
    assert headers["Content-Range"] == "bytes 2-5/10"
    assert headers["Cache-Control"] == "no-store"
    assert body == b"2345"

    status, _, _ = _request(
        f"/api/music/stream/{stream_id}",
        headers={"Cookie": "hsx_audio=incorrect"},
    )
    assert status == 404
