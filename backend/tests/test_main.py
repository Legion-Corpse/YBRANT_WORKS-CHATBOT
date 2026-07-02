from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.ingestion.pipeline import IngestResult
from app.main import app
from app.schemas import ChatResponse, Confidence

client = TestClient(app)

TOKEN = "test-admin-token"
AUTH = {"X-Admin-Token": TOKEN}


@pytest.fixture
def _enable_ingest(monkeypatch):
    monkeypatch.setattr(settings, "ingest_api_key", TOKEN)
    yield


def test_ingest_file_preserves_filename_and_passes_raw_path(_enable_ingest):
    captured = {}

    def fake_ingest_target(target, *, source_id, title, url, raw_path):
        captured.update(
            target=target, source_id=source_id, title=title, url=url, raw_path=raw_path
        )
        return [IngestResult(source_id=source_id, chunks=1)]

    # ingest_target is imported lazily inside the route now, so patch it at the
    # source module rather than on app.main.
    with patch("app.ingestion.pipeline.ingest_target", side_effect=fake_ingest_target):
        response = client.post(
            "/api/ingest/file",
            headers=AUTH,
            files={"file": (r"C:\fake\Company Profile.md", b"# Company\n", "text/markdown")},
        )

    assert response.status_code == 200
    assert response.json() == {"source_id": "file:Company Profile.md", "chunks": 1}
    assert captured["source_id"] == "file:Company Profile.md"
    assert captured["title"] == "Company Profile"
    assert captured["url"] == "Company Profile.md"
    # The raw temp file is uploaded to OpenAI as-is (no local parsing).
    assert captured["raw_path"] == captured["target"]
    assert captured["target"].endswith(".md")


def test_ingest_requires_admin_token_fail_closed():
    resp = client.post("/api/ingest/url", json={"url": "https://example.com"})
    assert resp.status_code == 401


def test_ingest_rejects_wrong_token(_enable_ingest):
    resp = client.post(
        "/api/ingest/url",
        json={"url": "https://example.com"},
        headers={"X-Admin-Token": "wrong"},
    )
    assert resp.status_code == 401


def test_ingest_url_blocks_ssrf_to_private_address(_enable_ingest):
    resp = client.post(
        "/api/ingest/url",
        json={"url": "http://169.254.169.254/latest/meta-data/"},
        headers=AUTH,
    )
    assert resp.status_code == 400


def test_ingest_file_rejects_oversize_upload(_enable_ingest, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_bytes", 16)
    resp = client.post(
        "/api/ingest/file",
        headers=AUTH,
        files={"file": ("big.txt", b"x" * 1024, "text/plain")},
    )
    assert resp.status_code == 413


def test_health_returns_source_counts():
    with patch("app.main.openai_store.file_count", return_value=7):
        resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "documents": 7, "chunks": 7}


def test_ready_is_503_when_key_missing(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "")
    resp = client.get("/api/ready")
    assert resp.status_code == 503
    assert resp.json()["ready"] is False
    assert resp.json()["llm_configured"] is False


def test_ready_is_200_when_key_and_store_configured():
    # conftest sets openai_api_key + openai_vector_store_id.
    resp = client.get("/api/ready")
    assert resp.status_code == 200
    assert resp.json() == {
        "ready": True,
        "models_warmed": True,
        "store_reachable": True,
        "llm_configured": True,
    }


def test_ready_is_503_without_vector_store(monkeypatch):
    monkeypatch.setattr(settings, "openai_vector_store_id", "")
    resp = client.get("/api/ready")
    assert resp.status_code == 503
    assert resp.json()["store_reachable"] is False


def test_metrics_requires_admin_token():
    assert client.get("/api/metrics").status_code == 401


def test_metrics_returns_snapshot_with_token(_enable_ingest):
    resp = client.get("/api/metrics", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert "counters" in body
    assert "llm_latency_seconds_avg" in body


def test_chat_rejects_blank_message():
    resp = client.post("/api/chat", json={"session_id": "s", "message": "   "})
    assert resp.status_code == 422


def test_chat_endpoint_is_rate_limited():
    dummy = ChatResponse(answer="ok", confidence=Confidence.HIGH)
    import app.main as main

    with patch.object(main, "answer", return_value=dummy):
        codes = [
            client.post("/api/chat", json={"session_id": "s", "message": "hi"}).status_code
            for _ in range(25)
        ]
    assert codes[0] == 200
    assert 429 in codes  # limit is 20/minute
