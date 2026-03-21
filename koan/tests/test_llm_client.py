"""Tests for llm_client lightweight direct API helper."""

import sys
from types import SimpleNamespace

from app.llm_client import try_complete_with_api


class _FakeClient:
    def __init__(self, *_args, **_kwargs):
        self.messages = self

    def create(self, **_kwargs):
        block = SimpleNamespace(type="text", text="hello from api")
        return SimpleNamespace(content=[block])


class _FakeAnthropicModule:
    Anthropic = _FakeClient


def test_returns_none_when_disabled(monkeypatch):
    monkeypatch.setenv("KOAN_DIRECT_API_LIGHTWEIGHT", "0")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    assert try_complete_with_api("ping") is None


def test_returns_none_without_api_key(monkeypatch):
    monkeypatch.setenv("KOAN_DIRECT_API_LIGHTWEIGHT", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert try_complete_with_api("ping") is None


def test_returns_text_on_success(monkeypatch):
    monkeypatch.setenv("KOAN_DIRECT_API_LIGHTWEIGHT", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setitem(sys.modules, "anthropic", _FakeAnthropicModule)
    result = try_complete_with_api("ping", system_prompt_name="")
    assert result == "hello from api"
