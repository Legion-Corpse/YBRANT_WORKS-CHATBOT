"""In-process metrics counters for production observability.

The chat route runs in the AnyIO worker-thread pool, so counters are touched
from many threads at once; a single lock guards every mutation (same pattern as
``app/chat/cache.py`` and ``app/chat/conversations.py``). These are *process-local*
and reset on restart — sufficient for a single-worker deployment at this scale
(scrape ``/api/metrics`` periodically; export to a TSDB later if needed).

Latency is tracked as a running sum + count so ``snapshot`` can report a mean
without retaining every sample.
"""

from __future__ import annotations

import threading

_lock = threading.Lock()
_counters: dict[str, int] = {}
_latency_sum = 0.0
_latency_count = 0
# Running token totals — tokens are what OpenAI actually bills, so this is the
# real cost signal (call counts alone don't say how close to budget we are).
_input_tokens = 0
_output_tokens = 0
_token_calls = 0


def incr(name: str, amount: int = 1) -> None:
    with _lock:
        _counters[name] = _counters.get(name, 0) + amount


def observe_latency(seconds: float) -> None:
    """Record one LLM call latency (seconds)."""
    global _latency_sum, _latency_count
    with _lock:
        _latency_sum += seconds
        _latency_count += 1


def observe_tokens(input_tokens: int, output_tokens: int) -> None:
    """Record one LLM call's token usage."""
    global _input_tokens, _output_tokens, _token_calls
    with _lock:
        _input_tokens += input_tokens
        _output_tokens += output_tokens
        _token_calls += 1


def snapshot() -> dict:
    """Point-in-time copy of all metrics. Safe to serialize as JSON."""
    with _lock:
        counters = dict(_counters)
        mean = (_latency_sum / _latency_count) if _latency_count else 0.0
        avg_tokens = (
            (_input_tokens + _output_tokens) / _token_calls if _token_calls else 0.0
        )
        return {
            "counters": counters,
            "llm_latency_seconds_avg": round(mean, 3),
            "llm_latency_samples": _latency_count,
            "llm_input_tokens_total": _input_tokens,
            "llm_output_tokens_total": _output_tokens,
            "llm_tokens_avg_per_call": round(avg_tokens, 1),
        }


def reset() -> None:
    """Clear all metrics. Test helper / manual reset."""
    global _latency_sum, _latency_count, _input_tokens, _output_tokens, _token_calls
    with _lock:
        _counters.clear()
        _latency_sum = 0.0
        _latency_count = 0
        _input_tokens = 0
        _output_tokens = 0
        _token_calls = 0
