from __future__ import annotations

import hmac
import logging
import re
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app import metrics, quota
from app.chat.service import answer, answer_stream
from app.config import settings
from app.ingestion import openai_store
from app.schemas import (
    ChatRequest,
    ChatResponse,
    HealthResponse,
    IngestResponse,
    IngestUrlRequest,
    ReadyResponse,
)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
# The OpenAI/httpx clients log a line per HTTP request at INFO (method + URL, no
# secrets, but noisy — it can bury our own signal). Quiet them to WARNING; our
# app loggers stay at INFO.
for _noisy in ("openai", "httpx", "httpcore"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Retrieval + generation are hosted by OpenAI now — there are no local models
    # to warm. Readiness only needs the OpenAI key and the vector store id; make a
    # missing config loud (chat will error until it's set, and /api/ready reports
    # not-ready so a load balancer can hold traffic back).
    if not settings.openai_api_key:
        logger.error(
            "OPENAI_API_KEY is not set — chat endpoints will fail until it is configured"
        )
    if not settings.openai_vector_store_id:
        logger.error(
            "OPENAI_VECTOR_STORE_ID is not set — retrieval has no vector store to "
            "search; ingest content and pin the store id in .env"
        )
    app.state.ready = True
    yield


limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="YbrantWorks Knowledge Chatbot", version="1.0.0", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

origins = [o.strip() for o in settings.cors_origins.split(",")]
if "*" in origins:
    # "*" is meant for local dev only (the file:// widget demo). Left in
    # production, any site can embed the widget and spend the OpenAI budget.
    logger.warning(
        "CORS_ORIGINS is '*' — any site can call this API. Set it to the real "
        "site origin(s) in production."
    )
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    # The widget only issues GET (health) and POST (chat) with a JSON body, so
    # restrict to those rather than the wildcard.
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)

SUPPORTED_UPLOADS = {".pdf", ".docx", ".txt", ".md"}
UPLOAD_CHUNK = 65536


def _safe_upload_name(filename: str | None) -> str:
    name = re.split(r"[\\/]", filename or "")[-1].strip()
    return name or "uploaded-file"


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    """Fail-closed auth for the ingest endpoints.

    If ``ingest_api_key`` is unset the HTTP ingest surface is disabled entirely
    (the CLI ingest path does not pass through here). A constant-time compare
    avoids leaking the token via timing.
    """
    expected = settings.ingest_api_key
    if not expected:
        raise HTTPException(status_code=401, detail="Ingest API is disabled")
    if not x_admin_token or not hmac.compare_digest(x_admin_token, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing admin token")


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    # Liveness: cheap, always 200 if the process is up. `documents` is the number
    # of ingested sources (files uploaded to the OpenAI vector store), read from
    # the local metadata map.
    count = openai_store.file_count()
    return HealthResponse(status="ok", documents=count, chunks=count)


@app.get("/api/ready", response_model=ReadyResponse)
def ready(response: Response) -> ReadyResponse:
    # Readiness: 200 only once the OpenAI key and a vector store id are
    # configured. A load balancer should route chat traffic only on a 200.
    llm_configured = bool(settings.openai_api_key)
    store_reachable = bool(settings.openai_vector_store_id)
    ok = llm_configured and store_reachable
    if not ok:
        response.status_code = 503
    return ReadyResponse(
        ready=ok,
        models_warmed=True,
        store_reachable=store_reachable,
        llm_configured=llm_configured,
    )


@app.get("/api/metrics")
def get_metrics(_: None = Depends(require_admin)) -> dict:
    # Admin-guarded: usage counters can leak traffic patterns, so reuse the
    # ingest admin token rather than exposing them publicly.
    return {**metrics.snapshot(), "quota": quota.snapshot()}


@app.post("/api/chat", response_model=ChatResponse)
@limiter.limit(settings.chat_rate_limit)
def chat(request: Request, req: ChatRequest) -> ChatResponse:
    return answer(req.session_id, req.message)


@app.post("/api/chat/stream")
@limiter.limit(settings.chat_rate_limit)
def chat_stream(request: Request, req: ChatRequest) -> StreamingResponse:
    # Server-Sent Events: tokens flow as Gemini generates them so the widget
    # renders the answer live instead of waiting for the full reply. The figure
    # guard runs per sentence inside answer_stream, so this is no less safe than
    # the buffered /api/chat path.
    return StreamingResponse(
        answer_stream(req.session_id, req.message),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/ingest/url", response_model=IngestResponse)
@limiter.limit(settings.ingest_rate_limit)
def ingest_url(
    request: Request, req: IngestUrlRequest, _: None = Depends(require_admin)
) -> IngestResponse:
    # Ingest deps (requests/bs4/trafilatura/playwright) are imported lazily so a
    # serve-only image without them still boots; ingestion is normally run offline
    # via the CLI (scripts/ingest.py).
    from app.ingestion.loaders import UnsafeUrlError, assert_public_url
    from app.ingestion.pipeline import ingest_target

    try:
        assert_public_url(req.url)
    except UnsafeUrlError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        results = ingest_target(req.url)
    except Exception as exc:
        # Log the detail server-side; return a generic message so internal
        # exception text (paths, library internals) isn't echoed to the caller.
        logger.exception("URL ingestion failed for %s", req.url)
        raise HTTPException(status_code=422, detail="Ingestion failed") from exc
    if not results:
        raise HTTPException(status_code=422, detail="No content extracted from URL")
    return IngestResponse(source_id=results[0].source_id, chunks=results[0].chunks)


@app.post("/api/ingest/file", response_model=IngestResponse)
@limiter.limit(settings.ingest_rate_limit)
async def ingest_file(
    request: Request,
    file: UploadFile = File(...),
    _: None = Depends(require_admin),
) -> IngestResponse:
    # Lazy import (see ingest_url): keeps the serve-only image free of ingest deps.
    from app.ingestion.pipeline import ingest_target

    original_name = _safe_upload_name(file.filename)
    suffix = Path(original_name).suffix.lower()
    if suffix not in SUPPORTED_UPLOADS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type {suffix or '(none)'}; "
            f"supported: {', '.join(sorted(SUPPORTED_UPLOADS))}",
        )
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp_path = tmp.name
        written = 0
        while chunk := await file.read(UPLOAD_CHUNK):
            written += len(chunk)
            if written > settings.max_upload_bytes:
                tmp.close()
                Path(tmp_path).unlink(missing_ok=True)
                raise HTTPException(
                    status_code=413,
                    detail=f"File exceeds maximum size of {settings.max_upload_bytes} bytes",
                )
            tmp.write(chunk)
    try:
        results = ingest_target(
            tmp_path,
            source_id=f"file:{original_name}",
            title=Path(original_name).stem,
            url=original_name,
            raw_path=tmp_path,
        )
    except Exception as exc:
        logger.exception("File ingestion failed for %s", original_name)
        raise HTTPException(status_code=422, detail="Ingestion failed") from exc
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    if not results:
        raise HTTPException(status_code=422, detail="No content extracted from file")
    return IngestResponse(source_id=results[0].source_id, chunks=results[0].chunks)
