"""In-process daily request cap — a hard ceiling on paid OpenAI chat calls.

The per-IP rate limit (slowapi) stops a single abuser; this caps the *aggregate*
number of real LLM calls per day so a distributed burst can't run up an unbounded
bill. Single-worker by design (like the cache and metrics), so an in-process
counter is a consistent global count.

The counter is keyed on the current UTC date, so it resets naturally at the day
boundary (and on restart). Only *real* LLM calls are counted — instant paths
(cache hit, identity guard) call neither the LLM nor this.
"""

from __future__ import annotations

import threading
from datetime import date, datetime, timezone

from app.config import settings

_lock = threading.Lock()
_day: date | None = None
_count = 0


def allow() -> bool:
    """Reserve one call against today's cap. Returns True if under the cap (and
    increments), False once the cap is reached."""
    global _day, _count
    today = datetime.now(timezone.utc).date()
    with _lock:
        if today != _day:
            _day = today
            _count = 0
        if _count >= settings.daily_request_cap:
            return False
        _count += 1
        return True


def reset() -> None:
    """Test helper / manual reset."""
    global _day, _count
    with _lock:
        _day = None
        _count = 0
