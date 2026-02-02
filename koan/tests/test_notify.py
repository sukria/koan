"""Tests for notify.py — message sending, chunking, error handling, format_and_send."""

from unittest.mock import patch, MagicMock

import pytest
import requests

from app.notify import send_telegram, format_and_send


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
    def test_exact_boundary_no_extra_chunk(self, mock_post):
        """Message of exactly 4000 chars should produce 1 chunk, not 2."""
        mock_post.return_value = MagicMock(json=lambda: {"ok": True})
        assert send_telegram("x" * 4000) is True
        assert mock_post.call_count == 1

    @patch("app.notify.requests.post")
    def test_just_over_boundary(self, mock_post):
        """Message of 4001 chars should produce 2 chunks."""
        mock_post.return_value = MagicMock(json=lambda: {"ok": True})
        assert send_telegram("x" * 4001) is True
        assert mock_post.call_count == 2

    @patch("app.notify.requests.post")
    def test_empty_message_sends_nothing(self, mock_post):
        """Empty string produces zero chunks — no API call, returns True."""
        assert send_telegram("") is True
        mock_post.assert_not_called()

    @patch("app.notify.requests.post")
    def test_partial_failure_returns_false(self, mock_post):
        """If one chunk fails but others succeed, return False."""
        responses = [
            MagicMock(json=lambda: {"ok": True}),
            MagicMock(json=lambda: {"ok": False, "description": "rate limit"}, text="rate limit"),
        ]
        mock_post.side_effect = responses
        assert send_telegram("a" * 5000) is False
        assert mock_post.call_count == 2

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

    @patch("app.notify.requests.post", side_effect=ValueError("bad json"))
    def test_json_decode_error(self, mock_post):
        """ValueError from resp.json() is caught."""
        assert send_telegram("test") is False

    def test_no_token(self, monkeypatch):
        monkeypatch.delenv("KOAN_TELEGRAM_TOKEN", raising=False)
        assert send_telegram("test") is False

    def test_no_chat_id(self, monkeypatch):
        monkeypatch.delenv("KOAN_TELEGRAM_CHAT_ID", raising=False)
        assert send_telegram("test") is False


class TestFormatAndSend:
    @patch("app.notify.send_telegram", return_value=True)
    def test_with_instance_dir(self, mock_send, instance_dir):
        """format_and_send with explicit instance_dir loads soul/prefs and formats."""
        with patch("app.format_outbox.format_for_telegram", return_value="formatted msg") as mock_fmt, \
             patch("app.format_outbox.load_soul", return_value="soul"), \
             patch("app.format_outbox.load_human_prefs", return_value="prefs"), \
             patch("app.format_outbox.load_memory_context", return_value="memory"):
            result = format_and_send("raw msg", instance_dir=str(instance_dir))

        assert result is True
        mock_fmt.assert_called_once_with("raw msg", "soul", "prefs", "memory")
        mock_send.assert_called_once_with("formatted msg")

    @patch("app.notify.send_telegram", return_value=True)
    def test_fallback_on_format_error(self, mock_send, instance_dir):
        """If formatting raises, fallback to basic cleanup."""
        with patch("app.format_outbox.load_soul", side_effect=OSError("boom")), \
             patch("app.format_outbox.fallback_format", return_value="clean msg") as mock_fb:
            result = format_and_send("raw", instance_dir=str(instance_dir))

        assert result is True
        mock_fb.assert_called_once_with("raw")
        mock_send.assert_called_once_with("clean msg")

    @patch("app.notify.send_telegram", return_value=True)
    def test_no_koan_root_sends_fallback(self, mock_send, monkeypatch):
        """Without KOAN_ROOT and no instance_dir, sends basic fallback."""
        monkeypatch.delenv("KOAN_ROOT", raising=False)
        result = format_and_send("raw technical msg")

        assert result is True
        # Should have called send_telegram with some version of the message
        mock_send.assert_called_once()
        sent_text = mock_send.call_args[0][0]
        assert len(sent_text) > 0  # fallback_format produces non-empty output

    @patch("app.notify.send_telegram", return_value=True)
    def test_koan_root_auto_detect(self, mock_send, tmp_path, monkeypatch):
        """With KOAN_ROOT set, instance_dir is auto-detected."""
        monkeypatch.setenv("KOAN_ROOT", str(tmp_path))

        with patch("app.format_outbox.load_soul", return_value="s"), \
             patch("app.format_outbox.load_human_prefs", return_value="p"), \
             patch("app.format_outbox.load_memory_context", return_value="m"), \
             patch("app.format_outbox.format_for_telegram", return_value="fmt"):
            result = format_and_send("raw")

        assert result is True
        mock_send.assert_called_once_with("fmt")

    @patch("app.notify.send_telegram", return_value=True)
    def test_project_name_passed_to_memory(self, mock_send, instance_dir):
        """project_name argument is forwarded to load_memory_context."""
        with patch("app.format_outbox.load_soul", return_value="s"), \
             patch("app.format_outbox.load_human_prefs", return_value="p"), \
             patch("app.format_outbox.load_memory_context", return_value="m") as mock_mem, \
             patch("app.format_outbox.format_for_telegram", return_value="fmt"):
            format_and_send("raw", instance_dir=str(instance_dir),
                           project_name="myproject")

        mock_mem.assert_called_once()
        assert mock_mem.call_args[0][1] == "myproject"


