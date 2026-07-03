from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- OpenAI (LLM + hosted retrieval) ------------------------------------
    # Retrieval is OpenAI's hosted file_search: uploaded docs live in a managed
    # vector store, and each chat call runs responses.create() with the
    # file_search tool. No local embedding/rerank models, no ChromaDB.
    openai_api_key: str = ""
    # Model id is office-supplied and kept configurable so it can be corrected in
    # .env without a code change if the account exposes a different id.
    openai_model: str = "gpt-5.4-nano-2026-03-17"
    # The persistent vector store searched on every chat turn. Create it once
    # (scripts/ingest.py creates one if unset) and pin the id here so restarts and
    # re-ingests reuse the same store instead of leaking a new one each run.
    openai_vector_store_id: str = ""
    # file_search only returns chunks scoring at/above this. Higher = stricter
    # grounding (fewer, more on-topic snippets); lower = more recall. Calibrated
    # live: 0.7 (the office notebook's value) missed real on-topic content, while
    # 0.3-0.5 recalled it and still rejected off-topic queries. 0.4 is the middle
    # of that band; re-tune per the actual document set.
    openai_score_threshold: float = 0.4
    # Max file_search chunks fed to the model per turn. Kept small — answers are
    # short and fewer chunks means fewer input tokens per call.
    openai_max_num_results: int = 5
    # 0.0: this is grounded extraction, not creative writing — determinism helps.
    openai_temperature: float = 0.0
    # Answers here are short (< ~150 words); a low cap trims output-token cost.
    openai_max_output_tokens: int = 512
    # Hard per-call deadline (s); a hung call can't pin a worker thread. The SDK
    # also retries transient 429/5xx up to openai_max_retries with backoff.
    openai_timeout_seconds: float = 30.0
    openai_max_retries: int = 2

    # --- Ingestion (web crawl feeds the OpenAI vector store) ----------------
    site_base_url: str = "https://www.ybrantworks.com"
    # The site is a Next.js app — some content (e.g. /career open positions)
    # renders client-side and is invisible to a static fetch. When enabled,
    # WebLoader runs the page through headless Chromium (Playwright) at ingest
    # to capture JS-rendered text; it falls back to the static fetch if
    # Playwright/Chromium is unavailable or the render fails.
    web_render_js: bool = True
    web_render_timeout_ms: int = 15000
    contact_email: str = "info@ybrantworks.com"
    contact_phone: str = "+91 9663422557"

    # Admin token required on /api/ingest/* (fail-closed: empty => endpoints
    # reject every request). The CLI ingest path does not go through HTTP and is
    # unaffected. Set INGEST_API_KEY to enable the HTTP ingest endpoints.
    ingest_api_key: str = ""
    # Hard ceiling on uploaded file size (bytes); requests over this get 413.
    max_upload_bytes: int = 10_485_760  # 10 MiB

    # --- Conversation memory (session_id -> OpenAI conversation id) ---------
    # OpenAI's Conversations API holds the running turn history server-side; we
    # only keep the id mapping in-process, bounded like the old session store.
    # A session is considered ended after this much inactivity: the next message
    # from that session then starts a fresh OpenAI conversation (the widget shows
    # a "session ended" divider and rotates its session id on the same 5-min
    # clock). Keep this >= the widget's data-idle-minutes.
    max_sessions: int = 1000
    session_ttl_seconds: int = 300  # 5 min idle

    # CORS: which origins' browser JS may call this API. Defaults to the live
    # site (safe for non-Docker deploys); set to "*" only for local dev.
    cors_origins: str = site_base_url

    # Answer cache (first-turn, no-history questions only). Saves repeat paid
    # OpenAI calls; cleared on every ingest so stale answers can't mask new docs.
    # Site content changes rarely, so the TTL is long (24h). The cache is
    # persisted to disk so restarts keep their hits.
    cache_enabled: bool = True
    cache_ttl_seconds: int = 86_400  # 24h
    cache_max_entries: int = 256
    cache_persist: bool = True
    cache_path: str = str(BACKEND_DIR / "data" / "answer_cache.json")

    # Per-IP request limits (slowapi). First-line abuse/cost guard on the paid key.
    chat_rate_limit: str = "20/minute"
    ingest_rate_limit: str = "5/minute"

    # Hard aggregate ceiling on real OpenAI chat calls per UTC day. The per-IP
    # limit above can be evaded across many IPs; this caps total spend regardless.
    # In-process (single worker); resets on the UTC day change and on restart.
    # Instant paths (cache hit, identity) do NOT count — only real LLM calls do.
    # Sized for the documented ~5 users/month: 100/day never bites legitimately,
    # it's purely the runaway-abuse backstop. Raise via env if real volume grows.
    daily_request_cap: int = 100


settings = Settings()
