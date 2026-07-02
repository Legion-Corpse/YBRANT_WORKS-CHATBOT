from unittest.mock import patch

from app.ingestion import pipeline
from app.ingestion.loaders import Document, WebLoader


class FakeWebLoader(WebLoader):
    def load(self, target: str) -> list[Document]:
        return [
            Document(
                source_id=target,
                title="Services",
                url=target,
                text="We offer cloud consulting and data analytics.",
            )
        ]


def test_web_ingest_uploads_extracted_text_and_clears_cache():
    calls = {}

    def fake_replace(source_id, *, title, url, text, path):
        calls.update(source_id=source_id, title=title, url=url, text=text, path=path)
        return "file_abc"

    with patch.object(pipeline, "find_loader", return_value=FakeWebLoader()):
        with patch.object(pipeline.openai_store, "replace_source", fake_replace):
            with patch.object(pipeline.cache, "clear") as clear:
                results = pipeline.ingest_target("https://y.com/services")

    assert results[0].source_id == "https://y.com/services"
    assert results[0].chunks == 1
    assert calls["path"] is None  # web content uploaded as text, not a raw file
    assert "cloud consulting" in calls["text"]
    clear.assert_called_once()


def test_file_ingest_uploads_raw_path_without_loader():
    calls = {}

    def fake_replace(source_id, *, title, url, text, path):
        calls.update(source_id=source_id, title=title, url=url, text=text, path=path)
        return "file_xyz"

    # raw_path given -> loaders are bypassed entirely (no parser dependency).
    with patch.object(pipeline, "find_loader", side_effect=AssertionError("loader must not be used")):
        with patch.object(pipeline.openai_store, "replace_source", fake_replace):
            with patch.object(pipeline.cache, "clear"):
                results = pipeline.ingest_target(
                    "/tmp/upload.pdf",
                    source_id="file:Company Profile.pdf",
                    title="Company Profile",
                    url="Company Profile.pdf",
                    raw_path="/tmp/upload.pdf",
                )

    assert results[0].source_id == "file:Company Profile.pdf"
    assert calls["path"] == "/tmp/upload.pdf"
    assert calls["text"] is None
    assert calls["title"] == "Company Profile"
