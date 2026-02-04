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


def test_agent_prompt_has_branch_pr_notification_instructions():
    """Conclusion message should instruct agent to report branch name and PR link."""
    agent_prompt = (PROMPTS_DIR / "agent.md").read_text()

    # Must mention branch notification in conclusion section
    assert "pushed a branch" in agent_prompt
    # Must mention PR link
    assert "draft PR" in agent_prompt


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
