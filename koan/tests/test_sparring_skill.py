"""Tests for the /sparring core skill handler."""

import subprocess
from unittest.mock import patch, MagicMock

from app.skills import SkillContext

# The sparring handler imports lazily inside handle():
#   from app.prompts import load_skill_prompt   → patch at app.prompts.load_skill_prompt
#   from app.utils import get_fast_reply_model   → patch at app.utils.get_fast_reply_model
#   from app.utils import save_telegram_message  → patch at app.utils.save_telegram_message
# But subprocess is module-level → patch at skills.core.sparring.handler.subprocess


def _make_ctx(instance_dir, send_message=None):
    """Create a minimal SkillContext for testing."""
    ctx = MagicMock(spec=SkillContext)
    ctx.command_name = "sparring"
    ctx.instance_dir = instance_dir
    ctx.args = ""
    ctx.send_message = send_message
    return ctx


_P_SUB = "skills.core.sparring.handler.subprocess"
_P_PROMPT = "app.prompts.load_skill_prompt"
_P_MODEL = "app.utils.get_fast_reply_model"
_P_SAVE = "app.utils.save_telegram_message"


# ---------------------------------------------------------------------------
# Context loading (soul, strategy, emotional memory, preferences, missions)
# ---------------------------------------------------------------------------

class TestSparringContextLoading:
    """Test that the handler loads context files correctly."""

    @patch(_P_SUB)
    @patch(_P_PROMPT, return_value="test prompt")
    @patch(_P_MODEL, return_value=None)
    def test_loads_soul_md(self, _model, mock_prompt, mock_sub, tmp_path):
        """Soul file is read and passed to prompt template."""
        instance = tmp_path / "instance"
        instance.mkdir()
        (instance / "soul.md").write_text("I am Koan.")
        mock_sub.run.return_value = MagicMock(returncode=0, stdout="Response text")

        from skills.core.sparring.handler import handle
        handle(_make_ctx(instance))

        _, kwargs = mock_prompt.call_args
        assert kwargs["SOUL"] == "I am Koan."

    @patch(_P_SUB)
    @patch(_P_PROMPT, return_value="test prompt")
    @patch(_P_MODEL, return_value=None)
    def test_missing_soul_file(self, _model, mock_prompt, mock_sub, tmp_path):
        """Missing soul.md = empty string, no crash."""
        instance = tmp_path / "instance"
        instance.mkdir()
        mock_sub.run.return_value = MagicMock(returncode=0, stdout="Response")

        from skills.core.sparring.handler import handle
        handle(_make_ctx(instance))

        _, kwargs = mock_prompt.call_args
        assert kwargs["SOUL"] == ""

    @patch(_P_SUB)
    @patch(_P_PROMPT, return_value="test prompt")
    @patch(_P_MODEL, return_value=None)
    def test_loads_strategy(self, _model, mock_prompt, mock_sub, tmp_path):
        """Strategy file is read and passed to prompt template."""
        instance = tmp_path / "instance"
        instance.mkdir()
        global_dir = instance / "memory" / "global"
        global_dir.mkdir(parents=True)
        (global_dir / "strategy.md").write_text("Focus on koan first.")
        mock_sub.run.return_value = MagicMock(returncode=0, stdout="Response")

        from skills.core.sparring.handler import handle
        handle(_make_ctx(instance))

        _, kwargs = mock_prompt.call_args
        assert kwargs["STRATEGY"] == "Focus on koan first."

    @patch(_P_SUB)
    @patch(_P_PROMPT, return_value="test prompt")
    @patch(_P_MODEL, return_value=None)
    def test_emotional_memory_truncated(self, _model, mock_prompt, mock_sub, tmp_path):
        """Emotional memory is truncated to 1000 chars."""
        instance = tmp_path / "instance"
        instance.mkdir()
        global_dir = instance / "memory" / "global"
        global_dir.mkdir(parents=True)
        (global_dir / "emotional-memory.md").write_text("x" * 2000)
        mock_sub.run.return_value = MagicMock(returncode=0, stdout="Response")

        from skills.core.sparring.handler import handle
        handle(_make_ctx(instance))

        _, kwargs = mock_prompt.call_args
        assert len(kwargs["EMOTIONAL_MEMORY"]) == 1000

    @patch(_P_SUB)
    @patch(_P_PROMPT, return_value="test prompt")
    @patch(_P_MODEL, return_value=None)
    def test_loads_preferences(self, _model, mock_prompt, mock_sub, tmp_path):
        """Preferences file is read and passed to prompt template."""
        instance = tmp_path / "instance"
        instance.mkdir()
        global_dir = instance / "memory" / "global"
        global_dir.mkdir(parents=True)
        (global_dir / "human-preferences.md").write_text("Prefers French.")
        mock_sub.run.return_value = MagicMock(returncode=0, stdout="Response")

        from skills.core.sparring.handler import handle
        handle(_make_ctx(instance))

        _, kwargs = mock_prompt.call_args
        assert kwargs["PREFS"] == "Prefers French."

    @patch(_P_SUB)
    @patch(_P_PROMPT, return_value="test prompt")
    @patch(_P_MODEL, return_value=None)
    def test_loads_recent_missions(self, _model, mock_prompt, mock_sub, tmp_path):
        """Recent missions are parsed and passed to prompt template."""
        instance = tmp_path / "instance"
        instance.mkdir()
        (instance / "missions.md").write_text(
            "# Missions\n\n## Pending\n\n- task A\n- task B\n\n"
            "## In Progress\n\n- active task\n\n## Done\n"
        )
        mock_sub.run.return_value = MagicMock(returncode=0, stdout="Response")

        from skills.core.sparring.handler import handle
        handle(_make_ctx(instance))

        _, kwargs = mock_prompt.call_args
        assert "active task" in kwargs["RECENT_MISSIONS"]
        assert "task A" in kwargs["RECENT_MISSIONS"]

    @patch(_P_SUB)
    @patch(_P_PROMPT, return_value="test prompt")
    @patch(_P_MODEL, return_value=None)
    def test_no_missions_file(self, _model, mock_prompt, mock_sub, tmp_path):
        """Missing missions.md = empty RECENT_MISSIONS."""
        instance = tmp_path / "instance"
        instance.mkdir()
        mock_sub.run.return_value = MagicMock(returncode=0, stdout="Response")

        from skills.core.sparring.handler import handle
        handle(_make_ctx(instance))

        _, kwargs = mock_prompt.call_args
        assert kwargs["RECENT_MISSIONS"] == ""

    @patch(_P_SUB)
    @patch(_P_PROMPT, return_value="test prompt")
    @patch(_P_MODEL, return_value=None)
    def test_empty_missions(self, _model, mock_prompt, mock_sub, tmp_path):
        """missions.md with no pending/in-progress = empty RECENT_MISSIONS."""
        instance = tmp_path / "instance"
        instance.mkdir()
        (instance / "missions.md").write_text(
            "# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n\n- old\n"
        )
        mock_sub.run.return_value = MagicMock(returncode=0, stdout="Response")

        from skills.core.sparring.handler import handle
        handle(_make_ctx(instance))

        _, kwargs = mock_prompt.call_args
        assert kwargs["RECENT_MISSIONS"] == ""


