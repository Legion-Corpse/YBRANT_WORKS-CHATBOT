from unittest.mock import patch

from app import quota
from app.chat import service
from app.chat.openai_client import LLMResult
from app.config import settings
from app.schemas import Confidence


def test_allow_stops_at_cap(monkeypatch):
    monkeypatch.setattr(settings, "daily_request_cap", 3)
    quota.reset()
    assert [quota.allow() for _ in range(5)] == [True, True, True, False, False]


def test_daily_rollover_resets(monkeypatch):
    import app.quota as q
    monkeypatch.setattr(settings, "daily_request_cap", 1)
    quota.reset()
    assert quota.allow() is True
    assert quota.allow() is False
    # New UTC day -> counter resets.
    monkeypatch.setattr(q, "_day", None)
    assert quota.allow() is True


class _CountingLLM:
    def __init__(self):
        self.calls = 0

    def generate(self, *a, **k):
        self.calls += 1
        return LLMResult(text="answer", citations=[], context="ctx")


def test_service_returns_cap_answer_without_llm(monkeypatch):
    monkeypatch.setattr(settings, "daily_request_cap", 1)
    quota.reset()
    llm = _CountingLLM()
    with patch.object(service, "get_llm", return_value=llm):
        first = service.answer("q1", "a real question")
        second = service.answer("q2", "another real question")
    assert first.answer == "answer"
    assert second.answer == service.CAP_ANSWER
    assert second.confidence == Confidence.LOW
    assert llm.calls == 1  # the capped turn never reached the LLM
    assert service.metrics.snapshot()["counters"]["daily_cap"] == 1


def test_stream_returns_cap_answer(monkeypatch):
    monkeypatch.setattr(settings, "daily_request_cap", 0)
    quota.reset()
    with patch.object(service, "get_llm") as llm:
        frames = list(service.answer_stream("s1", "a real question"))
    llm.assert_not_called()
    kinds = [f.split("\n")[0].replace("event: ", "") for f in frames]
    assert kinds == ["token", "meta", "done"]
    assert service.CAP_ANSWER in frames[0]