class TestNotifyCLI:
    """Tests for __main__ CLI entry point (lines 97-119)."""

    def test_cli_send_message(self, monkeypatch):
        import runpy
        monkeypatch.setattr("sys.argv", ["notify.py", "Hello", "world"])
        with patch("app.notify.requests.post") as mock_post, \
             pytest.raises(SystemExit) as exc_info:
            mock_post.return_value = MagicMock(json=lambda: {"ok": True})
            runpy.run_module("app.notify", run_name="__main__")
        assert exc_info.value.code == 0

    def test_cli_format_flag(self, monkeypatch):
        import runpy
        monkeypatch.setattr("sys.argv", ["notify.py", "--format", "Raw msg"])
        with patch("app.notify.requests.post") as mock_post, \
             patch("app.format_outbox.subprocess.run") as mock_sub, \
             pytest.raises(SystemExit) as exc_info:
            mock_post.return_value = MagicMock(json=lambda: {"ok": True})
            mock_sub.return_value = MagicMock(returncode=0, stdout="Formatted", stderr="")
            runpy.run_module("app.notify", run_name="__main__")
        assert exc_info.value.code == 0

    def test_cli_format_passes_project_name(self, monkeypatch):
        """CLI --format reads KOAN_CURRENT_PROJECT env var."""
        import runpy
        monkeypatch.setattr("sys.argv", ["notify.py", "--format", "Raw msg"])
        monkeypatch.setenv("KOAN_CURRENT_PROJECT", "myproject")
        with patch("app.notify.requests.post") as mock_post, \
             patch("app.format_outbox.subprocess.run") as mock_sub, \
             pytest.raises(SystemExit) as exc_info:
            mock_post.return_value = MagicMock(json=lambda: {"ok": True})
            mock_sub.return_value = MagicMock(returncode=0, stdout="Formatted", stderr="")
            runpy.run_module("app.notify", run_name="__main__")
        assert exc_info.value.code == 0
        # Verify Claude was called (format_and_send path was used)
        mock_sub.assert_called_once()

    def test_cli_no_args(self, monkeypatch):
        import runpy
        monkeypatch.setattr("sys.argv", ["notify.py"])
        with pytest.raises(SystemExit) as exc_info:
            runpy.run_module("app.notify", run_name="__main__")
        assert exc_info.value.code == 1

    def test_cli_format_no_message(self, monkeypatch):
        import runpy
        monkeypatch.setattr("sys.argv", ["notify.py", "--format"])
        with pytest.raises(SystemExit) as exc_info:
            runpy.run_module("app.notify", run_name="__main__")
        assert exc_info.value.code == 1

    def test_cli_failure_exit_code(self, monkeypatch):
        import runpy
        monkeypatch.setattr("sys.argv", ["notify.py", "msg"])
        with patch("app.notify.send_telegram", return_value=False), \
             pytest.raises(SystemExit) as exc_info:
            runpy.run_module("app.notify", run_name="__main__")
        assert exc_info.value.code == 1
