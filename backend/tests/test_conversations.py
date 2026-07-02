from unittest.mock import patch

import app.chat.openai_client as openai_client
from app.chat import conversations
from app.config import settings


class _CountingConversations:
    def __init__(self):
        self.calls = 0

    def create(self):
        self.calls += 1
        return type("Obj", (), {"id": f"conv-{self.calls}"})()


class _CountingClient:
    def __init__(self):
        self.conversations = _CountingConversations()


def test_get_or_create_creates_once_per_session():
    client = _CountingClient()
    with patch.object(openai_client, "get_client", return_value=client):
        first = conversations.get_or_create("s1")
        second = conversations.get_or_create("s1")
    assert first == second
    assert client.conversations.calls == 1  # not re-created on the second turn


def test_get_or_create_is_independent_per_session():
    client = _CountingClient()
    with patch.object(openai_client, "get_client", return_value=client):
        a = conversations.get_or_create("s1")
        b = conversations.get_or_create("s2")
    assert a != b
    assert client.conversations.calls == 2


def test_has_conversation_reflects_presence():
    assert conversations.has_conversation("fresh") is False
    client = _CountingClient()
    with patch.object(openai_client, "get_client", return_value=client):
        conversations.get_or_create("fresh")
    assert conversations.has_conversation("fresh") is True


def test_ttl_expiry_drops_the_mapping(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(conversations.time, "monotonic", lambda: clock["t"])
    client = _CountingClient()
    with patch.object(openai_client, "get_client", return_value=client):
        conversations.get_or_create("idle")
    assert conversations.has_conversation("idle") is True
    clock["t"] = 1000.0 + settings.session_ttl_seconds + 1
    assert conversations.has_conversation("idle") is False


def test_lru_eviction(monkeypatch):
    monkeypatch.setattr(settings, "max_sessions", 2)
    client = _CountingClient()
    with patch.object(openai_client, "get_client", return_value=client):
        conversations.get_or_create("a")
        conversations.get_or_create("b")
        conversations.get_or_create("c")  # evicts the least-recently-used ("a")
    assert conversations.has_conversation("a") is False
    assert conversations.has_conversation("b") is True
    assert conversations.has_conversation("c") is True


def test_clear_empties_the_store():
    client = _CountingClient()
    with patch.object(openai_client, "get_client", return_value=client):
        conversations.get_or_create("s1")
    conversations.clear()
    assert conversations.has_conversation("s1") is False
