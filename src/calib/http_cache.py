"""A polite, cached JSON client.

Every response is written to ``data/raw/<source>/<route>/<hash>.json`` before it is used, and a cached
response is never re-requested. That makes ingestion resumable (kill it, rerun it) and idempotent
(the same request always maps to the same file).
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from calib.config import RAW_DIR


class RetryableHTTPError(Exception):
    def __init__(self, status: int, retry_after: float | None) -> None:
        super().__init__(f"HTTP {status}")
        self.status = status
        self.retry_after = retry_after


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, (RetryableHTTPError, httpx.TransportError))


def cache_key(path: str, params: dict[str, Any] | None) -> str:
    """Stable key for a request: route plus sorted query params."""
    items = sorted((params or {}).items())
    blob = path + "?" + "&".join(f"{k}={v}" for k, v in items)
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


def route_slug(path: str) -> str:
    """Directory name for a route, with ids collapsed so the tree stays browsable."""
    parts = [p for p in path.strip("/").split("/") if p]
    keep = [p for p in parts if re.fullmatch(r"[a-z\-_]+", p)]
    return "_".join(keep) or "root"


class CachedClient:
    def __init__(
        self,
        source: str,
        base_url: str,
        requests_per_second: float = 5.0,
        raw_dir: Path = RAW_DIR,
    ) -> None:
        self.source = source
        self.base_url = base_url.rstrip("/")
        self.min_interval = 1.0 / requests_per_second
        self.cache_dir = raw_dir / source
        self._last_request = 0.0
        self._http = httpx.Client(timeout=30.0, headers={"User-Agent": "calib-research/0.1"})
        self.hits = 0
        self.misses = 0

    def cache_path(self, path: str, params: dict[str, Any] | None = None) -> Path:
        return self.cache_dir / route_slug(path) / f"{cache_key(path, params)}.json"

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        target = self.cache_path(path, params)
        if target.exists():
            self.hits += 1
            return json.loads(target.read_text())["response"]
        self.misses += 1
        data = self._fetch(path, params)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps({"path": path, "params": params, "response": data}))
        tmp.replace(target)  # atomic: a crash never leaves a half-written cache file
        return data

    @retry(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential(multiplier=1, min=1, max=60),
        stop=stop_after_attempt(8),
        reraise=True,
    )
    def _fetch(self, path: str, params: dict[str, Any] | None) -> Any:
        wait = self.min_interval - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()
        resp = self._http.get(self.base_url + path, params=params)
        if resp.status_code == 429 or resp.status_code >= 500:
            retry_after = resp.headers.get("Retry-After")
            delay = float(retry_after) if retry_after and retry_after.isdigit() else None
            if delay:
                time.sleep(min(delay, 120))
            raise RetryableHTTPError(resp.status_code, delay)
        resp.raise_for_status()
        return resp.json()
