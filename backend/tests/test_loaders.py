from unittest.mock import patch

import pytest

from app.config import settings
from app.ingestion.loaders import UnsafeUrlError, WebLoader, assert_public_url


def test_assert_public_url_rejects_cloud_metadata_ip():
    # SSRF guard (now also covering sitemap fetches): the link-local metadata
    # endpoint is not a global address and must be refused. Numeric IP, no DNS.
    with pytest.raises(UnsafeUrlError):
        assert_public_url("http://169.254.169.254/latest/meta-data/")


def test_assert_public_url_rejects_non_http_scheme():
    with pytest.raises(UnsafeUrlError):
        assert_public_url("file:///etc/passwd")


class FakeResponse:
    def __init__(self, content: str) -> None:
        self.content = content.encode("utf-8")
        self.text = content

    def raise_for_status(self) -> None:
        return None


PAGE_HTML = "<html><head><title>Careers</title></head><body><p>Open role: Backend Engineer.</p></body></html>"


def test_load_uses_js_render_when_enabled(monkeypatch):
    monkeypatch.setattr(settings, "web_render_js", True)
    with patch.object(WebLoader, "_render_html", return_value=PAGE_HTML) as render:
        with patch.object(WebLoader, "_safe_get") as static:
            docs = WebLoader().load("https://example.com/career")
    render.assert_called_once()
    static.assert_not_called()
    assert "Backend Engineer" in docs[0].text


def test_load_falls_back_to_static_when_render_fails(monkeypatch):
    # Playwright missing / render error must not break ingest.
    monkeypatch.setattr(settings, "web_render_js", True)
    with patch.object(WebLoader, "_render_html", side_effect=RuntimeError("no chromium")):
        with patch.object(WebLoader, "_safe_get", return_value=FakeResponse(PAGE_HTML)) as static:
            docs = WebLoader().load("https://example.com/career")
    static.assert_called_once()
    assert "Backend Engineer" in docs[0].text


def test_load_static_path_when_render_disabled(monkeypatch):
    monkeypatch.setattr(settings, "web_render_js", False)
    with patch.object(WebLoader, "_render_html") as render:
        with patch.object(WebLoader, "_safe_get", return_value=FakeResponse(PAGE_HTML)):
            WebLoader().load("https://example.com/career")
    render.assert_not_called()


def test_discover_sitemap_urls_recurses_sitemap_index_and_deduplicates():
    responses = {
        "https://example.com/sitemap.xml": """
            <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
              <sitemap><loc>https://example.com/pages.xml</loc></sitemap>
              <sitemap><loc>https://example.com/blog.xml</loc></sitemap>
            </sitemapindex>
        """,
        "https://example.com/pages.xml": """
            <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
              <url><loc>https://example.com/services</loc></url>
              <url><loc>https://example.com/contact</loc></url>
            </urlset>
        """,
        "https://example.com/blog.xml": """
            <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
              <url><loc>https://example.com/contact</loc></url>
              <url><loc>https://example.com/blog/cloud</loc></url>
            </urlset>
        """,
    }

    # Sitemap fetches now go through the SSRF-validated _safe_get (bounded
    # redirects), so stub that seam rather than raw requests.get.
    def fake_safe_get(url):
        return FakeResponse(responses[url])

    with patch.object(WebLoader, "_safe_get", side_effect=fake_safe_get):
        urls = WebLoader.discover_sitemap_urls("https://example.com")

    assert urls == [
        "https://example.com/services",
        "https://example.com/contact",
        "https://example.com/blog/cloud",
    ]