# ---------------------------------------------------------------------------
# Time hint
# ---------------------------------------------------------------------------

class TestSparringTimeHint:
    """Test time-of-day hint passed to prompt."""

    @patch(_P_SUB)
    @patch(_P_PROMPT, return_value="test prompt")
    @patch(_P_MODEL, return_value=None)
    @patch("skills.core.sparring.handler.datetime")
    def test_morning_hint(self, mock_dt, _model, mock_prompt, mock_sub, tmp_path):
        instance = tmp_path / "instance"
        instance.mkdir()
        mock_dt.now.return_value.hour = 9
        mock_sub.run.return_value = MagicMock(returncode=0, stdout="Response")

        from skills.core.sparring.handler import handle
        handle(_make_ctx(instance))

        _, kwargs = mock_prompt.call_args
        assert "morning" in kwargs["TIME_HINT"].lower()

    @patch(_P_SUB)
    @patch(_P_PROMPT, return_value="test prompt")
    @patch(_P_MODEL, return_value=None)
    @patch("skills.core.sparring.handler.datetime")
    def test_late_night_hint(self, mock_dt, _model, mock_prompt, mock_sub, tmp_path):
        instance = tmp_path / "instance"
        instance.mkdir()
        mock_dt.now.return_value.hour = 23
        mock_sub.run.return_value = MagicMock(returncode=0, stdout="Response")

        from skills.core.sparring.handler import handle
        handle(_make_ctx(instance))

        _, kwargs = mock_prompt.call_args
        assert "night" in kwargs["TIME_HINT"].lower()

    @patch(_P_SUB)
    @patch(_P_PROMPT, return_value="test prompt")
    @patch(_P_MODEL, return_value=None)
    @patch("skills.core.sparring.handler.datetime")
    def test_afternoon_hint(self, mock_dt, _model, mock_prompt, mock_sub, tmp_path):
        instance = tmp_path / "instance"
        instance.mkdir()
        mock_dt.now.return_value.hour = 14
        mock_sub.run.return_value = MagicMock(returncode=0, stdout="Response")

        from skills.core.sparring.handler import handle
        handle(_make_ctx(instance))

        _, kwargs = mock_prompt.call_args
        assert "afternoon" in kwargs["TIME_HINT"].lower()

    @patch(_P_SUB)
    @patch(_P_PROMPT, return_value="test prompt")
    @patch(_P_MODEL, return_value=None)
    @patch("skills.core.sparring.handler.datetime")
    def test_evening_hint(self, mock_dt, _model, mock_prompt, mock_sub, tmp_path):
        instance = tmp_path / "instance"
        instance.mkdir()
        mock_dt.now.return_value.hour = 19
        mock_sub.run.return_value = MagicMock(returncode=0, stdout="Response")

        from skills.core.sparring.handler import handle
        handle(_make_ctx(instance))

        _, kwargs = mock_prompt.call_args
        assert "evening" in kwargs["TIME_HINT"].lower()


