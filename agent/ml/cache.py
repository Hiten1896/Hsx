from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from config import DATA_DIR
from .storage import atomic_write_json

LOGGER = logging.getLogger("hsx.ml.cache")
WORD_PATTERN = re.compile(r"[^\W_]+", re.UNICODE)
MAX_ENTRIES = 512
MAX_RESPONSE_CHARS = 8000
MAX_QUERY_CHARS = 2000
MAX_TOKENS = 256
SECRET_PATTERN = re.compile(
    r"(?i)\b(password|passwd|secret|api[_ -]?key|token)\s*[:=]\s*\S+"
)
NUMBER_PATTERN = re.compile(r"\b\d+(?:[.,]\d+)*\b")
POLARITY_TOKENS = {
    "no",
    "not",
    "never",
    "without",
    "cannot",
    "can't",
    "shouldn't",
    "avoid",
    "disable",
    "enable",
}
FRESHNESS_TOKENS = {
    "today",
    "current",
    "currently",
    "latest",
    "now",
    "weather",
    "price",
    "prices",
    "news",
    "stock",
    "stocks",
    "forecast",
    "time",
    "date",
}


def _shingles(text: str, ngram_range: tuple[int, int]) -> set[str]:
    tokens = WORD_PATTERN.findall(text[:MAX_QUERY_CHARS].casefold())[:MAX_TOKENS]
    if not tokens:
        return set()
    minimum, maximum = ngram_range
    result = set()
    for size in range(minimum, maximum + 1):
        result.update(
            "\x1f".join(tokens[index:index + size])
            for index in range(max(0, len(tokens) - size + 1))
        )
    return result


def _cache_compatible(query: str, cached_query: str) -> bool:
    query_tokens = set(WORD_PATTERN.findall(query.casefold()))
    cached_tokens = set(WORD_PATTERN.findall(cached_query.casefold()))
    return (
        not query_tokens.intersection(FRESHNESS_TOKENS)
        and not cached_tokens.intersection(FRESHNESS_TOKENS)
        and query_tokens.intersection(POLARITY_TOKENS)
        == cached_tokens.intersection(POLARITY_TOKENS)
        and NUMBER_PATTERN.findall(query) == NUMBER_PATTERN.findall(cached_query)
    )


