"""OpenAI Responses API client with hosted file_search retrieval.

One `responses.create` call does both retrieval (the `file_search` tool over the
configured vector store) and generation, so there is no local embed/rerank step.
The conversation id (when present) lets OpenAI keep multi-turn context
server-side.

Returned to the service:
- `text`     — the answer.
- `citations`— `{file_id, filename}` per cited chunk; the service maps these to
               real page titles/URLs via the ingestion metadata map.
- `context`  — the concatenated text of the retrieved chunks (requested via
               `include=["file_search_call.results"]`), used by the invented-figure
               guard to check the answer's numbers against the source text.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Iterator

from app.config import settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_client():
    """Process-wide OpenAI client. Built-in retry/backoff on transient 429/5xx
    plus a hard per-call timeout so one hung call can't pin a worker thread."""
    from openai import OpenAI

    return OpenAI(
        api_key=settings.openai_api_key,
        timeout=settings.openai_timeout_seconds,
        max_retries=settings.openai_max_retries,
    )


@dataclass
class LLMResult:
    text: str
    citations: list[dict] = field(default_factory=list)
    context: str = ""
    usage: dict | None = None


@dataclass
class StreamChunk:
    """One streaming event, normalized. Any field may be empty/None on a given
    chunk: `text` carries an incremental delta; `context` is set once the
    file_search results arrive (before the text); `citations`/`usage` are set at
    completion."""

    text: str = ""
    context: str | None = None
    citations: list[dict] | None = None
    usage: dict | None = None


def _tools() -> list[dict]:
    return [
        {
            "type": "file_search",
            "vector_store_ids": [settings.openai_vector_store_id],
            "max_num_results": settings.openai_max_num_results,
            "ranking_options": {"score_threshold": settings.openai_score_threshold},
        }
    ]


def _extract_citations(response) -> list[dict]:
    """File citations from the message output's annotations, deduped by file_id."""
    seen: set[str] = set()
    citations: list[dict] = []
    for item in getattr(response, "output", None) or []:
        if getattr(item, "type", None) != "message":
            continue
        for block in getattr(item, "content", None) or []:
            if getattr(block, "type", None) != "output_text":
                continue
            for ann in getattr(block, "annotations", None) or []:
                if getattr(ann, "type", None) != "file_citation":
                    continue
                file_id = getattr(ann, "file_id", "") or ""
                key = file_id or getattr(ann, "filename", "")
                if key and key not in seen:
                    seen.add(key)
                    citations.append(
                        {"file_id": file_id, "filename": getattr(ann, "filename", "")}
                    )
    return citations


def _results_text_from_item(item) -> str:
    """Concatenate the retrieved chunk text from a file_search_call output item
    (populated because we pass include=['file_search_call.results'])."""
    parts: list[str] = []
    for r in getattr(item, "results", None) or []:
        text = getattr(r, "text", "") or ""
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def _extract_context(response) -> str:
    parts: list[str] = []
    for item in getattr(response, "output", None) or []:
        if getattr(item, "type", None) == "file_search_call":
            text = _results_text_from_item(item)
            if text:
                parts.append(text)
    return "\n\n".join(parts)


def _extract_usage(response) -> dict | None:
    """Token usage for cost tracking (what OpenAI actually bills), or None if the
    response carries no usage block."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    return {
        "input_tokens": getattr(usage, "input_tokens", 0) or 0,
        "output_tokens": getattr(usage, "output_tokens", 0) or 0,
    }


class OpenAIClient:
    def _base_kwargs(self, instructions: str, user_input: str, conversation_id: str | None) -> dict:
        kwargs = {
            "model": settings.openai_model,
            "instructions": instructions,
            "input": user_input,
            "temperature": settings.openai_temperature,
            "max_output_tokens": settings.openai_max_output_tokens,
            "tools": _tools(),
            "include": ["file_search_call.results"],
        }
        if conversation_id:
            kwargs["conversation"] = conversation_id
        return kwargs

    def generate(
        self, instructions: str, user_input: str, conversation_id: str | None = None
    ) -> LLMResult:
        response = get_client().responses.create(
            **self._base_kwargs(instructions, user_input, conversation_id)
        )
        return LLMResult(
            text=(getattr(response, "output_text", "") or "").strip(),
            citations=_extract_citations(response),
            context=_extract_context(response),
            usage=_extract_usage(response),
        )

    def generate_stream(
        self, instructions: str, user_input: str, conversation_id: str | None = None
    ) -> Iterator[StreamChunk]:
        """Yield normalized StreamChunks. Text deltas stream as they arrive; the
        retrieved-context chunk is emitted when the file_search results complete
        (before any text), and citations are emitted at completion."""
        stream = get_client().responses.create(
            stream=True, **self._base_kwargs(instructions, user_input, conversation_id)
        )
        for event in stream:
            etype = getattr(event, "type", "") or ""
            if etype == "response.output_text.delta":
                delta = getattr(event, "delta", "") or ""
                if delta:
                    yield StreamChunk(text=delta)
            elif etype == "response.output_item.done":
                item = getattr(event, "item", None)
                if item is not None and getattr(item, "type", None) == "file_search_call":
                    context = _results_text_from_item(item)
                    if context:
                        yield StreamChunk(context=context)
            elif etype == "response.completed":
                response = getattr(event, "response", None)
                if response is not None:
                    yield StreamChunk(
                        citations=_extract_citations(response),
                        context=_extract_context(response) or None,
                        usage=_extract_usage(response),
                    )


@lru_cache(maxsize=1)
def get_llm() -> OpenAIClient:
    return OpenAIClient()
