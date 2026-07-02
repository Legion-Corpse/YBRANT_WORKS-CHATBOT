import threading

from app import metrics


def setup_function():
    metrics.reset()


def test_incr_and_snapshot():
    metrics.incr("chat_requests")
    metrics.incr("chat_requests")
    metrics.incr("cache_hits", 3)
    snap = metrics.snapshot()
    assert snap["counters"]["chat_requests"] == 2
    assert snap["counters"]["cache_hits"] == 3


def test_latency_average():
    metrics.observe_latency(0.2)
    metrics.observe_latency(0.4)
    snap = metrics.snapshot()
    assert snap["llm_latency_samples"] == 2
    assert snap["llm_latency_seconds_avg"] == 0.3


def test_snapshot_is_a_copy():
    metrics.incr("x")
    snap = metrics.snapshot()
    snap["counters"]["x"] = 999
    assert metrics.snapshot()["counters"]["x"] == 1


def test_concurrent_incr_is_thread_safe():
    def worker():
        for _ in range(1000):
            metrics.incr("hits")

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert metrics.snapshot()["counters"]["hits"] == 8000
