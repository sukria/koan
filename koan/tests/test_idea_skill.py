"""Tests for the /idea core skill — ideas backlog management."""

import re
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from app.missions import (
    parse_ideas,
    insert_idea,
    delete_idea,
    promote_idea,
)
from app.skills import SkillContext


# ---------------------------------------------------------------------------
# missions.py — parse_ideas
# ---------------------------------------------------------------------------

class TestParseIdeas:
    def test_empty_content(self):
        assert parse_ideas("") == []

    def test_no_ideas_section(self):
        content = "# Missions\n\n## Pending\n\n- task\n\n## Done\n"
        assert parse_ideas(content) == []

    def test_empty_ideas_section(self):
        content = "# Missions\n\n## Ideas\n\n## Pending\n\n## Done\n"
        assert parse_ideas(content) == []

    def test_single_idea(self):
        content = "# Missions\n\n## Ideas\n\n- my idea\n\n## Pending\n"
        assert parse_ideas(content) == ["- my idea"]

    def test_multiple_ideas(self):
        content = textwrap.dedent("""\
            # Missions

            ## Ideas

            - idea one
            - idea two
            - idea three

            ## Pending
        """)
        ideas = parse_ideas(content)
        assert len(ideas) == 3
        assert ideas[0] == "- idea one"
        assert ideas[2] == "- idea three"

    def test_ideas_with_project_tags(self):
        content = textwrap.dedent("""\
            # Missions

            ## Ideas

            - [project:koan] fix something
            - plain idea
            - [project:webapp] add feature

            ## Pending
        """)
        ideas = parse_ideas(content)
        assert len(ideas) == 3
        assert "[project:koan]" in ideas[0]

    def test_case_insensitive_header(self):
        content = "# Missions\n\n## ideas\n\n- lower case\n\n## Pending\n"
        assert parse_ideas(content) == ["- lower case"]

    def test_ideas_at_end_of_file(self):
        """Ideas section at end of file with no following section."""
        content = "# Missions\n\n## Pending\n\n## Done\n\n## Ideas\n\n- last idea\n"
        assert parse_ideas(content) == ["- last idea"]

    def test_ideas_section_not_confused_with_pending(self):
        """Items in Ideas should NOT appear in parse_sections."""
        from app.missions import parse_sections
        content = textwrap.dedent("""\
            # Missions

            ## Ideas

            - idea item
            - another idea

            ## Pending

            - pending item

            ## In Progress

            ## Done
        """)
        sections = parse_sections(content)
        ideas = parse_ideas(content)
        assert len(ideas) == 2
        assert len(sections["pending"]) == 1
        assert "idea item" not in str(sections["pending"])


# ---------------------------------------------------------------------------
# missions.py — insert_idea
# ---------------------------------------------------------------------------

class TestInsertIdea:
    def test_insert_into_existing_section(self):
        content = "# Missions\n\n## Ideas\n\n- existing\n\n## Pending\n\n## Done\n"
        result = insert_idea(content, "- new idea")
        ideas = parse_ideas(result)
        assert len(ideas) == 2
        assert "- new idea" in ideas
        assert "- existing" in ideas

    def test_insert_creates_section(self):
        content = "# Missions\n\n## Pending\n\n## Done\n"
        result = insert_idea(content, "- first idea")
        assert "## Ideas" in result
        ideas = parse_ideas(result)
        assert ideas == ["- first idea"]

    def test_insert_preserves_pending(self):
        content = "# Missions\n\n## Pending\n\n- task\n\n## Done\n"
        result = insert_idea(content, "- idea")
        from app.missions import parse_sections
        sections = parse_sections(result)
        assert len(sections["pending"]) == 1

    def test_insert_empty_content(self):
        result = insert_idea("", "- idea from nothing")
        assert "## Ideas" in result
        ideas = parse_ideas(result)
        assert ideas == ["- idea from nothing"]


# ---------------------------------------------------------------------------
# missions.py — delete_idea
# ---------------------------------------------------------------------------