# ---------------------------------------------------------------------------
# Claude subprocess invocation
# ---------------------------------------------------------------------------

class TestSparringClaudeCall:
    """Test the Claude CLI invocation."""

    @patch(_P_SUB)
    @patch(_P_PROMPT, return_value="the prompt")
    @patch(_P_MODEL, return_value=None)
    def test_calls_claude_with_prompt(self, _model, _prompt, mock_sub, tmp_path):
        """Claude is called with -p and --max-turns 1."""
        instance = tmp_path / "instance"
        instance.mkdir()
        mock_sub.run.return_value = MagicMock(returncode=0, stdout="Response")

        from skills.core.sparring.handler import handle
        handle(_make_ctx(instance))

        cmd = mock_sub.run.call_args[0][0]
        assert cmd[0] == "claude"
        assert "-p" in cmd
        assert "--max-turns" in cmd
        assert "1" in cmd

    @patch(_P_SUB)
    @patch(_P_PROMPT, return_value="the prompt")
    @patch(_P_MODEL, return_value="haiku")
    def test_uses_fast_model_when_configured(self, _model, _prompt, mock_sub, tmp_path):
        """When fast_reply_model is set, --model flag is added."""
        instance = tmp_path / "instance"
        instance.mkdir()
        mock_sub.run.return_value = MagicMock(returncode=0, stdout="Response")

        from skills.core.sparring.handler import handle
        handle(_make_ctx(instance))

        cmd = mock_sub.run.call_args[0][0]
        assert "--model" in cmd
        model_idx = cmd.index("--model")
        assert cmd[model_idx + 1] == "haiku"

    @patch(_P_SUB)
    @patch(_P_PROMPT, return_value="the prompt")
    @patch(_P_MODEL, return_value=None)
    def test_no_model_flag_when_not_configured(self, _model, _prompt, mock_sub, tmp_path):
        """When no fast_reply_model, no --model flag."""
        instance = tmp_path / "instance"
        instance.mkdir()
        mock_sub.run.return_value = MagicMock(returncode=0, stdout="Response")

        from skills.core.sparring.handler import handle
        handle(_make_ctx(instance))

        cmd = mock_sub.run.call_args[0][0]
        assert "--model" not in cmd

    @patch(_P_SUB)
    @patch(_P_PROMPT, return_value="the prompt")
    @patch(_P_MODEL, return_value=None)
    def test_timeout_set_to_60s(self, _model, _prompt, mock_sub, tmp_path):
        """Subprocess is called with timeout=60."""
        instance = tmp_path / "instance"
        instance.mkdir()
        mock_sub.run.return_value = MagicMock(returncode=0, stdout="Response")

        from skills.core.sparring.handler import handle
        handle(_make_ctx(instance))

        kwargs = mock_sub.run.call_args[1]
        assert kwargs["timeout"] == 60


# ---------------------------------------------------------------------------
# Response handling
# ---------------------------------------------------------------------------

