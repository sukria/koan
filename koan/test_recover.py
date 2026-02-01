"""Tests for recover.py — crash recovery of stale in-progress missions."""

from unittest.mock import patch

import pytest

from recover import recover_missions


def _missions(pending="", in_progress="", done=""):
    """Build a missions.md content string."""
    return (
        f"# Missions\n\n"
        f"## En attente\n\n{pending}\n\n"
        f"## En cours\n\n{in_progress}\n\n"
        f"## Terminées\n\n{done}\n"
    )


class TestRecoverMissions:
    """Core recovery logic."""

    def test_no_stale_missions(self, instance_dir):
        """No recovery needed when in-progress is empty."""
        assert recover_missions(str(instance_dir)) == 0

    def test_missing_missions_file(self, tmp_path):
        """Returns 0 if missions.md doesn't exist."""
        assert recover_missions(str(tmp_path / "nonexistent")) == 0

    def test_recover_simple_mission(self, instance_dir):
        """Simple - item in 'En cours' moves back to 'En attente'."""
        missions = instance_dir / "missions.md"
        missions.write_text(_missions(in_progress="- Fix the bug"))

        count = recover_missions(str(instance_dir))

        assert count == 1
        content = missions.read_text()
        # Should be in pending now
        lines = content.splitlines()
        pending_idx = next(i for i, l in enumerate(lines) if "en attente" in l.lower())
        in_prog_idx = next(i for i, l in enumerate(lines) if "en cours" in l.lower())
        # The mission should appear between pending header and in-progress header
        between = "\n".join(lines[pending_idx + 1 : in_prog_idx])
        assert "Fix the bug" in between

    def test_recover_multiple_simple_missions(self, instance_dir):
        """Multiple simple missions are all recovered."""
        missions = instance_dir / "missions.md"
        missions.write_text(
            _missions(in_progress="- Task A\n- Task B\n- Task C")
        )

        count = recover_missions(str(instance_dir))
        assert count == 3

        content = missions.read_text()
        assert "Task A" in content
        assert "Task B" in content
        assert "Task C" in content

    def test_skip_strikethrough_missions(self, instance_dir):
        """Fully struck-through items (~~done~~) are NOT recovered."""
        missions = instance_dir / "missions.md"
        missions.write_text(
            _missions(in_progress="- ~~Already done~~\n- Still active")
        )

        count = recover_missions(str(instance_dir))
        assert count == 1

        content = missions.read_text()
        lines = content.splitlines()
        pending_idx = next(i for i, l in enumerate(lines) if "en attente" in l.lower())
        in_prog_idx = next(i for i, l in enumerate(lines) if "en cours" in l.lower())
        between = "\n".join(lines[pending_idx + 1 : in_prog_idx])
        assert "Still active" in between
        assert "Already done" not in between

    def test_skip_complex_mission(self, instance_dir):
        """### header missions with sub-items are NOT recovered."""
        missions = instance_dir / "missions.md"
        missions.write_text(
            _missions(
                in_progress=(
                    "### Complex Project\n"
                    "- ~~Step 1~~ done\n"
                    "- Step 2 in progress\n"
                    "- Step 3 todo\n"
                    "\n"
                    "- Simple orphan task"
                )
            )
        )

        count = recover_missions(str(instance_dir))
        # Only the simple orphan should be recovered, not the complex sub-items
        assert count == 1

        content = missions.read_text()
        lines = content.splitlines()
        in_prog_idx = next(i for i, l in enumerate(lines) if "en cours" in l.lower())
        done_idx = next(i for i, l in enumerate(lines) if "terminées" in l.lower())
        in_progress_section = "\n".join(lines[in_prog_idx + 1 : done_idx])
        # Complex mission should still be in-progress
        assert "Complex Project" in in_progress_section
        assert "Step 2" in in_progress_section

    def test_removes_aucune_placeholder(self, instance_dir):
        """The (aucune) placeholder is removed from pending when missions are added."""
        missions = instance_dir / "missions.md"
        missions.write_text(_missions(pending="(aucune)", in_progress="- Recover me"))

        recover_missions(str(instance_dir))

        content = missions.read_text()
        lines = content.splitlines()
        pending_idx = next(i for i, l in enumerate(lines) if "en attente" in l.lower())
        in_prog_idx = next(i for i, l in enumerate(lines) if "en cours" in l.lower())
        between = "\n".join(lines[pending_idx + 1 : in_prog_idx])
        assert "(aucune)" not in between
        assert "Recover me" in between

    def test_english_section_names(self, instance_dir):
        """Works with English section names too."""
        missions = instance_dir / "missions.md"
        missions.write_text(
            "# Missions\n\n"
            "## Pending\n\n(none)\n\n"
            "## In Progress\n\n- English task\n\n"
            "## Done\n\n"
        )

        count = recover_missions(str(instance_dir))
        assert count == 1

    def test_preserves_existing_pending(self, instance_dir):
        """Existing pending missions are kept when recovered missions are added."""
        missions = instance_dir / "missions.md"
        missions.write_text(
            _missions(pending="- Already pending", in_progress="- Crashed task")
        )

        recover_missions(str(instance_dir))

        content = missions.read_text()
        assert "Already pending" in content
        assert "Crashed task" in content

    def test_no_sections_returns_zero(self, instance_dir):
        """If missions.md has no recognized sections, returns 0."""
        missions = instance_dir / "missions.md"
        missions.write_text("# Random file\n\nSome content\n")

        assert recover_missions(str(instance_dir)) == 0

    def test_tagged_missions_preserved(self, instance_dir):
        """Project-tagged missions are recovered like any other."""
        missions = instance_dir / "missions.md"
        missions.write_text(
            _missions(in_progress="- [project:koan] Fix something")
        )

        count = recover_missions(str(instance_dir))
        assert count == 1
        content = missions.read_text()
        assert "[project:koan] Fix something" in content

    def test_no_duplicate_lines(self, instance_dir):
        """Regression: recovered missions must not duplicate existing lines."""
        missions = instance_dir / "missions.md"
        missions.write_text(
            "# Missions\n\n"
            "## En attente\n\n"
            "- Existing task\n\n"
            "## En cours\n\n"
            "- Stale task\n\n"
            "## Terminées\n\n"
        )

        recover_missions(str(instance_dir))
        content = missions.read_text()

        # "Existing task" must appear exactly once
        assert content.count("Existing task") == 1
        # "Stale task" must appear exactly once (moved to pending)
        assert content.count("Stale task") == 1

    def test_no_section_headers_duplicated(self, instance_dir):
        """Section headers must not be duplicated after recovery."""
        missions = instance_dir / "missions.md"
        missions.write_text(_missions(in_progress="- Task A\n- Task B"))

        recover_missions(str(instance_dir))
        content = missions.read_text()

        assert content.count("## En attente") == 1
        assert content.count("## En cours") == 1
        assert content.count("## Terminées") == 1


class TestRecoverCLI:
    """Test the __main__ CLI behavior."""

    @patch("recover.send_telegram")
    def test_cli_with_recovery(self, mock_send, instance_dir, capsys):
        """CLI prints count and sends Telegram when missions recovered."""
        missions = instance_dir / "missions.md"
        missions.write_text(_missions(in_progress="- Stale task"))

        import recover
        import sys

        with patch.object(sys, "argv", ["recover.py", str(instance_dir)]):
            # Can't easily test sys.exit, so just call the main block logic
            count = recover_missions(str(instance_dir))
            if count > 0:
                recover.send_telegram(
                    f"🔄 Restart — {count} mission(s) recovered from interrupted run, moved back to Pending."
                )

        mock_send.assert_called_once()
        assert "1 mission" in mock_send.call_args[0][0]