class TestDeleteIdea:
    def test_delete_first(self):
        content = textwrap.dedent("""\
            # Missions

            ## Ideas

            - idea one
            - idea two
            - idea three

            ## Pending
        """)
        result, deleted = delete_idea(content, 1)
        assert deleted == "- idea one"
        ideas = parse_ideas(result)
        assert len(ideas) == 2
        assert "- idea one" not in ideas

    def test_delete_last(self):
        content = "# Missions\n\n## Ideas\n\n- a\n- b\n- c\n\n## Pending\n"
        result, deleted = delete_idea(content, 3)
        assert deleted == "- c"
        assert len(parse_ideas(result)) == 2

    def test_delete_out_of_range(self):
        content = "# Missions\n\n## Ideas\n\n- only one\n\n## Pending\n"
        result, deleted = delete_idea(content, 2)
        assert deleted is None
        assert result == content

    def test_delete_zero_index(self):
        content = "# Missions\n\n## Ideas\n\n- item\n\n## Pending\n"
        result, deleted = delete_idea(content, 0)
        assert deleted is None

    def test_delete_negative_index(self):
        content = "# Missions\n\n## Ideas\n\n- item\n\n## Pending\n"
        result, deleted = delete_idea(content, -1)
        assert deleted is None


# ---------------------------------------------------------------------------
# missions.py — promote_idea
# ---------------------------------------------------------------------------

class TestPromoteIdea:
    def test_promote_moves_to_pending(self):
        content = textwrap.dedent("""\
            # Missions

            ## Ideas

            - idea to promote
            - keep this

            ## Pending

            ## In Progress

            ## Done
        """)
        result, promoted = promote_idea(content, 1)
        assert promoted == "- idea to promote"

        from app.missions import parse_sections
        ideas = parse_ideas(result)
        sections = parse_sections(result)
        assert len(ideas) == 1
        assert "- keep this" in ideas[0]
        assert len(sections["pending"]) == 1
        assert "idea to promote" in sections["pending"][0]

    def test_promote_out_of_range(self):
        content = "# Missions\n\n## Ideas\n\n- only\n\n## Pending\n\n## Done\n"
        result, promoted = promote_idea(content, 5)
        assert promoted is None
        assert result == content

    def test_promote_with_project_tag(self):
        content = textwrap.dedent("""\
            # Missions

            ## Ideas

            - [project:koan] tagged idea

            ## Pending

            ## In Progress

            ## Done
        """)
        result, promoted = promote_idea(content, 1)
        assert promoted == "- [project:koan] tagged idea"
        from app.missions import parse_sections
        sections = parse_sections(result)
        assert "[project:koan]" in sections["pending"][0]


# ---------------------------------------------------------------------------
# Handler tests (direct handler invocation)
# ---------------------------------------------------------------------------

