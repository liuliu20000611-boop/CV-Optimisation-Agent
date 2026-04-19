"""Short-TTL in-memory cache for identical resume+JD analysis (single-process only)."""

from __future__ import annotations

import hashlib
import time
from threading import Lock

from app.schemas import AnalysisResult

_lock = Lock()
_store: dict[str, tuple[float, AnalysisResult]] = {}


def _key(resume: str, jd: str) -> str:
    h = hashlib.sha256()
    h.update(resume.encode("utf-8"))
    h.update(b"\n---\n")
    h.update(jd.encode("utf-8"))
    return h.hexdigest()


def get_cached(resume: str, jd: str, ttl_seconds: float) -> AnalysisResult | None:
    k = _key(resume, jd)
    now = time.monotonic()
    with _lock:
        item = _store.get(k)
        if not item:
            return None
        ts, value = item
        if now - ts > ttl_seconds:
            del _store[k]
            return None
        return value.model_copy(deep=True)


def set_cached(resume: str, jd: str, result: AnalysisResult) -> None:
    k = _key(resume, jd)
    with _lock:
        _store[k] = (time.monotonic(), result.model_copy(deep=True))


def clear_all() -> None:
    """Clear cache (e.g. between tests)."""
    with _lock:
        _store.clear()


def clear_expired(ttl_seconds: float, max_entries: int = 5000) -> None:
    """Best-effort cleanup to cap memory (called occasionally)."""
    now = time.monotonic()
    with _lock:
        if len(_store) <= max_entries:
            return
        dead = [k for k, (ts, _) in _store.items() if now - ts > ttl_seconds]
        for k in dead:
            _store.pop(k, None)
