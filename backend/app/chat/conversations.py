"""Maps a widget ``session_id`` to an OpenAI conversation id.

OpenAI's Conversations API holds the running turn history server-side, so we
only need to remember which conversation belongs to which visitor session. That
mapping is a small in-process store, bounded exactly like the old turn-history
store:

* total live sessions capped (LRU eviction at ``max_sessions``),
* sessions idle longer than ``session_ttl_seconds`` dropped (lazy sweep on touch).

The chat route runs across many worker threads, so every access is locked.
Single-worker by design (the map, the cache, and the rate limiter are all
in-process).
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict

from app.config import settings

# session_id -> (last_access_monotonic, conversation_id)
_store: "OrderedDict[str, tuple[float, str]]" = OrderedDict()
_lock = threading.Lock()


def _expired(stored_at: float, now: float) -> bool:
    return now - stored_at > settings.session_ttl_seconds


def _sweep_locked(now: float) -> None:
    stale = [sid for sid, (ts, _) in _store.items() if _expired(ts, now)]
    for sid in stale:
        _store.pop(sid, None)


def has_conversation(session_id: str) -> bool:
    """True if this session already has a (non-expired) conversation — i.e. this
    is a follow-up turn, not the first. Drives cacheability in the service."""
    now = time.monotonic()
    with _lock:
        _sweep_locked(now)
        return session_id in _store


def get_or_create(session_id: str) -> str:
    """Return the OpenAI conversation id for this session, creating one on first
    use. The create call hits OpenAI, so it runs outside the lock."""
    now = time.monotonic()
    with _lock:
        _sweep_locked(now)
        entry = _store.get(session_id)
        if entry is not None:
            _store[session_id] = (now, entry[1])
            _store.move_to_end(session_id)
            return entry[1]

    from app.chat.openai_client import get_client

    conversation_id = get_client().conversations.create().id

    with _lock:
        # Re-check: a concurrent turn for the same session may have created one
        # while we were calling OpenAI. Keep the existing id and drop ours.
        entry = _store.get(session_id)
        if entry is not None:
            _store[session_id] = (now, entry[1])
            _store.move_to_end(session_id)
            return entry[1]
        _store[session_id] = (now, conversation_id)
        _store.move_to_end(session_id)
        while len(_store) > settings.max_sessions:
            _store.popitem(last=False)
    return conversation_id


def clear() -> None:
    with _lock:
        _store.clear()