class TestSparringResponse:
    """Test response processing and error handling."""

    @patch(_P_SAVE)
    @patch(_P_SUB)
    @patch(_P_PROMPT, return_value="prompt")
    @patch(_P_MODEL, return_value=None)
    def test_successful_response_returned(self, _model, _prompt, mock_sub, _save, tmp_path):
        """Successful Claude output is returned."""
        instance = tmp_path / "instance"
        instance.mkdir()
        mock_sub.run.return_value = MagicMock(returncode=0, stdout="Deep thought here")

        from skills.core.sparring.handler import handle
        result = handle(_make_ctx(instance))
        assert result == "Deep thought here"

    @patch(_P_SAVE)
    @patch(_P_SUB)
    @patch(_P_PROMPT, return_value="prompt")
    @patch(_P_MODEL, return_value=None)
    def test_markdown_stripped_from_response(self, _model, _prompt, mock_sub, _save, tmp_path):
        """Bold and code block markers are removed from response."""
        instance = tmp_path / "instance"
        instance.mkdir()
        mock_sub.run.return_value = MagicMock(
            returncode=0, stdout="**Bold** and ```code```"
        )

        from skills.core.sparring.handler import handle
        result = handle(_make_ctx(instance))
        assert "**" not in result
        assert "```" not in result
        assert "Bold" in result

    @patch(_P_SAVE)
    @patch(_P_SUB)
    @patch(_P_PROMPT, return_value="prompt")
    @patch(_P_MODEL, return_value=None)
    def test_response_saved_to_history(self, _model, _prompt, mock_sub, mock_save, tmp_path):
        """Response is saved to telegram history."""
        instance = tmp_path / "instance"
        instance.mkdir()
        mock_sub.run.return_value = MagicMock(returncode=0, stdout="Deep thought")

        from skills.core.sparring.handler import handle
        handle(_make_ctx(instance))

        mock_save.assert_called_once()
        args = mock_save.call_args[0]
        assert args[1] == "assistant"
        assert args[2] == "Deep thought"

    @patch(_P_SUB)
    @patch(_P_PROMPT, return_value="prompt")
    @patch(_P_MODEL, return_value=None)
    def test_claude_failure_returns_fallback(self, _model, _prompt, mock_sub, tmp_path):
        """Non-zero exit code returns a fallback message."""
        instance = tmp_path / "instance"
        instance.mkdir()
        mock_sub.run.return_value = MagicMock(
            returncode=1, stdout="", stderr="error"
        )

        from skills.core.sparring.handler import handle
        result = handle(_make_ctx(instance))
        assert "Nothing compelling" in result

    @patch(_P_SUB)
    @patch(_P_PROMPT, return_value="prompt")
    @patch(_P_MODEL, return_value=None)
    def test_empty_output_returns_fallback(self, _model, _prompt, mock_sub, tmp_path):
        """Empty stdout returns fallback even with exit 0."""
        instance = tmp_path / "instance"
        instance.mkdir()
        mock_sub.run.return_value = MagicMock(returncode=0, stdout="  ")

        from skills.core.sparring.handler import handle
        result = handle(_make_ctx(instance))
        assert "Nothing compelling" in result

    @patch(_P_PROMPT, return_value="prompt")
    @patch(_P_MODEL, return_value=None)
    def test_timeout_returns_timeout_message(self, _model, _prompt, tmp_path):
        """Subprocess timeout returns a timeout message."""
        instance = tmp_path / "instance"
        instance.mkdir()
        with patch(_P_SUB + ".run", side_effect=subprocess.TimeoutExpired("claude", 60)):
            from skills.core.sparring.handler import handle
            result = handle(_make_ctx(instance))
        assert "Timeout" in result

    @patch(_P_PROMPT, return_value="prompt")
    @patch(_P_MODEL, return_value=None)
    def test_generic_exception_returns_error(self, _model, _prompt, tmp_path):
        """Generic exception returns error message."""
        instance = tmp_path / "instance"
        instance.mkdir()
        with patch(_P_SUB + ".run", side_effect=RuntimeError("something broke")):
            from skills.core.sparring.handler import handle
            result = handle(_make_ctx(instance))
        assert "Error" in result


# ---------------------------------------------------------------------------
# Notification
# ---------------------------------------------------------------------------

class TestSparringNotification:
    """Test the thinking notification."""

    @patch(_P_SUB)
    @patch(_P_PROMPT, return_value="prompt")
    @patch(_P_MODEL, return_value=None)
    def test_sends_thinking_message(self, _model, _prompt, mock_sub, tmp_path):
        """Sends a thinking notification when send_message is available."""
        instance = tmp_path / "instance"
        instance.mkdir()
        mock_sub.run.return_value = MagicMock(returncode=0, stdout="Response")
        send = MagicMock()

        from skills.core.sparring.handler import handle
        handle(_make_ctx(instance, send_message=send))

        send.assert_called_once()
        assert "thinking" in send.call_args[0][0].lower()

    @patch(_P_SUB)
    @patch(_P_PROMPT, return_value="prompt")
    @patch(_P_MODEL, return_value=None)
    def test_no_send_message_no_crash(self, _model, _prompt, mock_sub, tmp_path):
        """No send_message function = no notification, no crash."""
        instance = tmp_path / "instance"
        instance.mkdir()
        mock_sub.run.return_value = MagicMock(returncode=0, stdout="Response")

        from skills.core.sparring.handler import handle
        ctx = _make_ctx(instance, send_message=None)
        result = handle(ctx)
        assert result is not None
