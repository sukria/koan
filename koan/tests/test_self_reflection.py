"""Tests for self_reflection module."""

import re
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.self_reflection import should_reflect, build_reflection_prompt, save_reflection, notify_outbox


@pytest.fixture
def instance_dir(tmp_path):
    """Create a minimal instance directory for testing."""
    memory_dir = tmp_path / "memory" / "global"
    memory_dir.mkdir(parents=True)
    (tmp_path / "soul.md").write_text("You are Kōan.")
    return tmp_path


class TestShouldReflect:
    def test_no_summary_file(self, instance_dir):
        assert should_reflect(instance_dir) is False

    def test_session_divisible_by_10(self, instance_dir):
        summary = instance_dir / "memory" / "summary.md"
        summary.write_text("Session 100 (project: koan) : test\n")
        assert should_reflect(instance_dir) is True

    def test_session_not_divisible_by_10(self, instance_dir):
        summary = instance_dir / "memory" / "summary.md"
        summary.write_text("Session 103 (project: koan) : test\n")
        assert should_reflect(instance_dir) is False

    def test_multiple_sessions_uses_max(self, instance_dir):
        summary = instance_dir / "memory" / "summary.md"
        summary.write_text(
            "Session 98 (project: koan) : a\n"
            "Session 99 (project: koan) : b\n"
            "Session 100 (project: koan) : c\n"
        )
        assert should_reflect(instance_dir) is True

    def test_custom_interval(self, instance_dir):
        summary = instance_dir / "memory" / "summary.md"
        summary.write_text("Session 15 (project: koan) : test\n")
        assert should_reflect(instance_dir, interval=5) is True
        assert should_reflect(instance_dir, interval=10) is False


class TestBuildReflectionPrompt:
    def test_includes_soul(self, instance_dir):
        prompt = build_reflection_prompt(instance_dir)
        assert "Kōan" in prompt

    def test_includes_summary(self, instance_dir):
        summary = instance_dir / "memory" / "summary.md"
        summary.write_text("Session 42 (project: koan) : audited codebase\n")
        prompt = build_reflection_prompt(instance_dir)
        assert "audited codebase" in prompt

    def test_includes_personality(self, instance_dir):
        personality = instance_dir / "memory" / "global" / "personality-evolution.md"
        personality.write_text("# Personality\n\n- I like tests\n")
        prompt = build_reflection_prompt(instance_dir)
        assert "I like tests" in prompt

    def test_includes_emotional_memory(self, instance_dir):
        emotional = instance_dir / "memory" / "global" / "emotional-memory.md"
        emotional.write_text("# Emotional\n\n- 'tu déchires mec'\n")
        prompt = build_reflection_prompt(instance_dir)
        assert "tu déchires" in prompt

    def test_has_reflection_instructions(self, instance_dir):
        prompt = build_reflection_prompt(instance_dir)
        assert "Patterns" in prompt
        assert "Growth" in prompt
        assert "Relationship" in prompt


class TestSaveReflection:
    def test_creates_new_file(self, instance_dir):
        personality = instance_dir / "memory" / "global" / "personality-evolution.md"
        personality.write_text("# Personality Evolution\n")
        save_reflection(instance_dir, "- I notice I love audits")
        content = personality.read_text()
        assert "Réflexion" in content
        assert "I notice I love audits" in content

    def test_appends_to_existing(self, instance_dir):
        personality = instance_dir / "memory" / "global" / "personality-evolution.md"
        personality.write_text("# Personality\n\n## Réflexion — 2026-01-01\n\n- old\n")
        save_reflection(instance_dir, "- new observation")
        content = personality.read_text()
        assert "old" in content
        assert "new observation" in content

    def test_includes_date(self, instance_dir):
        personality = instance_dir / "memory" / "global" / "personality-evolution.md"
        personality.write_text("# Personality\n")
        save_reflection(instance_dir, "- test")
        content = personality.read_text()
        assert re.search(r"## Réflexion — \d{4}-\d{2}-\d{2}", content)


class TestNotifyOutbox:
    def test_writes_to_outbox(self, instance_dir):
        notify_outbox(instance_dir, "- I notice patterns")
        outbox = instance_dir / "outbox.md"
        assert outbox.exists()
        content = outbox.read_text()
        assert "I notice patterns" in content
        assert "🪷" in content
        assert "personality-evolution.md" in content

    def test_overwrites_existing_outbox(self, instance_dir):
        outbox = instance_dir / "outbox.md"
        outbox.write_text("old message")
        notify_outbox(instance_dir, "- new reflection")
        content = outbox.read_text()
        assert "old message" not in content
        assert "new reflection" in content
