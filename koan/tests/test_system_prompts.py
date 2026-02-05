"""Tests for system prompt templates."""

from pathlib import Path

PROMPTS_DIR = Path(__file__).parent.parent / "system-prompts"


def test_agent_prompt_has_audit_github_issue_instructions():
    """Audit missions should be instructed to create GitHub issues when appropriate."""
    agent_prompt = (PROMPTS_DIR / "agent.md").read_text()

    # Must have the audit section header
    assert "# Audit Missions" in agent_prompt

    # Must mention gh issue create
    assert "gh issue create" in agent_prompt

    # Must have skip conditions (don't create issues for trivial findings)
    assert "Skip issue creation when" in agent_prompt

    # Must check for GitHub remote first
    assert "gh repo view" in agent_prompt


def test_agent_prompt_has_all_required_placeholders():
    """Agent prompt should have all required placeholders for run.sh substitution."""
    agent_prompt = (PROMPTS_DIR / "agent.md").read_text()

    required_placeholders = [
        "{INSTANCE}",
        "{PROJECT_NAME}",
        "{PROJECT_PATH}",
        "{RUN_NUM}",
        "{MAX_RUNS}",
        "{AUTONOMOUS_MODE}",
        "{FOCUS_AREA}",
        "{AVAILABLE_PCT}",
        "{MISSION_INSTRUCTION}",
    ]

    for placeholder in required_placeholders:
        assert placeholder in agent_prompt, f"Missing placeholder: {placeholder}"


def test_all_prompts_exist():
    """All referenced prompt files should exist."""
    expected_prompts = [
        "agent.md",
        "chat.md",
        "contemplative.md",
        "format-telegram.md",
        "pick-mission.md",
        "sparring.md",
        "usage-status.md",
        "dashboard-chat.md",
    ]

    for prompt_name in expected_prompts:
        prompt_path = PROMPTS_DIR / prompt_name
        assert prompt_path.exists(), f"Missing prompt: {prompt_name}"


def test_contemplative_prompt_has_required_placeholders():
    """Contemplative prompt should have all required placeholders."""
    prompt = (PROMPTS_DIR / "contemplative.md").read_text()

    required_placeholders = [
        "{INSTANCE}",
        "{PROJECT_NAME}",
        "{SESSION_INFO}",
    ]

    for placeholder in required_placeholders:
        assert placeholder in prompt, f"Missing placeholder: {placeholder}"


def test_contemplative_prompt_has_reflection_topics():
    """Contemplative prompt should have structured reflection topics."""
    prompt = (PROMPTS_DIR / "contemplative.md").read_text()

    # Must have the four topic categories
    assert "**Retrospective**" in prompt, "Missing Retrospective section"
    assert "**Relational**" in prompt, "Missing Relational section"
    assert "**Strategic**" in prompt, "Missing Strategic section"
    assert "**Philosophical**" in prompt, "Missing Philosophical section"


def test_contemplative_prompt_requires_output():
    """Contemplative prompt should require at least one output type."""
    prompt = (PROMPTS_DIR / "contemplative.md").read_text()

    # Must have Required Output section
    assert "# Required Output" in prompt, "Missing Required Output section"

    # Must describe output options
    assert "Option 1: Learning" in prompt
    assert "Option 2: Mission Proposal" in prompt
    assert "Option 3: Question for Alexis" in prompt
    assert "Option 4: Kōan" in prompt

    # Must reference outbox for output delivery
    assert "outbox.md" in prompt


def test_contemplative_prompt_anti_noise_rules():
    """Contemplative prompt should have anti-noise guidance."""
    prompt = (PROMPTS_DIR / "contemplative.md").read_text()

    # Must discourage empty/generic output
    assert "silence" in prompt.lower() or "silent" in prompt.lower()
    assert "noise" in prompt.lower() or "generic" in prompt.lower()
