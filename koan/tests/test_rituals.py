"""Tests for rituals module."""

import subprocess
import sys
from unittest.mock import patch, MagicMock

import pytest

from app.rituals import load_template, should_run_morning, should_run_evening, run_ritual


@pytest.fixture
def instance_dir(tmp_path):
    """Create a minimal instance directory for testing."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True)
    (tmp_path / "soul.md").write_text("You are Koan.")
    return tmp_path


@pytest.fixture
def prompt_dir(tmp_path):
    """Create a minimal prompt directory with ritual templates."""
    prompts = tmp_path / "prompts"
    prompts.mkdir()

    (prompts / "morning-brief.md").write_text(
        "Morning brief template. Instance: {INSTANCE}"
    )
    (prompts / "evening-debrief.md").write_text(
        "Evening debrief template. Instance: {INSTANCE}"
    )

    def _get_prompt_path(name):
        return prompts / f"{name}.md"

    with patch("app.rituals.get_prompt_path", side_effect=_get_prompt_path):
        yield prompts


class TestShouldRunMorning:
    def test_first_run_triggers_morning(self):
        assert should_run_morning(1) is True

    def test_second_run_no_morning(self):
        assert should_run_morning(2) is False

    def test_later_run_no_morning(self):
        assert should_run_morning(5) is False


class TestShouldRunEvening:
    def test_last_run_triggers_evening(self):
        assert should_run_evening(10, 10) is True

    def test_not_last_run_no_evening(self):
        assert should_run_evening(5, 10) is False

    def test_first_run_no_evening_unless_max_is_1(self):
        assert should_run_evening(1, 10) is False
        assert should_run_evening(1, 1) is True


class TestLoadTemplate:
    def test_loads_morning_template(self, prompt_dir, instance_dir):
        template = load_template("morning-brief", instance_dir)
        assert "Morning brief template" in template
        assert str(instance_dir) in template

    def test_loads_evening_template(self, prompt_dir, instance_dir):
        template = load_template("evening-debrief", instance_dir)
        assert "Evening debrief template" in template
        assert str(instance_dir) in template

    def test_replaces_instance_placeholder(self, prompt_dir, instance_dir):
        template = load_template("morning-brief", instance_dir)
        assert "{INSTANCE}" not in template
        assert str(instance_dir) in template

    def test_raises_for_missing_template(self, prompt_dir, instance_dir):
        with pytest.raises(FileNotFoundError):
            load_template("nonexistent", instance_dir)


class TestRunRitual:
    @patch("app.rituals.subprocess.run")
    def test_morning_calls_claude(self, mock_run, prompt_dir, instance_dir):
        mock_run.return_value = MagicMock(returncode=0, stdout="Morning message", stderr="")

        result = run_ritual("morning", instance_dir)

        assert result is True
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert call_args[0] == "claude"
        assert "-p" in call_args

    @patch("app.rituals.subprocess.run")
    def test_evening_calls_claude(self, mock_run, prompt_dir, instance_dir):
        mock_run.return_value = MagicMock(returncode=0, stdout="Evening message", stderr="")

        result = run_ritual("evening", instance_dir)

        assert result is True
        mock_run.assert_called_once()

    @patch("app.rituals.subprocess.run")
    def test_handles_claude_failure(self, mock_run, prompt_dir, instance_dir):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Error")

        result = run_ritual("morning", instance_dir)

        assert result is False

    @patch("app.rituals.subprocess.run")
    def test_handles_timeout(self, mock_run, prompt_dir, instance_dir):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="claude", timeout=90)

        result = run_ritual("morning", instance_dir)

        assert result is False

    def test_returns_false_for_missing_template(self, tmp_path, instance_dir):
        # Point get_prompt_path to a directory without templates
        with patch("app.rituals.get_prompt_path", side_effect=lambda n: tmp_path / f"{n}.md"):
            result = run_ritual("morning", instance_dir)

        assert result is False

    @patch("app.rituals.subprocess.run")
    def test_handles_generic_exception(self, mock_run, prompt_dir, instance_dir):
        mock_run.side_effect = OSError("Permission denied")

        result = run_ritual("morning", instance_dir)
        assert result is False

    @patch("app.rituals.subprocess.run")
    def test_empty_stdout(self, mock_run, prompt_dir, instance_dir):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        result = run_ritual("morning", instance_dir)
        assert result is True


class TestRitualsCLI:
    """CLI tests call main() directly to avoid runpy re-import issues."""

    @patch("app.rituals.subprocess.run")
    def test_main_morning(self, mock_subprocess, prompt_dir, instance_dir):
        mock_subprocess.return_value = MagicMock(returncode=0, stdout="Morning!", stderr="")

        from app.rituals import main
        with patch.object(sys, "argv", ["rituals.py", "morning", str(instance_dir)]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    @patch("app.rituals.subprocess.run")
    def test_main_evening(self, mock_subprocess, prompt_dir, instance_dir):
        mock_subprocess.return_value = MagicMock(returncode=0, stdout="Evening!", stderr="")

        from app.rituals import main
        with patch.object(sys, "argv", ["rituals.py", "evening", str(instance_dir)]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_main_missing_args(self):
        from app.rituals import main
        with patch.object(sys, "argv", ["rituals.py"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1

    def test_main_invalid_ritual_type(self, instance_dir):
        from app.rituals import main
        with patch.object(sys, "argv", ["rituals.py", "midnight", str(instance_dir)]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1

    def test_main_missing_instance_dir(self, tmp_path):
        from app.rituals import main
        with patch.object(sys, "argv", ["rituals.py", "morning", str(tmp_path / "nonexistent")]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1

    @patch("app.rituals.subprocess.run")
    def test_main_failure_exits_1(self, mock_subprocess, prompt_dir, instance_dir):
        mock_subprocess.return_value = MagicMock(returncode=1, stdout="", stderr="Error")

        from app.rituals import main
        with patch.object(sys, "argv", ["rituals.py", "morning", str(instance_dir)]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1