class TestIdeaHandler:
    def _make_ctx(self, tmp_path, missions_content=None, command="idea", args=""):
        instance_dir = tmp_path / "instance"
        instance_dir.mkdir(exist_ok=True)
        if missions_content is not None:
            (instance_dir / "missions.md").write_text(missions_content)
        return SkillContext(
            koan_root=tmp_path,
            instance_dir=instance_dir,
            command_name=command,
            args=args,
        )

    def test_no_missions_file(self, tmp_path):
        from skills.core.idea.handler import handle

        ctx = self._make_ctx(tmp_path, command="idea")
        result = handle(ctx)
        assert "No missions file" in result

    def test_list_empty(self, tmp_path):
        from skills.core.idea.handler import handle

        ctx = self._make_ctx(
            tmp_path,
            "# Missions\n\n## Ideas\n\n## Pending\n\n## Done\n",
            command="idea",
        )
        result = handle(ctx)
        assert "No ideas" in result

    def test_list_with_ideas(self, tmp_path):
        from skills.core.idea.handler import handle

        content = textwrap.dedent("""\
            # Missions

            ## Ideas

            - first idea
            - second idea

            ## Pending

            ## Done
        """)
        ctx = self._make_ctx(tmp_path, content, command="idea")
        result = handle(ctx)
        assert "IDEAS" in result
        assert "1. first idea" in result
        assert "2. second idea" in result

    def test_ideas_command_always_lists(self, tmp_path):
        from skills.core.idea.handler import handle

        content = "# Missions\n\n## Ideas\n\n- item\n\n## Pending\n\n## Done\n"
        ctx = self._make_ctx(tmp_path, content, command="ideas")
        result = handle(ctx)
        assert "IDEAS" in result
        assert "1. item" in result

    def test_add_idea(self, tmp_path):
        from skills.core.idea.handler import handle

        content = "# Missions\n\n## Ideas\n\n## Pending\n\n## Done\n"
        ctx = self._make_ctx(tmp_path, content, command="idea", args="my new idea")
        result = handle(ctx)
        assert "Idea saved" in result
        assert "my new idea" in result

        # Verify it was written to the file
        written = (tmp_path / "instance" / "missions.md").read_text()
        assert "my new idea" in written

    def test_add_idea_with_project_tag(self, tmp_path):
        from skills.core.idea.handler import handle

        content = "# Missions\n\n## Ideas\n\n## Pending\n\n## Done\n"
        ctx = self._make_ctx(
            tmp_path, content, command="idea",
            args="[project:koan] fix something",
        )
        result = handle(ctx)
        assert "Idea saved" in result

        written = (tmp_path / "instance" / "missions.md").read_text()
        assert "[project:koan]" in written

    def test_buffer_alias_adds(self, tmp_path):
        from skills.core.idea.handler import handle

        content = "# Missions\n\n## Ideas\n\n## Pending\n\n## Done\n"
        ctx = self._make_ctx(tmp_path, content, command="buffer", args="buffer idea")
        result = handle(ctx)
        assert "Idea saved" in result

    def test_delete_idea(self, tmp_path):
        from skills.core.idea.handler import handle

        content = "# Missions\n\n## Ideas\n\n- to delete\n- to keep\n\n## Pending\n\n## Done\n"
        ctx = self._make_ctx(tmp_path, content, command="idea", args="delete 1")
        result = handle(ctx)
        assert "Deleted" in result
        assert "to delete" in result

        written = (tmp_path / "instance" / "missions.md").read_text()
        assert "to delete" not in written
        assert "to keep" in written

    def test_delete_with_rm_alias(self, tmp_path):
        from skills.core.idea.handler import handle

        content = "# Missions\n\n## Ideas\n\n- item\n\n## Pending\n\n## Done\n"
        ctx = self._make_ctx(tmp_path, content, command="idea", args="rm 1")
        result = handle(ctx)
        assert "Deleted" in result

    def test_delete_invalid_index(self, tmp_path):
        from skills.core.idea.handler import handle

        content = "# Missions\n\n## Ideas\n\n- only one\n\n## Pending\n\n## Done\n"
        ctx = self._make_ctx(tmp_path, content, command="idea", args="delete 5")
        result = handle(ctx)
        assert "Invalid index" in result

    def test_promote_idea(self, tmp_path):
        from skills.core.idea.handler import handle

        content = textwrap.dedent("""\
            # Missions

            ## Ideas

            - promote me

            ## Pending

            ## In Progress

            ## Done
        """)
        ctx = self._make_ctx(tmp_path, content, command="idea", args="promote 1")
        result = handle(ctx)
        assert "Promoted to pending" in result
        assert "promote me" in result

        written = (tmp_path / "instance" / "missions.md").read_text()
        ideas = parse_ideas(written)
        assert len(ideas) == 0
        from app.missions import parse_sections
        sections = parse_sections(written)
        assert any("promote me" in p for p in sections["pending"])

    def test_promote_with_push_alias(self, tmp_path):
        from skills.core.idea.handler import handle

        content = "# Missions\n\n## Ideas\n\n- pushme\n\n## Pending\n\n## In Progress\n\n## Done\n"
        ctx = self._make_ctx(tmp_path, content, command="idea", args="push 1")
        result = handle(ctx)
        assert "Promoted to pending" in result

    def test_promote_invalid_index(self, tmp_path):
        from skills.core.idea.handler import handle

        content = "# Missions\n\n## Ideas\n\n- only\n\n## Pending\n\n## Done\n"
        ctx = self._make_ctx(tmp_path, content, command="idea", args="promote 99")
        result = handle(ctx)
        assert "Invalid index" in result


