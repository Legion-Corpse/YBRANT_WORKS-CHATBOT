# YbrantWorks knowledge chatbot — backend image.
#
# SINGLE-WORKER BY DESIGN. The answer cache (app/chat/cache.py), the conversation
# id map (app/chat/conversations.py), and the metrics counters (app/metrics.py)
# all live in process memory. Running more than one worker would fragment the
# cache (hit-rate collapses) and scatter the session->conversation mapping. At the
# target scale (a handful of users) one worker is correct; to scale out, move
# those to a shared store (e.g. Redis) first.
FROM python:3.11-slim

WORKDIR /app

ENV PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Serve-only deps: no ingestion stack (no requests/bs4/trafilatura), and crucially
# no Playwright/Chromium (~400MB). Ingestion is run offline from a workstation
# with the full requirements.txt (scripts/ingest.py); the vector store is hosted
# by OpenAI, so the serving container never ingests.
COPY requirements-serve.txt .
RUN pip install -r requirements-serve.txt

COPY app/ app/

# Run as a non-root user. /app/data holds the persisted answer cache and the
# source_id->file_id map (app/chat/cache.py, app/ingestion/openai_store.py both
# create it on first write), so the app needs write access there.
RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser

# Lock CORS to the live site by default. Override at deploy time only if the
# widget is embedded on a different host. Never set this to '*' in production.
ENV CORS_ORIGINS=https://www.ybrantworks.com

# OPENAI_API_KEY and OPENAI_VECTOR_STORE_ID are injected at deploy time
# (never baked into the image).

EXPOSE 8000

# Most low-cost PaaS hosts (Render/Railway-style) inject $PORT and route traffic
# only to that port; default to 8000 for `docker run` without one set.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request as u; u.urlopen('http://localhost:' + os.environ.get('PORT','8000') + '/api/health', timeout=3)" || exit 1

# --workers 1 is intentional — see the header note above. --proxy-headers trusts
# the reverse proxy's X-Forwarded-For so slowapi's per-IP rate limit sees real
# visitor IPs instead of collapsing every request onto the proxy's IP (which
# would silently defeat the abuse guard behind any PaaS load balancer).
#
# JSON-array CMD wrapping `sh -c 'exec ...'`: the ${PORT} expansion needs a
# shell, but plain shell-form CMD leaves sh as PID 1, which can swallow/delay
# the SIGTERM a platform sends on stop/redeploy. `exec` replaces the shell with
# uvicorn so it becomes PID 1 and shuts down promptly.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --proxy-headers"]
