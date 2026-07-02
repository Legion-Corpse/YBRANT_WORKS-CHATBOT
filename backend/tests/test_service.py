import json
from unittest.mock import patch

from app.chat import conversations, guards, service
from app.chat.openai_client import LLMResult, StreamChunk
from app.schemas import Confidence


def _parse_sse(frames):
    events = []
    for f in frames:
        lines = f.strip().split("\n")
        event = lines[0].split("event: ", 1)[1]
        data = json.loads(lines[1].split("data: ", 1)[1])
        events.append((event, data))
    return events


class FakeLLM:
    def __init__(self, result=None, stream=None):
        self._result = result or LLMResult(text="", citations=[], context="")
        self._stream = stream or []
        self.gen_called = False
        self.stream_called = False

    def generate(self, instructions, message, conversation_id=None):
        self.gen_called = True
        return self._result

    def generate_stream(self, instructions, message, conversation_id=None):
        self.stream_called = True
        yield from self._stream


def _map(**pairs):
    """Patch openai_store.source_for_file to a fixed file_id -> (title, url) map."""
    def lookup(file_id):
        return pairs.get(file_id)
    return lookup


# --- instant paths ----------------------------------------------------------

def test_identity_answered_without_llm():
    with patch.object(service, "get_llm") as llm:
        resp = service.answer("id1", "who are you")
    llm.assert_not_called()
    assert "assistant" in resp.answer.lower()
    assert "cloud technology solutions company" not in resp.answer.lower()
    assert resp.confidence == Confidence.HIGH
    assert resp.sources[0].url.endswith("/about")


def test_identity_stream_single_frame_no_llm():
    with patch.object(service, "get_llm") as llm:
        events = _parse_sse(list(service.answer_stream("id2", "are you a bot")))
    llm.assert_not_called()
    assert [e for e, _ in events] == ["token", "meta", "done"]


# --- buffered answer --------------------------------------------------------

def test_answer_maps_citations_to_sources():
    result = LLMResult(
        text="We offer cloud consulting.",
        citations=[{"file_id": "f1", "filename": "services.txt"}],
        context="We offer cloud consulting to enterprises.",
    )
    with patch.object(service, "get_llm", return_value=FakeLLM(result)):
        with patch.object(service.openai_store, "source_for_file",
                          _map(f1=("Our Services", "https://y.com/services"))):
            resp = service.answer("a1", "what do you offer")
    assert resp.answer == "We offer cloud consulting."
    assert resp.confidence == Confidence.HIGH
    assert resp.sources[0].url == "https://y.com/services"


def test_no_citations_is_low_confidence_with_nav_suggestions():
    result = LLMResult(text="The information isn't available.", citations=[], context="")
    with patch.object(service, "get_llm", return_value=FakeLLM(result)):
        resp = service.answer("a2", "do you sell pizza")
    assert resp.confidence == Confidence.LOW
    assert resp.suggestions == ["Our Services", "About Us", "Contact Us"]


def test_empty_answer_becomes_no_match():
    result = LLMResult(text="", citations=[], context="")
    with patch.object(service, "get_llm", return_value=FakeLLM(result)):
        resp = service.answer("a3", "obscure question")
    assert "couldn't find" in resp.answer
    assert resp.confidence == Confidence.LOW


def test_pricing_question_flows_through_when_docs_have_price():
    # Pricing is no longer special-cased: a price present in the retrieved context
    # is allowed straight through.
    result = LLMResult(
        text="Our workshop costs $500 per seat.",
        citations=[{"file_id": "f1", "filename": "pricing.txt"}],
        context="Workshop pricing: $500 per seat.",
    )
    with patch.object(service, "get_llm", return_value=FakeLLM(result)):
        with patch.object(service.openai_store, "source_for_file",
                          _map(f1=("Pricing", "https://y.com/pricing"))):
            resp = service.answer("a4", "how much is the workshop")
    assert "$500" in resp.answer


def test_invented_figure_replaced_with_neutral_message():
    # Price NOT in context -> guard fires -> neutral replacement (not a pricing
    # deflection).
    result = LLMResult(
        text="Our projects typically cost $25,000.",
        citations=[{"file_id": "f1", "filename": "services.txt"}],
        context="We build custom software for enterprises.",
    )
    with patch.object(service, "get_llm", return_value=FakeLLM(result)):
        resp = service.answer("a5", "tell me about your projects")
    assert "$25,000" not in resp.answer
    assert resp.answer == guards.NOT_IN_DOCS_ANSWER
    assert resp.sources == []


def test_llm_error_returns_graceful_message():
    class BoomLLM:
        def generate(self, *a, **k):
            raise RuntimeError("openai down")

    with patch.object(service, "get_llm", return_value=BoomLLM()):
        resp = service.answer("a6", "anything")
    assert "trouble" in resp.answer.lower()
    assert resp.confidence == Confidence.LOW


