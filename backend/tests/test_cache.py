import pytest

from app.chat import cache
from app.config import settings
from app.schemas import ChatResponse, Confidence


@pytest.fixture(autouse=True)
def _clean_cache():
    cache.clear()
    yield
    cache.clear()


def _resp(answer="hi"):
    return ChatResponse(answer=answer, confidence=Confidence.HIGH)


def test_set_then_get_hit():
    cache.set("What services?", _resp("services answer"))
    got = cache.get("What services?")
    assert got is not None
    assert got.answer == "services answer"


def test_miss_returns_none():
    assert cache.get("never stored") is None


def test_key_is_normalized():
    cache.set("  How  CAN i Contact you?  ", _resp("contact"))
    assert cache.get("how can i contact you?").answer == "contact"


def test_ttl_expiry(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(cache.time, "time", lambda: clock["t"])
    cache.set("q", _resp())
    clock["t"] = 1000.0 + settings.cache_ttl_seconds + 1
    assert cache.get("q") is None


def test_lru_eviction(monkeypatch):
    monkeypatch.setattr(settings, "cache_max_entries", 2)
    cache.set("a", _resp("a"))
    cache.set("b", _resp("b"))
    cache.set("c", _resp("c"))  # evicts oldest ("a")
    assert cache.get("a") is None
    assert cache.get("b") is not None
    assert cache.get("c") is not None


def test_disabled_is_noop(monkeypatch):
    monkeypatch.setattr(settings, "cache_enabled", False)
    cache.set("q", _resp())
    assert cache.get("q") is None


def test_persist_survives_restart(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "cache_persist", True)
    monkeypatch.setattr(settings, "cache_path", str(tmp_path / "c.json"))
    cache.set("what services", _resp("services"))
    # Simulate a restart: drop the in-memory store but keep the file on disk.
    cache._store.clear()
    assert cache.get("what services") is None
    cache.load()
    assert cache.get("what services").answer == "services"


def test_persist_load_drops_expired(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "cache_persist", True)
    monkeypatch.setattr(settings, "cache_path", str(tmp_path / "c.json"))
    clock = {"t": 1000.0}
    monkeypatch.setattr(cache.time, "time", lambda: clock["t"])
    cache.set("q", _resp())
    cache._store.clear()
    clock["t"] = 1000.0 + settings.cache_ttl_seconds + 1
    cache.load()  # expired entry must not be reloaded
    assert cache.get("q") is None
