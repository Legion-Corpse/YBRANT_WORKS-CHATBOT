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

# Lock CORS to the live site by default. Override at deploy time only if the
# widget is embedded on a different host. Never set this to '*' in production.
ENV CORS_ORIGINS=https://www.ybrantworks.com

# OPENAI_API_KEY and OPENAI_VECTOR_STORE_ID are injected at deploy time
# (never baked into the image).

EXPOSE 8000

# --workers 1 is intentional — see the header note above.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
