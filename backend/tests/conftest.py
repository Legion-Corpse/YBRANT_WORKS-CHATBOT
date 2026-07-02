import types

import pytest

import app.chat.openai_client as openai_client
from app import metrics, quota
from app.chat import cache, conversations
from app.config import settings


class _FakeConversations:
    def __init__(self):
        self._n = 0

    def create(self):
        self._n += 1
        return types.SimpleNamespace(id=f"conv_test_{self._n}")


class _FakeClient:
    """Stands in for the OpenAI client so nothing hits the network. Only the
    surface the code touches at test time (conversations.create) is implemented;
    the LLM itself is patched per-test via service.get_llm."""

    def __init__(self):
        self.conversations = _FakeConversations()


@pytest.fixture(autouse=True)
def _env_and_reset(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "sk-test")
    monkeypatch.setattr(settings, "openai_vector_store_id", "vs_test")
    # Unit tests must not write the real answer-cache file to disk.
    monkeypatch.setattr(settings, "cache_persist", False)
    # conversations.get_or_create() resolves get_client at call time from the
    # module, so patching it here keeps conversation creation offline.
    monkeypatch.setattr(openai_client, "get_client", lambda: _FakeClient())
    cache.clear()
    conversations.clear()
    metrics.reset()
    quota.reset()
    yield
    cache.clear()
    conversations.clear()
