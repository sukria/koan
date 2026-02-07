"""Tests for prompts.py — system prompt loader and placeholder substitution."""

from pathlib import Path
from unittest.mock import patch

import pytest

from app.prompts import PROMPT_DIR, _substitute, load_prompt, load_skill_prompt


# ---------- _substitute ----------


class TestSubstitute:
    """Tests for placeholder substitution."""

    def test_single_placeholder(self):
        assert _substitute("Hello {NAME}!", {"NAME": "World"}) == "Hello World!"

    def test_multiple_placeholders(self):
        template = "{A} and {B}"
        assert _substitute(template, {"A": "one", "B": "two"}) == "one and two"

    def test_repeated_placeholder(self):
        template = "{X} then {X} again"
        assert _substitute(template, {"X": "val"}) == "val then val again"

    def test_no_placeholders(self):
        assert _substitute("plain text", {}) == "plain text"

    def test_missing_placeholder_left_as_is(self):
        assert _substitute("Hello {NAME}!", {}) == "Hello {NAME}!"

    def test_non_string_value_converted(self):
        assert _substitute("count: {N}", {"N": 42}) == "count: 42"

    def test_empty_string_value(self):
        assert _substitute("x{V}y", {"V": ""}) == "xy"


# ---------- load_prompt ----------


class TestLoadPrompt:
    """Tests for loading prompts from koan/system-prompts/."""

    def test_load_chat_prompt(self):
        result = load_prompt("chat")
        assert len(result) > 0
        assert isinstance(result, str)

    def test_load_format_telegram(self):
        result = load_prompt("format-telegram")
        assert len(result) > 0

    def test_load_agent(self):
        result = load_prompt("agent")
        assert len(result) > 0

    def test_load_contemplative(self):
        result = load_prompt("contemplative")
        assert len(result) > 0

    def test_nonexistent_prompt_raises(self):
        with pytest.raises(FileNotFoundError):
            load_prompt("this-does-not-exist-at-all")

    def test_placeholder_substitution(self):
        """Prompts with placeholders should have them replaced."""
        # Read a prompt that has known placeholders
        raw = (PROMPT_DIR / "chat.md").read_text()
        # If it has any {KEY} patterns, test substitution works
        if "{" in raw:
            # Just verify load_prompt doesn't crash with kwargs
            result = load_prompt("chat", SOUL="test soul", MEMORY="test memory")
            assert isinstance(result, str)

    def test_prompt_dir_exists(self):
        assert PROMPT_DIR.exists()
        assert PROMPT_DIR.is_dir()

    def test_all_system_prompts_loadable(self):
        """Every .md file in system-prompts/ should be loadable."""
        for md_file in PROMPT_DIR.glob("*.md"):
            name = md_file.stem
            result = load_prompt(name)
            assert len(result) > 0, f"Prompt {name} is empty"


# ---------- load_skill_prompt ----------


class TestLoadSkillPrompt:
    """Tests for loading prompts from skill directories."""

    def test_load_from_skill_dir(self, tmp_path):
        """When prompt exists in skill dir, use it."""
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "test.md").write_text("Skill prompt {VAR}")
        result = load_skill_prompt(tmp_path, "test", VAR="value")
        assert result == "Skill prompt value"

    def test_fallback_to_system_prompts(self, tmp_path):
        """When prompt missing from skill dir, fall back to system-prompts/."""
        # tmp_path has no prompts/ dir, so should fall back
        result = load_skill_prompt(tmp_path, "chat")
        # Should get the system chat prompt
        assert len(result) > 0

    def test_skill_prompt_takes_priority(self, tmp_path):
        """Skill-specific prompt overrides system prompt."""
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        # Create a prompt with the same name as a system prompt
        (prompts_dir / "chat.md").write_text("Custom skill chat prompt")
        result = load_skill_prompt(tmp_path, "chat")
        assert result == "Custom skill chat prompt"

    def test_nonexistent_in_both_raises(self, tmp_path):
        """If prompt doesn't exist in skill or system dir, raise."""
        with pytest.raises(FileNotFoundError):
            load_skill_prompt(tmp_path, "totally-nonexistent-prompt-xyz")

    def test_substitution_works(self, tmp_path):
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "review.md").write_text("Review {PROJECT} for {GOAL}")
        result = load_skill_prompt(
            tmp_path, "review", PROJECT="koan", GOAL="quality"
        )
        assert result == "Review koan for quality"

    def test_real_skill_prompts_loadable(self):
        """All existing skill prompts should be loadable."""
        skills_dir = Path(__file__).parent.parent / "skills" / "core"
        if not skills_dir.exists():
            pytest.skip("skills/core not found")
        for skill_dir in skills_dir.iterdir():
            prompts = skill_dir / "prompts"
            if prompts.exists():
                for md_file in prompts.glob("*.md"):
                    result = load_skill_prompt(skill_dir, md_file.stem)
                    assert len(result) > 0, f"{skill_dir.name}/{md_file.stem} is empty"