# ---------------------------------------------------------------------------
# _clean_idea helper
# ---------------------------------------------------------------------------

class TestCleanIdea:
    def test_strip_dash(self):
        from app.missions import clean_mission_display
        assert clean_mission_display("- simple idea") == "simple idea"

    def test_strip_project_tag(self):
        from app.missions import clean_mission_display
        result = clean_mission_display("- [project:koan] fix parser")
        assert result == "[koan] fix parser"

    def test_truncation(self):
        from app.missions import clean_mission_display
        long = "- " + "x" * 200
        result = clean_mission_display(long)
        assert result.endswith("...")
        assert len(result) == 120


# ---------------------------------------------------------------------------
# Integration: command routing via awake.py
# ---------------------------------------------------------------------------

class TestIdeaCommandRouting:
    @patch("app.awake.send_telegram")
    def test_idea_routes_via_skill(self, mock_send, tmp_path):
        from app.awake import handle_command

        missions_file = tmp_path / "missions.md"
        missions_file.write_text(
            "# Missions\n\n## Ideas\n\n- test idea\n\n## Pending\n\n## In Progress\n\n## Done\n"
        )
        with patch("app.awake.KOAN_ROOT", tmp_path), \
             patch("app.awake.INSTANCE_DIR", tmp_path), \
             patch("app.awake.MISSIONS_FILE", missions_file):
            handle_command("/idea")
        mock_send.assert_called_once()
        output = mock_send.call_args[0][0]
        assert "IDEAS" in output
        assert "test idea" in output

    @patch("app.awake.send_telegram")
    def test_ideas_routes_via_skill(self, mock_send, tmp_path):
        from app.awake import handle_command

        missions_file = tmp_path / "missions.md"
        missions_file.write_text(
            "# Missions\n\n## Ideas\n\n- listed idea\n\n## Pending\n\n## In Progress\n\n## Done\n"
        )
        with patch("app.awake.KOAN_ROOT", tmp_path), \
             patch("app.awake.INSTANCE_DIR", tmp_path), \
             patch("app.awake.MISSIONS_FILE", missions_file):
            handle_command("/ideas")
        mock_send.assert_called_once()
        assert "listed idea" in mock_send.call_args[0][0]

    @patch("app.awake.send_telegram")
    def test_buffer_routes_via_skill(self, mock_send, tmp_path):
        from app.awake import handle_command

        missions_file = tmp_path / "missions.md"
        missions_file.write_text(
            "# Missions\n\n## Ideas\n\n## Pending\n\n## In Progress\n\n## Done\n"
        )
        with patch("app.awake.KOAN_ROOT", tmp_path), \
             patch("app.awake.INSTANCE_DIR", tmp_path), \
             patch("app.awake.MISSIONS_FILE", missions_file):
            handle_command("/buffer new buffered idea")
        mock_send.assert_called_once()
        assert "Idea saved" in mock_send.call_args[0][0]

    @patch("app.awake.send_telegram")
    def test_idea_appears_in_help(self, mock_send, tmp_path):
        from app.awake import handle_command

        with patch("app.awake.KOAN_ROOT", tmp_path), \
             patch("app.awake.INSTANCE_DIR", tmp_path):
            handle_command("/help")
        mock_send.assert_called_once()
        help_text = mock_send.call_args[0][0]
        assert "/idea" in help_text
