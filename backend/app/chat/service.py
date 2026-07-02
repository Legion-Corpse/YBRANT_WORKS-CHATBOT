from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Iterator

from app import metrics, quota
from app.config import settings
from app.chat import cache, conversations, guards
from app.chat.openai_client import get_llm
from app.chat.prompts import INSTRUCTIONS
from app.ingestion import openai_store
from app.schemas import ChatResponse, Confidence, Source

logger = logging.getLogger(__name__)

NAV_PAGES = [
    ("Our Services", "/services"),
    ("About Us", "/about"),
    ("Contact Us", "/contact"),
    ("Products", "/product"),
    ("Blogs", "/blogs"),
    ("Careers", "/career"),
]

NO_MATCH_ANSWER = (
    "I couldn't find information about that in the YbrantWorks documents I can "
    "access. I can help with our services, company background, blogs, careers, or "
    f"how to get in touch. You can also email us at {settings.contact_email}."
)

ERROR_ANSWER = (
    "Sorry, I'm having trouble answering right now. Please try again in a moment, "
    f"or reach us directly at {settings.contact_email}."
)

CAP_ANSWER = (
    "We've reached today's limit for automated answers. Please try again tomorrow, "
    f"or reach us directly at {settings.contact_email} and the team will help."
)


def _cap_response() -> ChatResponse:
    return ChatResponse(
        answer=CAP_ANSWER, sources=[], confidence=Confidence.LOW, suggestions=[]
    )


def _short_title(title: str) -> str:
    short = re.split(r"\s*[|–-]\s+", title, maxsplit=1)[0].strip()
    if len(short) > 42:
        short = short[:42].rsplit(" ", 1)[0] + "…"
    return short


def _sources_from_citations(citations: list[dict]) -> list[Source]:
    """Turn OpenAI file citations into visitor-facing sources. The file id is
    mapped back to the real page title/URL via the ingestion metadata; if a file
    isn't in the map (e.g. uploaded outside this app) we fall back to its
    filename."""
    seen: set[str] = set()
    sources: list[Source] = []
    for cite in citations:
        meta = openai_store.source_for_file(cite.get("file_id", ""))
        if meta:
            title, url = meta
        else:
            title, url = cite.get("filename", ""), ""
        key = url or title
        if key and key not in seen:
            seen.add(key)
            sources.append(Source(title=_short_title(title or url), url=url))
    return sources[:3]


def _suggestions_from(sources: list[Source]) -> list[str]:
    titles = [_short_title(s.title) for s in sources if s.title]
    if titles:
        return titles[:3]
    return [label for label, _ in NAV_PAGES[:3]]


# --- Shared pre-LLM pipeline -----------------------------------------------
#
# answer() (buffered) and answer_stream() (SSE) apply IDENTICAL logic up to the
# OpenAI call — cache lookup and the identity guard. Keeping it in one place stops
# the two endpoints from diverging. Retrieval, confidence banding, the pricing
# pre-guard, and the free-tier throttle are all gone: OpenAI's file_search does
# retrieval server-side, and the conversation id carries multi-turn context.


@dataclass
class _Prepared:
    message: str
    cacheable: bool


def _prepare_turn(session_id: str, message: str) -> ChatResponse | _Prepared:
    metrics.incr("chat_requests")
    # First turn only (no OpenAI conversation yet) is cacheable — a follow-up's
    # meaning depends on the conversation before it.
    cacheable = not conversations.has_conversation(session_id)
    if cacheable:
        cached = cache.get(message)
        if cached is not None:
            metrics.incr("cache_hits")
            return cached
        metrics.incr("cache_misses")

    if guards.has_identity_intent(message):
        metrics.incr("guard_identity")
        resp = ChatResponse(
            answer=guards.IDENTITY_ANSWER,
            sources=[Source(title="About Us", url=f"{settings.site_base_url}/about")],
            confidence=Confidence.HIGH,
            suggestions=["Our Services", "Contact Us", "Careers"],
        )
        return _finalize_instant(message, resp, cacheable)

    return _Prepared(message=message, cacheable=cacheable)


