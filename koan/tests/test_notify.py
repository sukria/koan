"""Tests for notify.py — message sending, chunking, error handling."""

from unittest.mock import patch, MagicMock

import pytest
import requests

from app.notify import send_telegram


class TestSendTelegram:
    @patch("app.notify.requests.post")
    def test_short_message(self, mock_post):
        mock_post.return_value = MagicMock(json=lambda: {"ok": True})
        assert send_telegram("hello") is True
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert call_kwargs[1]["json"]["text"] == "hello"

    @patch("app.notify.requests.post")
    def test_long_message_chunked(self, mock_post):
        mock_post.return_value = MagicMock(json=lambda: {"ok": True})
        long_msg = "x" * 8500  # Should be split into 3 chunks (4000+4000+500)
        assert send_telegram(long_msg) is True
        assert mock_post.call_count == 3

    @patch("app.notify.requests.post")
    def test_api_error(self, mock_post):
        mock_post.return_value = MagicMock(
            json=lambda: {"ok": False, "description": "bad request"},
            text='{"ok":false}',
        )
        assert send_telegram("test") is False

    @patch("app.notify.requests.post", side_effect=requests.RequestException("network error"))
    def test_network_error(self, mock_post):
        assert send_telegram("test") is False

    def test_no_token(self, monkeypatch):
        monkeypatch.delenv("KOAN_TELEGRAM_TOKEN", raising=False)
        assert send_telegram("test") is False

    def test_no_chat_id(self, monkeypatch):
        monkeypatch.delenv("KOAN_TELEGRAM_CHAT_ID", raising=False)
        assert send_telegram("test") is False