# --- cache + conversation ---------------------------------------------------

def test_repeated_first_turn_served_from_cache():
    calls = {"n": 0}

    class CountingLLM:
        def generate(self, *a, **k):
            calls["n"] += 1
            return LLMResult(text="We offer services.", citations=[], context="ctx")

    with patch.object(service, "get_llm", return_value=CountingLLM()):
        first = service.answer("c1", "what do you offer")
        second = service.answer("c2", "what do you offer")  # different session
    assert calls["n"] == 1
    assert first.answer == second.answer


def test_conversation_created_on_first_real_turn():
    result = LLMResult(text="ok", citations=[], context="")
    assert not conversations.has_conversation("sess")
    with patch.object(service, "get_llm", return_value=FakeLLM(result)):
        service.answer("sess", "a real question")
    assert conversations.has_conversation("sess")


def test_metrics_count_cache_miss_then_hit():
    class FakeGen:
        def generate(self, *a, **k):
            return LLMResult(text="We offer services.", citations=[], context="ctx")

    with patch.object(service, "get_llm", return_value=FakeGen()):
        service.answer("m1", "metrics probe query")   # miss -> LLM
        service.answer("m2", "metrics probe query")   # hit
    counters = service.metrics.snapshot()["counters"]
    assert counters["chat_requests"] == 2
    assert counters["cache_misses"] == 1
    assert counters["cache_hits"] == 1
    assert service.metrics.snapshot()["llm_latency_samples"] == 1


# --- streaming --------------------------------------------------------------

def test_stream_clean_answer_then_meta():
    stream = [
        StreamChunk(context="We offer cloud services to enterprises."),
        StreamChunk(text="We offer "),
        StreamChunk(text="cloud services. "),
        StreamChunk(text="Contact us."),
        StreamChunk(citations=[{"file_id": "f1", "filename": "cloud.txt"}], context="We offer cloud services to enterprises."),
    ]
    with patch.object(service, "get_llm", return_value=FakeLLM(stream=stream)):
        with patch.object(service.openai_store, "source_for_file",
                          _map(f1=("Cloud", "https://y.com/cloud"))):
            events = _parse_sse(list(service.answer_stream("s1", "cloud?")))
    tokens = "".join(d["text"] for e, d in events if e == "token")
    assert "We offer cloud services. Contact us." in tokens
    meta = next(d for e, d in events if e == "meta")
    assert meta["sources"][0]["url"] == "https://y.com/cloud"
    assert meta["confidence"] == "high"
    assert events[-1][0] == "done"


def test_stream_figure_guard_blocks_before_leak():
    stream = [
        StreamChunk(context="We build custom software for enterprises."),
        StreamChunk(text="Our projects "),
        StreamChunk(text="typically cost $25,000 "),
        StreamChunk(text="to build. "),
        StreamChunk(citations=[{"file_id": "f1", "filename": "s.txt"}], context="We build custom software for enterprises."),
    ]
    with patch.object(service, "get_llm", return_value=FakeLLM(stream=stream)):
        events = _parse_sse(list(service.answer_stream("s2", "tell me about your projects")))
    for e, d in events:
        if e == "token":
            assert "$25,000" not in d["text"]
    replace = [d for e, d in events if e == "replace"]
    assert replace and replace[0]["answer"] == guards.NOT_IN_DOCS_ANSWER


class _FailStreamLLM:
    def __init__(self, buffered):
        self._buffered = buffered
        self.generate_called = False

    def generate_stream(self, *a, **k):
        raise RuntimeError("read timed out")
        yield  # pragma: no cover

    def generate(self, *a, **k):
        self.generate_called = True
        return self._buffered


def test_stream_falls_back_to_buffered_before_any_token():
    buffered = LLMResult(text="We offer cloud consulting.", citations=[{"file_id": "f1", "filename": "c.txt"}], context="cloud consulting")
    llm = _FailStreamLLM(buffered)
    with patch.object(service, "get_llm", return_value=llm):
        with patch.object(service.openai_store, "source_for_file",
                          _map(f1=("Cloud", "https://y.com/cloud"))):
            events = _parse_sse(list(service.answer_stream("s3", "cloud?")))
    assert llm.generate_called
    assert not any(e == "error" for e, _ in events)
    replace = [d for e, d in events if e == "replace"]
    assert replace and replace[0]["answer"] == "We offer cloud consulting."
    assert events[-1][0] == "done"


def test_stream_errors_when_buffered_fallback_also_fails():
    class _AllFail:
        def generate_stream(self, *a, **k):
            raise RuntimeError("stream down")
            yield  # pragma: no cover

        def generate(self, *a, **k):
            raise RuntimeError("buffered down")

    with patch.object(service, "get_llm", return_value=_AllFail()):
        events = _parse_sse(list(service.answer_stream("s4", "cloud?")))
    assert any(e == "error" for e, _ in events)
    assert events[-1][0] == "done"
