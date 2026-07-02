"""Thin wrapper over OpenAI Files + Vector Stores for ingestion.

OpenAI hosts the vector store and does the chunking/embedding server-side, so
ingestion here is just: get the document text (via the existing loaders), upload
it as a file, attach it to the one persistent vector store, and remember the
mapping so a re-ingest can replace the old file cleanly.

The mapping ``source_id -> {file_id, title, url}`` is persisted to
``data/openai_files.json``. It gives us idempotent re-ingest (delete-then-add)
and lets the chat path turn a file citation back into a real page title/URL —
neither of which the raw OpenAI file object carries.
"""

from __future__ import annotations

import io
import json
import logging
import threading
from pathlib import Path

from app.config import BACKEND_DIR, settings

logger = logging.getLogger(__name__)

_MAP_PATH = BACKEND_DIR / "data" / "openai_files.json"
_lock = threading.Lock()


# --- persistent source_id -> metadata map ----------------------------------

def _load_map() -> dict[str, dict]:
    try:
        return json.loads(_MAP_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_map(data: dict[str, dict]) -> None:
    _MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
    _MAP_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# --- vector store ----------------------------------------------------------

def ensure_vector_store() -> str:
    """Return the configured vector store id, creating one if unset.

    Creating one mutates only in-process settings — the id is logged so it can be
    pinned in .env; otherwise a restart would create yet another orphaned store.
    """
    if settings.openai_vector_store_id:
        return settings.openai_vector_store_id
    from app.chat.openai_client import get_client

    store = get_client().vector_stores.create(name="ybrant_knowledge")
    settings.openai_vector_store_id = store.id
    logger.warning(
        "Created a new OpenAI vector store %s — set OPENAI_VECTOR_STORE_ID=%s in "
        ".env so restarts reuse it instead of creating another.",
        store.id,
        store.id,
    )
    return store.id


# --- upload / attach / delete ----------------------------------------------

def _upload_and_attach(file_arg) -> str:
    """Upload a file object and attach it to the vector store, waiting until it
    is fully processed. Returns the file id."""
    from app.chat.openai_client import get_client

    client = get_client()
    vs_id = ensure_vector_store()
    vector_file = client.vector_stores.files.upload_and_poll(
        vector_store_id=vs_id, file=file_arg
    )
    status = getattr(vector_file, "status", None)
    if status and status != "completed":
        logger.warning("Vector store file %s ended in status %s", vector_file.id, status)
    return vector_file.id


def _delete_file(file_id: str) -> None:
    from app.chat.openai_client import get_client

    client = get_client()
    vs_id = settings.openai_vector_store_id
    for call in (
        lambda: client.vector_stores.files.delete(vector_store_id=vs_id, file_id=file_id),
        lambda: client.files.delete(file_id),
    ):
        try:
            call()
        except Exception:
            logger.warning("Failed to delete OpenAI file %s (continuing)", file_id)


def replace_source(source_id: str, *, title: str, url: str, text: str | None, path: str | None) -> str:
    """Idempotently (re-)ingest one source: delete any prior file for this
    source_id, upload the new content, and record the mapping.

    Exactly one of ``text`` (web/extracted content, uploaded as .txt) or ``path``
    (a raw file OpenAI parses itself) is provided.
    """
    with _lock:
        mapping = _load_map()
        prior = mapping.get(source_id)
        if prior and prior.get("file_id"):
            _delete_file(prior["file_id"])

        if path is not None:
            with open(path, "rb") as fh:
                file_id = _upload_and_attach(fh)
        else:
            name = _safe_filename(title or source_id)
            file_arg = (name, io.BytesIO((text or "").encode("utf-8")))
            file_id = _upload_and_attach(file_arg)

        mapping[source_id] = {"file_id": file_id, "title": title, "url": url}
        _save_map(mapping)
        return file_id


def _safe_filename(base: str) -> str:
    import re

    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("_") or "document"
    if not stem.lower().endswith(".txt"):
        stem += ".txt"
    return stem[:128]


# --- read helpers (chat + health) ------------------------------------------

def source_for_file(file_id: str) -> tuple[str, str] | None:
    """(title, url) for a cited file id, or None if unknown."""
    for meta in _load_map().values():
        if meta.get("file_id") == file_id:
            return meta.get("title", ""), meta.get("url", "")
    return None


def list_sources() -> list[dict]:
    return [{"source_id": sid, **meta} for sid, meta in _load_map().items()]


def file_count() -> int:
    return len(_load_map())
