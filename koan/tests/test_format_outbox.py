"""Tests for format_outbox module."""

import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

from app.format_outbox import (
    load_soul,
    load_human_prefs,
    format_for_telegram,
    fallback_format,
)


class TestLoadSoul:
    def test_returns_content_when_file_exists(self, instance_dir):
        result = load_soul(instance_dir)
        assert result == "# Test Soul"

    def test_returns_empty_when_file_missing(self, tmp_path):
        result = load_soul(tmp_path)
        assert result == ""


class TestLoadHumanPrefs:
    def test_returns_content_when_file_exists(self, instance_dir):
        prefs_dir = instance_dir / "memory" / "global"
        prefs_dir.mkdir(parents=True, exist_ok=True)
        (prefs_dir / "human-preferences.md").write_text("Prefers French")
        result = load_human_prefs(instance_dir)
        assert result == "Prefers French"

    def test_returns_empty_when_file_missing(self, instance_dir):
        result = load_human_prefs(instance_dir)
        assert result == ""


class TestFallbackFormat:
    def test_removes_markdown_headers(self):
        assert fallback_format("## Title\nContent") == "Title\nContent"

    def test_removes_code_fences(self):
        assert fallback_format("```python\ncode\n```") == "python\ncode"

    def test_truncates_long_content(self):
        long_text = "a" * 600
        result = fallback_format(long_text)
        assert len(result) == 503  # 500 + "..."
        assert result.endswith("...")

    def test_strips_whitespace(self):
        assert fallback_format("  hello  ") == "hello"

    def test_short_content_not_truncated(self):
        assert fallback_format("Short message") == "Short message"


class TestFormatForTelegram:
    @patch("app.format_outbox.subprocess.run")
    def test_returns_claude_output_on_success(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="Voici le résumé formaté.\n", stderr=""
        )
        result = format_for_telegram("raw content", "soul", "prefs")
        assert result == "Voici le résumé formaté."
        mock_run.assert_called_once()

    @patch("app.format_outbox.subprocess.run")
    def test_strips_markdown_from_claude_output(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="**bold** and ```code``` and __under__ and ~~strike~~", stderr=""
        )
        result = format_for_telegram("raw", "soul", "")
        assert "**" not in result
        assert "```" not in result
        assert "__" not in result
        assert "~~" not in result

    @patch("app.format_outbox.subprocess.run")
    def test_fallback_on_nonzero_returncode(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="error"
        )
        result = format_for_telegram("## Raw content", "soul", "")
        # Should use fallback (removes #)
        assert "#" not in result
        assert "Raw content" in result

    @patch("app.format_outbox.subprocess.run")
    def test_fallback_on_empty_stdout(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="  \n  ", stderr=""
        )
        result = format_for_telegram("Some raw content", "soul", "")
        assert "Some raw content" in result

    @patch("app.format_outbox.subprocess.run")
    def test_fallback_on_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="claude", timeout=30)
        result = format_for_telegram("Raw", "soul", "")
        assert result == "Raw"

    @patch("app.format_outbox.subprocess.run")
    def test_fallback_on_exception(self, mock_run):
        mock_run.side_effect = FileNotFoundError("claude not found")
        result = format_for_telegram("Raw", "soul", "")
        assert result == "Raw"

    @patch("app.format_outbox.subprocess.run")
    def test_prompt_includes_soul_and_prefs(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        format_for_telegram("content", "my-soul", "my-prefs")
        call_args = mock_run.call_args[0][0]
        prompt = call_args[2]  # ["claude", "-p", prompt]
        assert "my-soul" in prompt
        assert "my-prefs" in prompt

    @patch("app.format_outbox.subprocess.run")
    def test_prompt_omits_prefs_when_empty(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        format_for_telegram("content", "soul", "")
        call_args = mock_run.call_args[0][0]
        prompt = call_args[2]
        assert "Human preferences:" not in prompt
