from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections import OrderedDict
from pathlib import Path

from app.config import settings
from app.schemas import ChatResponse

logger = logging.getLogger(__name__)

# key -> (stored_at_epoch, ChatResponse). stored_at is WALL-CLOCK time (time.time)
# rather than monotonic so the TTL is still meaningful after a restart that loads
# the persisted cache from disk.
_store: OrderedDict[str, tuple[float, ChatResponse]] = OrderedDict()
# The chat route runs in the AnyIO worker-thread pool, so get/set can be called
# from many threads at once; guard the non-atomic check-then-act sequences.
_lock = threading.Lock()


def _normalize(message: str) -> str:
    return re.sub(r"\s+", " ", message.strip().lower())


def get(message: str) -> ChatResponse | None:
    if not settings.cache_enabled:
        return None
    key = _normalize(message)
    with _lock:
        entry = _store.get(key)
        if entry is None:
            return None
        stored_at, response = entry
        if time.time() - stored_at > settings.cache_ttl_seconds:
            _store.pop(key, None)
            return None
        _store.move_to_end(key)
        return response


def set(message: str, response: ChatResponse) -> None:
    if not settings.cache_enabled:
        return
    key = _normalize(message)
    with _lock:
        _store[key] = (time.time(), response)
        _store.move_to_end(key)
        while len(_store) > settings.cache_max_entries:
            _store.popitem(last=False)
        snapshot = list(_store.items())
    _persist(snapshot)


def clear() -> None:
    with _lock:
        _store.clear()
    if settings.cache_persist:
        try:
            Path(settings.cache_path).unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not delete cache file %s", settings.cache_path)


# --- disk persistence (best-effort; the cache is an optimization, never
# correctness, so any file error is logged and swallowed) -------------------

def _persist(snapshot: list[tuple[str, tuple[float, ChatResponse]]]) -> None:
    if not settings.cache_persist:
        return
    try:
        payload = {k: [ts, resp.model_dump()] for k, (ts, resp) in snapshot}
        path = Path(settings.cache_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except (OSError, TypeError) as exc:
        logger.warning("Could not persist answer cache: %s", exc)


def load() -> None:
    """Populate the in-memory cache from disk at startup, dropping expired
    entries. Best-effort: a missing or corrupt file yields an empty cache."""
    if not settings.cache_persist:
        return
    try:
        raw = json.loads(Path(settings.cache_path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return
    except OSError as exc:
        logger.warning("Could not read answer cache: %s", exc)
        return
    now = time.time()
    with _lock:
        _store.clear()
        for key, item in raw.items():
            try:
                ts, payload = item
                if now - ts > settings.cache_ttl_seconds:
                    continue
                _store[key] = (ts, ChatResponse(**payload))
            except (ValueError, TypeError):
                continue


# Load any persisted cache on import so a restart keeps its hits.
load()