class SemanticCache:
    """Bounded MinHash/LSH cache with exact Jaccard verification."""

    def __init__(
        self,
        path: Path | None = None,
        *,
        threshold: float = 0.88,
        ttl_seconds: float = 7 * 24 * 60 * 60,
        max_entries: int = MAX_ENTRIES,
        signature_size: int = 64,
        bands: int = 8,
        ngram_range: tuple[int, int] = (1, 2),
    ):
        if not 0 < threshold <= 1:
            raise ValueError("threshold must be greater than 0 and at most 1.")
        if ttl_seconds <= 0 or max_entries < 1:
            raise ValueError("ttl_seconds and max_entries must be positive.")
        if signature_size < 1 or bands < 1 or signature_size % bands:
            raise ValueError("signature_size must be positive and divisible by bands.")
        if ngram_range[0] < 1 or ngram_range[1] < ngram_range[0]:
            raise ValueError("ngram_range must contain positive, ordered sizes.")
        self.path = path or DATA_DIR / "semantic_cache.json"
        self.threshold = threshold
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self.signature_size = signature_size
        self.bands = bands
        self.ngram_range = ngram_range
        self._lock = threading.RLock()
        self._entries: list[dict[str, Any]] = []
        self._buckets: dict[str, set[int]] = defaultdict(set)
        self._shingle_index: dict[str, set[int]] = defaultdict(set)
        self._load()

    def _signature(self, shingles: set[str]) -> tuple[int, ...]:
        if not shingles:
            return ()
        prime = (1 << 61) - 1
        base_hashes = [
            int.from_bytes(
                hashlib.blake2b(shingle.encode("utf-8"), digest_size=8).digest(),
                "big",
            ) % prime
            for shingle in shingles
        ]
        mask = (1 << 64) - 1

        def seeded_hash(value: int, seed: int) -> int:
            value = (value + seed * 0x9E3779B97F4A7C15) & mask
            value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & mask
            value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & mask
            return value ^ (value >> 31)

        return tuple(
            min(
                seeded_hash(value, seed) % prime
                for value in base_hashes
            )
            for seed in range(self.signature_size)
        )

    def _bucket_keys(self, signature: tuple[int, ...]) -> list[str]:
        rows_per_band = self.signature_size // self.bands
        return [
            hashlib.blake2b(
                repr(signature[band * rows_per_band:(band + 1) * rows_per_band]).encode(),
                digest_size=12,
            ).hexdigest()
            for band in range(self.bands)
        ]

    @staticmethod
    def _valid_entry(entry: Any) -> bool:
        return (
            isinstance(entry, dict)
            and isinstance(entry.get("query"), str)
            and isinstance(entry.get("response"), str)
            and isinstance(entry.get("created"), (int, float))
            and isinstance(entry.get("shingles"), list)
            and all(isinstance(item, str) for item in entry["shingles"])
        )

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            LOGGER.warning("Could not load semantic cache: %s", error)
            return
        records = payload.get("entries") if isinstance(payload, dict) else None
        if not isinstance(records, list):
            LOGGER.warning("Ignoring invalid semantic cache contents.")
            return
        signature_size = payload.get("signature_size", self.signature_size)
        bands = payload.get("bands", self.bands)
        ngram_range = payload.get("ngram_range", list(self.ngram_range))
        if (
            isinstance(signature_size, int)
            and isinstance(bands, int)
            and signature_size > 0
            and bands > 0
            and signature_size % bands == 0
            and isinstance(ngram_range, list)
            and len(ngram_range) == 2
            and all(isinstance(value, int) for value in ngram_range)
            and 1 <= ngram_range[0] <= ngram_range[1]
        ):
            self.signature_size = signature_size
            self.bands = bands
            self.ngram_range = (ngram_range[0], ngram_range[1])
        cutoff = time.time() - self.ttl_seconds
        self._entries = [entry for entry in records if self._valid_entry(entry) and entry["created"] >= cutoff]
        self._entries = self._entries[-self.max_entries:]
        for index, entry in enumerate(self._entries):
            shingles = set(entry["shingles"])
            for shingle in shingles:
                self._shingle_index[shingle].add(index)
            for key in self._bucket_keys(self._signature(shingles)):
                self._buckets[key].add(index)

    def _save(self) -> None:
        atomic_write_json(
            self.path,
            {
                "version": 1,
                "signature_size": self.signature_size,
                "bands": self.bands,
                "ngram_range": list(self.ngram_range),
                "entries": self._entries[-self.max_entries:],
            },
        )

    def get(self, query: str, *, min_similarity: float | None = None) -> dict[str, Any] | None:
        shingles = _shingles(query, self.ngram_range)
        if not shingles:
            return None
        threshold = self.threshold if min_similarity is None else min_similarity
        if not 0 < threshold <= 1:
            raise ValueError("min_similarity must be greater than 0 and at most 1.")
        with self._lock:
            signature = self._signature(shingles)
            candidates: set[int] = set()
            for key in self._bucket_keys(signature):
                candidates.update(self._buckets.get(key, ()))
            for shingle in shingles:
                candidates.update(self._shingle_index.get(shingle, ()))
            now = time.time()
            best: tuple[float, dict[str, Any]] | None = None
            for index in candidates:
                entry = self._entries[index]
                if now - entry["created"] > self.ttl_seconds:
                    continue
                if not _cache_compatible(query, entry["query"]):
                    continue
                cached_shingles = set(entry["shingles"])
                union = shingles | cached_shingles
                similarity = len(shingles & cached_shingles) / len(union) if union else 0.0
                if similarity > threshold and (best is None or similarity > best[0]):
                    best = (similarity, entry)
            if best is None:
                return None
            return {
                "response": best[1]["response"],
                "similarity": best[0],
                "cached_query": best[1]["query"],
            }

    def put(self, query: str, response: str, *, metadata: dict[str, Any] | None = None) -> bool:
        clean_query = query.strip()[:2000]
        clean_response = response.strip()[:MAX_RESPONSE_CHARS]
        shingles = _shingles(clean_query, self.ngram_range)
        if (
            not clean_query
            or not clean_response
            or len(shingles) < 2
            or set(WORD_PATTERN.findall(clean_query.casefold())).intersection(FRESHNESS_TOKENS)
            or SECRET_PATTERN.search(clean_query)
            or SECRET_PATTERN.search(clean_response)
        ):
            return False
        entry = {
            "query": clean_query,
            "response": clean_response,
            "created": time.time(),
            "shingles": sorted(shingles),
            "metadata": metadata or {},
        }
        with self._lock:
            self._entries = [
                old for old in self._entries
                if old["query"] != clean_query
                and time.time() - old["created"] <= self.ttl_seconds
            ]
            self._entries.append(entry)
            self._entries = self._entries[-self.max_entries:]
            self._reindex()
            self._save()
        return True

    def _reindex(self) -> None:
        self._buckets = defaultdict(set)
        self._shingle_index = defaultdict(set)
        for index, entry in enumerate(self._entries):
            shingles = set(entry["shingles"])
            signature = self._signature(shingles)
            for shingle in shingles:
                self._shingle_index[shingle].add(index)
            for key in self._bucket_keys(signature):
                self._buckets[key].add(index)


SEMANTIC_CACHE = SemanticCache()