def _finalize_instant(message: str, resp: ChatResponse, cacheable: bool) -> ChatResponse:
    if cacheable:
        cache.set(message, resp)
    return resp


def answer(session_id: str, message: str) -> ChatResponse:
    prepared = _prepare_turn(session_id, message)
    if isinstance(prepared, ChatResponse):
        return prepared

    if not quota.allow():
        metrics.incr("daily_cap")
        logger.warning("Daily request cap reached; returning cap fallback")
        return _cap_response()

    try:
        conversation_id = conversations.get_or_create(session_id)
        started = time.monotonic()
        result = get_llm().generate(INSTRUCTIONS, prepared.message, conversation_id)
        metrics.observe_latency(time.monotonic() - started)
        if result.usage:
            metrics.observe_tokens(
                result.usage.get("input_tokens", 0), result.usage.get("output_tokens", 0)
            )
    except Exception:
        metrics.incr("llm_errors")
        logger.exception("OpenAI generation failed")
        return ChatResponse(
            answer=ERROR_ANSWER, sources=[], confidence=Confidence.LOW, suggestions=[]
        )

    text = result.text
    citations = result.citations
    if not text:
        text = NO_MATCH_ANSWER
        citations = []
    elif guards.answer_invents_figures(text, result.context):
        metrics.incr("guard_figure")
        logger.warning("Blocked answer containing invented figures")
        text = guards.NOT_IN_DOCS_ANSWER
        citations = []

    sources = _sources_from_citations(citations)
    confidence = Confidence.HIGH if sources else Confidence.LOW
    response = ChatResponse(
        answer=text,
        sources=sources,
        confidence=confidence,
        suggestions=_suggestions_from(sources),
    )
    if prepared.cacheable:
        cache.set(message, response)
    return response


# --- Streaming (SSE) -------------------------------------------------------
#
# Frame protocol (unchanged, so the widget needs no change):
#   event: token   data: {"text": "..."}   incremental answer text
#   event: meta    data: {"sources":[...], "suggestions":[...], "confidence":"high"}
#   event: replace data: {"answer": "..."}  discard streamed text, show this
#   event: done    data: {}
#   event: error   data: {"answer": "..."}
#
# There is no throttle slot to release, so this is a plain generator (no producer
# thread needed). The figure guard runs on each flushed sentence/line against the
# retrieved-context text (which file_search emits before the answer text); a final
# full-answer check covers the case where the context only arrives at completion.

# A sentence terminator (optionally a closing quote/bracket) followed by
# whitespace, OR a newline. Newlines matter because the bot favors hyphen-bulleted
# lists whose items have no sentence terminator — without a newline boundary a
# whole list would buffer until its final period and defeat streaming.
_FLUSH_BOUNDARY = re.compile(r"(?:[.!?][\"')\]]?(?=\s))|\n")


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _meta_payload(
    sources: list[Source], suggestions: list[str], confidence: Confidence
) -> dict:
    return {
        "sources": [s.model_dump() for s in sources],
        "suggestions": suggestions,
        "confidence": confidence.value,
    }


def _flush_sentences(buffer: str) -> tuple[str, str]:
    """Split off the run of complete sentences/lines; keep the trailing fragment."""
    matches = list(_FLUSH_BOUNDARY.finditer(buffer))
    if not matches:
        return "", buffer
    end = matches[-1].end()
    return buffer[:end], buffer[end:]


def _emit_full(resp: ChatResponse) -> Iterator[str]:
    """Stream a fully-formed (instant) response as a single token + meta + done."""
    if resp.answer:
        yield _sse("token", {"text": resp.answer})
    yield _sse("meta", _meta_payload(resp.sources, resp.suggestions, resp.confidence))
    yield _sse("done", {})


