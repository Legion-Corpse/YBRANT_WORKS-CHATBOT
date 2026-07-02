from __future__ import annotations

import logging
from dataclasses import dataclass, replace

from app.chat import cache
from app.config import settings
from app.ingestion import openai_store
from app.ingestion.loaders import Document, WebLoader, find_loader

logger = logging.getLogger(__name__)


@dataclass
class IngestResult:
    source_id: str
    # Number of files uploaded for this source (usually 1). Named `chunks` for
    # backward-compatible responses; OpenAI does the actual chunking server-side.
    chunks: int


def ingest_target(
    target: str,
    *,
    source_id: str | None = None,
    title: str | None = None,
    url: str | None = None,
    raw_path: str | None = None,
) -> list[IngestResult]:
    """Ingest one target into the OpenAI vector store.

    A local file (``raw_path`` given) is uploaded to OpenAI as-is so it parses
    the PDF/DOCX/TXT itself — higher fidelity than our own extraction, and no
    local parser dependency. A web target is fetched and extracted to text
    (preserving WebLoader's SSRF guard and JS-render path), then uploaded as a
    .txt file.
    """
    results: list[IngestResult]
    if raw_path is not None:
        sid = source_id or f"file:{target}"
        openai_store.replace_source(
            sid, title=title or sid, url=url or sid, text=None, path=raw_path
        )
        logger.info("Ingested %s -> OpenAI vector store", sid)
        results = [IngestResult(source_id=sid, chunks=1)]
    else:
        loader = find_loader(target)
        documents = _with_metadata_overrides(
            loader.load(target), source_id=source_id, title=title, url=url
        )
        results = []
        for doc in documents:
            openai_store.replace_source(
                doc.source_id, title=doc.title, url=doc.url, text=doc.text, path=None
            )
            logger.info("Ingested %s -> OpenAI vector store", doc.source_id)
            results.append(IngestResult(source_id=doc.source_id, chunks=1))

    if results:
        # Knowledge base changed — drop cached answers so re-ingested content
        # isn't masked by stale responses.
        cache.clear()
    return results


def _with_metadata_overrides(
    documents: list[Document],
    *,
    source_id: str | None,
    title: str | None,
    url: str | None,
) -> list[Document]:
    if not any((source_id, title, url)):
        return documents
    return [
        replace(
            doc,
            source_id=source_id or doc.source_id,
            title=title or doc.title,
            url=url or doc.url,
        )
        for doc in documents
    ]


def ingest_site(base_url: str | None = None) -> tuple[list[IngestResult], list[str]]:
    """Crawl and ingest every sitemap URL. One bad page is logged and skipped
    rather than aborting the whole crawl, but the failed URLs are returned
    alongside the results so the caller (the CLI) can surface them instead of
    a silently-successful run that actually missed content."""
    base = base_url or settings.site_base_url
    urls = WebLoader.discover_sitemap_urls(base)
    urls = [u for u in urls if not u.rstrip("/").endswith("/sitemap")]
    logger.info("Sitemap lists %d URLs", len(urls))

    results = []
    failed: list[str] = []
    for url in urls:
        try:
            results.extend(ingest_target(url))
        except Exception:
            logger.exception("Failed to ingest %s", url)
            failed.append(url)
    return results, failed
