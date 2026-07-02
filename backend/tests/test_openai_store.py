import types

import pytest

import app.chat.openai_client as openai_client
from app.ingestion import openai_store


class _FakeFiles:
    def __init__(self, recorder):
        self._rec = recorder
        self._n = 0

    def upload_and_poll(self, *, vector_store_id, file):
        self._n += 1
        return types.SimpleNamespace(id=f"file_{self._n}", status="completed")

    def delete(self, *, vector_store_id, file_id):
        self._rec.setdefault("vs_deletes", []).append(file_id)


class _FakeTopFiles:
    def __init__(self, recorder):
        self._rec = recorder

    def delete(self, file_id):
        self._rec.setdefault("file_deletes", []).append(file_id)


class _FakeClient:
    def __init__(self, recorder):
        self.vector_stores = types.SimpleNamespace(files=_FakeFiles(recorder))
        self.files = _FakeTopFiles(recorder)


@pytest.fixture
def store(tmp_path, monkeypatch):
    rec = {}
    fake = _FakeClient(rec)  # one instance so the upload counter persists across calls
    monkeypatch.setattr(openai_store, "_MAP_PATH", tmp_path / "openai_files.json")
    monkeypatch.setattr(openai_client, "get_client", lambda: fake)
    return rec


def test_upload_text_records_mapping(store):
    file_id = openai_store.replace_source(
        "https://y.com/services", title="Services", url="https://y.com/services",
        text="cloud consulting", path=None,
    )
    assert file_id == "file_1"
    assert openai_store.file_count() == 1
    assert openai_store.source_for_file("file_1") == ("Services", "https://y.com/services")


def test_reingest_deletes_prior_file(store):
    openai_store.replace_source("src", title="S", url="u", text="v1", path=None)
    openai_store.replace_source("src", title="S", url="u", text="v2", path=None)
    # The first file must be deleted from both the vector store and the files API.
    assert store["vs_deletes"] == ["file_1"]
    assert store["file_deletes"] == ["file_1"]
    # Only the latest file remains mapped.
    assert openai_store.file_count() == 1
    assert openai_store.source_for_file("file_2") == ("S", "u")
    assert openai_store.source_for_file("file_1") is None