def answer_stream(session_id: str, message: str) -> Iterator[str]:
    """SSE variant of :func:`answer`. Shares the pre-LLM pipeline exactly, then
    streams the OpenAI answer sentence-by-sentence through the figure guard."""
    prepared = _prepare_turn(session_id, message)
    if isinstance(prepared, ChatResponse):
        yield from _emit_full(prepared)
        return

    if not quota.allow():
        metrics.incr("daily_cap")
        logger.warning("Daily request cap reached; returning cap fallback")
        yield from _emit_full(_cap_response())
        return

    context_text = ""
    citations: list[dict] = []
    buffer = ""
    emitted = ""
    blocked = False
    recovered = None
    conversation_id: str | None = None

    try:
        conversation_id = conversations.get_or_create(session_id)
        started = time.monotonic()
        for chunk in get_llm().generate_stream(INSTRUCTIONS, prepared.message, conversation_id):
            if chunk.context:
                context_text = chunk.context
            if chunk.citations is not None:
                citations = chunk.citations
            if chunk.usage:
                metrics.observe_tokens(
                    chunk.usage.get("input_tokens", 0), chunk.usage.get("output_tokens", 0)
                )
            if chunk.text:
                buffer += chunk.text
                complete, buffer = _flush_sentences(buffer)
                if complete:
                    if context_text and guards.answer_invents_figures(complete, context_text):
                        blocked = True
                        break
                    emitted += complete
                    yield _sse("token", {"text": complete})
        if not blocked and buffer.strip():
            if context_text and guards.answer_invents_figures(buffer, context_text):
                blocked = True
            else:
                emitted += buffer
                yield _sse("token", {"text": buffer})
        metrics.observe_latency(time.monotonic() - started)
    except Exception:
        metrics.incr("llm_errors")
        logger.exception("OpenAI stream failed")
        # Tokens already on screen can't be transparently retried (would double
        # the answer). Nothing emitted yet → fall back to the buffered generate()
        # so the stream degrades like /api/chat.
        if emitted.strip():
            yield _sse("error", {"answer": ERROR_ANSWER})
            yield _sse("done", {})
            return
        try:
            recovered = get_llm().generate(INSTRUCTIONS, prepared.message, conversation_id)
            if recovered.usage:
                metrics.observe_tokens(
                    recovered.usage.get("input_tokens", 0),
                    recovered.usage.get("output_tokens", 0),
                )
        except Exception:
            logger.exception("Buffered fallback after stream failure failed")
            yield _sse("error", {"answer": ERROR_ANSWER})
            yield _sse("done", {})
            return

    if recovered is not None:
        text = recovered.text
        if text and guards.answer_invents_figures(text, recovered.context):
            metrics.incr("guard_figure")
            logger.warning("Blocked streamed answer containing invented figures")
            final_answer, citations = guards.NOT_IN_DOCS_ANSWER, []
        else:
            final_answer = text.strip() or NO_MATCH_ANSWER
            citations = recovered.citations if text.strip() else []
        yield _sse("replace", {"answer": final_answer})
    else:
        final_text = emitted.strip()
        # Final full-answer guard: the retrieved context may only have been
        # captured at completion, in which case the per-block checks were skipped.
        if not blocked and final_text and context_text and guards.answer_invents_figures(
            final_text, context_text
        ):
            blocked = True
        if blocked:
            metrics.incr("guard_figure")
            logger.warning("Blocked streamed answer containing invented figures")
            final_answer, citations = guards.NOT_IN_DOCS_ANSWER, []
            yield _sse("replace", {"answer": final_answer})
        elif not final_text:
            final_answer, citations = NO_MATCH_ANSWER, []
            yield _sse("replace", {"answer": final_answer})
        else:
            final_answer = final_text

    sources = _sources_from_citations(citations)
    confidence = Confidence.HIGH if sources else Confidence.LOW
    resp = ChatResponse(
        answer=final_answer,
        sources=sources,
        confidence=confidence,
        suggestions=_suggestions_from(sources),
    )
    if prepared.cacheable:
        cache.set(message, resp)
    yield _sse("meta", _meta_payload(resp.sources, resp.suggestions, resp.confidence))
    yield _sse("done", {})
