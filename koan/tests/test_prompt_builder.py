"""Tests for prompt_builder.py — agent and contemplative prompt assembly."""

import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.prompt_builder import (
    build_agent_prompt,
    build_contemplative_prompt,
    _is_auto_merge_enabled,
    _get_merge_policy,
    _get_audit_section,
    _get_deep_research,
    _get_verbose_section,
)


@pytest.fixture
def prompt_env(tmp_path):
    """Create a minimal environment for prompt builder testing."""
    instance = tmp_path / "instance"
    project_path = tmp_path / "project"
    project_name = "testproj"

    # Create directory structure
    (instance / "memory" / "projects" / project_name).mkdir(parents=True)
    (instance / "journal").mkdir(parents=True)
    project_path.mkdir()

    return {
        "instance": str(instance),
        "project_path": str(project_path),
        "project_name": project_name,
        "koan_root": str(tmp_path),
    }


# --- Tests for _is_auto_merge_enabled ---


class TestIsAutoMergeEnabled:
    """Tests for auto-merge config detection."""

    @patch("app.config.get_auto_merge_config")
    @patch("app.prompt_builder._load_config_safe")
    def test_enabled_with_rules(self, mock_load_config, mock_merge_cfg):
        mock_load_config.return_value = {}
        mock_merge_cfg.return_value = {"enabled": True, "rules": ["*"]}
        assert _is_auto_merge_enabled("myproject") is True

    @patch("app.config.get_auto_merge_config")
    @patch("app.prompt_builder._load_config_safe")
    def test_disabled(self, mock_load_config, mock_merge_cfg):
        mock_load_config.return_value = {}
        mock_merge_cfg.return_value = {"enabled": False, "rules": ["*"]}
        assert _is_auto_merge_enabled("myproject") is False

    @patch("app.config.get_auto_merge_config")
    @patch("app.prompt_builder._load_config_safe")
    def test_enabled_no_rules(self, mock_load_config, mock_merge_cfg):
        mock_load_config.return_value = {}
        mock_merge_cfg.return_value = {"enabled": True, "rules": []}
        assert _is_auto_merge_enabled("myproject") is False

    @patch("app.prompt_builder._load_config_safe", side_effect=Exception("no config"))
    def test_config_error_returns_false(self, _mock):
        assert _is_auto_merge_enabled("myproject") is False


# --- Tests for _get_merge_policy ---


class TestGetMergePolicy:
    """Tests for merge policy text generation."""

    @patch("app.prompt_builder._get_branch_prefix", return_value="koan/")
    @patch("app.prompt_builder._is_auto_merge_enabled", return_value=True)
    def test_auto_merge_enabled(self, _mock_merge, _mock_prefix):
        policy = _get_merge_policy("proj")
        assert "Auto-Merge Enabled" in policy
        assert "auto-merge system handles the merge" in policy

    @patch("app.prompt_builder._get_branch_prefix", return_value="koan/")
    @patch("app.prompt_builder._is_auto_merge_enabled", return_value=False)
    def test_auto_merge_disabled(self, _mock_merge, _mock_prefix):
        policy = _get_merge_policy("proj")
        assert "NOT configured" in policy
        assert "DO NOT merge yourself" in policy


# --- Tests for _get_audit_section ---


class TestGetAuditSection:
    """Tests for conditional audit section injection."""

    def test_audit_mission_includes_section(self):
        result = _get_audit_section("Security audit of auth module", "/tmp/project")
        assert "Audit Missions" in result
        assert "GitHub Issue Follow-up" in result
        assert "/tmp/project" in result

    def test_non_audit_mission_returns_empty(self):
        result = _get_audit_section("Fix the login bug", "/tmp/project")
        assert result == ""

    def test_empty_mission_returns_empty(self):
        result = _get_audit_section("", "/tmp/project")
        assert result == ""

    def test_audit_case_insensitive(self):
        result = _get_audit_section("Code AUDIT for dependencies", "/tmp/project")
        assert "Audit Missions" in result

    def test_audit_substring_match(self):
        """'audit' embedded in a word should still match."""
        result = _get_audit_section("Run a security audit-lite check", "/tmp/project")
        assert "Audit Missions" in result


# --- Tests for _get_deep_research ---


class TestGetDeepResearch:
    """Tests for deep research injection."""

    @patch("app.deep_research.DeepResearch")
    def test_with_suggestions(self, mock_cls, prompt_env):
        mock_instance = MagicMock()
        mock_instance.format_for_agent.return_value = "## Topic 1\n- Something"
        mock_cls.return_value = mock_instance

        result = _get_deep_research(
            prompt_env["instance"],
            prompt_env["project_name"],
            prompt_env["project_path"],
        )
        assert "# Deep Research Analysis" in result
        assert "Topic 1" in result

    @patch("app.deep_research.DeepResearch")
    def test_no_suggestions(self, mock_cls, prompt_env):
        mock_instance = MagicMock()
        mock_instance.format_for_agent.return_value = ""
        mock_cls.return_value = mock_instance

        result = _get_deep_research(
            prompt_env["instance"],
            prompt_env["project_name"],
            prompt_env["project_path"],
        )
        assert result == ""

    @patch("app.deep_research.DeepResearch", side_effect=Exception("boom"))
    def test_error_returns_empty(self, _mock, prompt_env):
        result = _get_deep_research(
            prompt_env["instance"],
            prompt_env["project_name"],
            prompt_env["project_path"],
        )
        assert result == ""


# --- Tests for _get_verbose_section ---


class TestGetVerboseSection:
    """Tests for verbose mode section injection."""

    def test_verbose_active(self, prompt_env):
        # Create .koan-verbose file
        verbose_flag = Path(prompt_env["koan_root"]) / ".koan-verbose"
        verbose_flag.touch()

        result = _get_verbose_section(prompt_env["instance"])
        assert "# Verbose Mode (ACTIVE)" in result
        assert "outbox.md" in result
        assert prompt_env["instance"] in result

    def test_verbose_inactive(self, prompt_env):
        result = _get_verbose_section(prompt_env["instance"])
        assert result == ""


# --- Tests for build_agent_prompt ---


class TestBuildAgentPrompt:
    """Tests for the main agent prompt builder."""

    @patch("app.prompt_builder._get_verbose_section", return_value="")
    @patch("app.prompt_builder._get_audit_section", return_value="")
    @patch("app.prompt_builder._get_deep_research", return_value="")
    @patch("app.prompt_builder._get_merge_policy", return_value="\n# Git Merge\nStandard.\n")
    @patch("app.prompt_builder._get_branch_prefix", return_value="koan/")
    @patch("app.prompts.load_prompt")
    def test_basic_mission_prompt(
        self, mock_load, mock_prefix, mock_merge, mock_deep, mock_audit, mock_verbose,
        prompt_env,
    ):
        mock_load.return_value = "Template with {placeholder}"

        result = build_agent_prompt(
            instance=prompt_env["instance"],
            project_name="testproj",
            project_path=prompt_env["project_path"],
            run_num=3,
            max_runs=25,
            autonomous_mode="implement",
            focus_area="Medium-cost implementation",
            available_pct=42,
            mission_title="Fix the bug",
        )

        # Verify load_prompt was called with correct args
        mock_load.assert_called_once_with(
            "agent",
            INSTANCE=prompt_env["instance"],
            PROJECT_PATH=prompt_env["project_path"],
            PROJECT_NAME="testproj",
            RUN_NUM="3",
            MAX_RUNS="25",
            AUTONOMOUS_MODE="implement",
            FOCUS_AREA="Medium-cost implementation",
            AVAILABLE_PCT="42",
            MISSION_INSTRUCTION=(
                "Your assigned mission is: **Fix the bug** "
                "The mission is already marked In Progress. "
                "Follow the Mission Execution Workflow below."
            ),
            BRANCH_PREFIX="koan/",
        )

        # Merge policy appended
        assert "Git Merge" in result

    @patch("app.prompt_builder._get_verbose_section", return_value="")
    @patch("app.prompt_builder._get_audit_section", return_value="")
    @patch("app.prompt_builder._get_deep_research", return_value="")
    @patch("app.prompt_builder._get_merge_policy", return_value="\nMerge\n")
    @patch("app.prompt_builder._get_branch_prefix", return_value="koan/")
    @patch("app.prompts.load_prompt")
    def test_autonomous_mode_instruction(
        self, mock_load, mock_prefix, mock_merge, mock_deep, mock_audit, mock_verbose,
        prompt_env,
    ):
        mock_load.return_value = "Template"

        build_agent_prompt(
            instance=prompt_env["instance"],
            project_name="testproj",
            project_path=prompt_env["project_path"],
            run_num=1,
            max_runs=20,
            autonomous_mode="deep",
            focus_area="Deep work",
            available_pct=60,
            mission_title="",
        )

        call_kwargs = mock_load.call_args[1]
        instruction = call_kwargs["MISSION_INSTRUCTION"]
        assert "No specific mission assigned" in instruction
        assert "testproj" in instruction
        assert "[project:testproj]" in instruction

    @patch("app.prompt_builder._get_verbose_section", return_value="")
    @patch("app.prompt_builder._get_audit_section", return_value="")
    @patch("app.prompt_builder._get_deep_research", return_value="\n# Deep\nTopics\n")
    @patch("app.prompt_builder._get_merge_policy", return_value="\nMerge\n")
    @patch("app.prompt_builder._get_branch_prefix", return_value="koan/")
    @patch("app.prompts.load_prompt", return_value="Base")
    def test_deep_mode_includes_research(
        self, mock_load, mock_prefix, mock_merge, mock_deep, mock_audit, mock_verbose,
        prompt_env,
    ):
        result = build_agent_prompt(
            instance=prompt_env["instance"],
            project_name="testproj",
            project_path=prompt_env["project_path"],
            run_num=1,
            max_runs=20,
            autonomous_mode="deep",
            focus_area="Deep work",
            available_pct=60,
            mission_title="",
        )

        mock_deep.assert_called_once()
        assert "Deep" in result

    @patch("app.prompt_builder._get_verbose_section", return_value="")
    @patch("app.prompt_builder._get_audit_section", return_value="")
    @patch("app.prompt_builder._get_deep_research")
    @patch("app.prompt_builder._get_merge_policy", return_value="\nMerge\n")
    @patch("app.prompt_builder._get_branch_prefix", return_value="koan/")
    @patch("app.prompts.load_prompt", return_value="Base")
    def test_deep_mode_with_mission_skips_research(
        self, mock_load, mock_prefix, mock_merge, mock_deep, mock_audit, mock_verbose,
        prompt_env,
    ):
        """Deep mode with assigned mission should NOT inject deep research."""
        build_agent_prompt(
            instance=prompt_env["instance"],
            project_name="testproj",
            project_path=prompt_env["project_path"],
            run_num=1,
            max_runs=20,
            autonomous_mode="deep",
            focus_area="Execute assigned mission",
            available_pct=60,
            mission_title="Do this specific thing",
        )

        mock_deep.assert_not_called()

    @patch("app.prompt_builder._get_verbose_section", return_value="")
    @patch("app.prompt_builder._get_audit_section", return_value="")
    @patch("app.prompt_builder._get_deep_research")
    @patch("app.prompt_builder._get_merge_policy", return_value="\nMerge\n")
    @patch("app.prompt_builder._get_branch_prefix", return_value="koan/")
    @patch("app.prompts.load_prompt", return_value="Base")
    def test_implement_mode_skips_research(
        self, mock_load, mock_prefix, mock_merge, mock_deep, mock_audit, mock_verbose,
        prompt_env,
    ):
        """Non-deep modes should NOT inject deep research."""
        build_agent_prompt(
            instance=prompt_env["instance"],
            project_name="testproj",
            project_path=prompt_env["project_path"],
            run_num=1,
            max_runs=20,
            autonomous_mode="implement",
            focus_area="Medium-cost",
            available_pct=35,
            mission_title="",
        )

        mock_deep.assert_not_called()

    @patch("app.prompt_builder._get_verbose_section", return_value="\n# Verbose\nActive\n")
    @patch("app.prompt_builder._get_audit_section", return_value="")
    @patch("app.prompt_builder._get_deep_research", return_value="")
    @patch("app.prompt_builder._get_merge_policy", return_value="\nMerge\n")
    @patch("app.prompt_builder._get_branch_prefix", return_value="koan/")
    @patch("app.prompts.load_prompt", return_value="Base")
    def test_verbose_mode_appended(
        self, mock_load, mock_prefix, mock_merge, mock_deep, mock_audit, mock_verbose,
        prompt_env,
    ):
        result = build_agent_prompt(
            instance=prompt_env["instance"],
            project_name="testproj",
            project_path=prompt_env["project_path"],
            run_num=1,
            max_runs=20,
            autonomous_mode="implement",
            focus_area="Work",
            available_pct=50,
        )

        assert "Verbose" in result

    @patch("app.prompt_builder._get_verbose_section", return_value="")
    @patch("app.prompt_builder._get_audit_section", return_value="")
    @patch("app.prompt_builder._get_deep_research", return_value="")
    @patch("app.prompt_builder._get_merge_policy", return_value="\nMerge\n")
    @patch("app.prompt_builder._get_branch_prefix", return_value="koan/")
    @patch("app.prompts.load_prompt", return_value="Base prompt")
    def test_prompt_assembly_order(
        self, mock_load, mock_prefix, mock_merge, mock_deep, mock_audit, mock_verbose,
        prompt_env,
    ):
        """Sections are appended in correct order: template, merge, audit, deep, verbose."""
        result = build_agent_prompt(
            instance=prompt_env["instance"],
            project_name="testproj",
            project_path=prompt_env["project_path"],
            run_num=1,
            max_runs=20,
            autonomous_mode="implement",
            focus_area="Work",
            available_pct=50,
        )

        assert result.startswith("Base prompt")
        assert result.index("Merge") > result.index("Base prompt")

    @patch("app.prompt_builder._get_verbose_section", return_value="")
    @patch("app.prompt_builder._get_audit_section", return_value="\n# Audit\nSection\n")
    @patch("app.prompt_builder._get_deep_research", return_value="")
    @patch("app.prompt_builder._get_merge_policy", return_value="\nMerge\n")
    @patch("app.prompt_builder._get_branch_prefix", return_value="koan/")
    @patch("app.prompts.load_prompt", return_value="Base")
    def test_audit_section_appended_for_audit_mission(
        self, mock_load, mock_prefix, mock_merge, mock_deep, mock_audit, mock_verbose,
        prompt_env,
    ):
        """Audit section should be appended for audit missions."""
        result = build_agent_prompt(
            instance=prompt_env["instance"],
            project_name="testproj",
            project_path=prompt_env["project_path"],
            run_num=1,
            max_runs=20,
            autonomous_mode="implement",
            focus_area="Execute assigned mission",
            available_pct=50,
            mission_title="Security audit of auth module",
        )

        mock_audit.assert_called_once_with(
            "Security audit of auth module", prompt_env["project_path"]
        )
        assert "Audit" in result


# --- Tests for build_contemplative_prompt ---


class TestBuildContemplativePrompt:
    """Tests for contemplative prompt building."""

    @patch("app.prompts.load_prompt")
    def test_basic_contemplative(self, mock_load, prompt_env):
        mock_load.return_value = "Contemplative template"

        result = build_contemplative_prompt(
            instance=prompt_env["instance"],
            project_name="testproj",
            session_info="Pause mode. Run loop paused.",
        )

        mock_load.assert_called_once_with(
            "contemplative",
            INSTANCE=prompt_env["instance"],
            PROJECT_NAME="testproj",
            SESSION_INFO="Pause mode. Run loop paused.",
        )
        assert result == "Contemplative template"

    @patch("app.prompts.load_prompt")
    def test_active_mode_session_info(self, mock_load, prompt_env):
        mock_load.return_value = "Result"

        build_contemplative_prompt(
            instance=prompt_env["instance"],
            project_name="koan",
            session_info="Run 5/25 on koan. Mode: deep. Triggered by 10% contemplative chance.",
        )

        call_kwargs = mock_load.call_args[1]
        assert "Run 5/25" in call_kwargs["SESSION_INFO"]
        assert "deep" in call_kwargs["SESSION_INFO"]


# --- Tests for CLI interface ---


class TestCLI:
    """Tests for the CLI entry point."""

    @patch("app.prompt_builder.build_agent_prompt", return_value="Agent prompt output")
    def test_agent_subcommand(self, mock_build, prompt_env, capsys):
        import sys
        from app.prompt_builder import main

        with patch.object(sys, "argv", [
            "prompt_builder",
            "agent",
            "--instance", prompt_env["instance"],
            "--project-name", "testproj",
            "--project-path", prompt_env["project_path"],
            "--run-num", "3",
            "--max-runs", "25",
            "--autonomous-mode", "deep",
            "--focus-area", "Deep work",
            "--available-pct", "47",
            "--mission-title", "Fix bug",
        ]):
            main()

        mock_build.assert_called_once_with(
            instance=prompt_env["instance"],
            project_name="testproj",
            project_path=prompt_env["project_path"],
            run_num=3,
            max_runs=25,
            autonomous_mode="deep",
            focus_area="Deep work",
            available_pct=47,
            mission_title="Fix bug",
        )
        captured = capsys.readouterr()
        assert "Agent prompt output" in captured.out

    @patch("app.prompt_builder.build_contemplative_prompt", return_value="Contemplate output")
    def test_contemplative_subcommand(self, mock_build, prompt_env, capsys):
        import sys
        from app.prompt_builder import main

        with patch.object(sys, "argv", [
            "prompt_builder",
            "contemplative",
            "--instance", prompt_env["instance"],
            "--project-name", "koan",
            "--session-info", "Pause mode",
        ]):
            main()

        mock_build.assert_called_once_with(
            instance=prompt_env["instance"],
            project_name="koan",
            session_info="Pause mode",
        )
        captured = capsys.readouterr()
        assert "Contemplate output" in captured.out

    @patch("app.prompt_builder.build_agent_prompt", return_value="Prompt")
    def test_agent_defaults(self, mock_build, prompt_env, capsys):
        """Test that defaults are applied when optional args are omitted."""
        import sys
        from app.prompt_builder import main

        with patch.object(sys, "argv", [
            "prompt_builder",
            "agent",
            "--instance", prompt_env["instance"],
            "--project-name", "testproj",
            "--project-path", prompt_env["project_path"],
            "--run-num", "1",
            "--max-runs", "20",
        ]):
            main()

        mock_build.assert_called_once_with(
            instance=prompt_env["instance"],
            project_name="testproj",
            project_path=prompt_env["project_path"],
            run_num=1,
            max_runs=20,
            autonomous_mode="implement",
            focus_area="General autonomous work",
            available_pct=50,
            mission_title="",
        )


# --- Integration-style tests ---


class TestIntegration:
    """Tests that verify the full prompt assembly with real templates."""

    @patch("app.prompt_builder._get_verbose_section", return_value="")
    @patch("app.prompt_builder._is_auto_merge_enabled", return_value=False)
    @patch("app.prompt_builder._get_deep_research", return_value="")
    def test_full_agent_prompt_with_real_template(
        self, mock_deep, mock_merge, mock_verbose, prompt_env
    ):
        """Build a full prompt using the real agent.md template."""
        result = build_agent_prompt(
            instance=prompt_env["instance"],
            project_name="testproj",
            project_path=prompt_env["project_path"],
            run_num=5,
            max_runs=25,
            autonomous_mode="implement",
            focus_area="Quick wins",
            available_pct=35,
            mission_title="Fix auth bug",
        )

        # Verify placeholders were substituted
        assert "{INSTANCE}" not in result
        assert "{PROJECT_NAME}" not in result
        assert "{PROJECT_PATH}" not in result
        assert "{RUN_NUM}" not in result
        assert "{MAX_RUNS}" not in result
        assert "{AUTONOMOUS_MODE}" not in result
        assert "{FOCUS_AREA}" not in result
        assert "{AVAILABLE_PCT}" not in result
        assert "{MISSION_INSTRUCTION}" not in result
        assert "{BRANCH_PREFIX}" not in result

        # Verify substituted values are present
        assert prompt_env["instance"] in result
        assert "testproj" in result
        assert prompt_env["project_path"] in result
        assert "implement" in result
        assert "Quick wins" in result
        assert "35" in result
        assert "Fix auth bug" in result

        # Verify merge policy was appended
        assert "NOT configured" in result

        # Verify new prompt structure
        assert "Mission Execution Workflow" in result
        assert "Pull Request Quality" in result
        assert "Mission Completion Checklist" in result

        # Verify old misleading instruction is gone
        assert "Mark it In Progress in missions.md" not in result
        assert "already marked In Progress" in result

        # Verify no hardcoded names in generic prompt
        assert "Alexis" not in result

        # Verify audit section is NOT included for non-audit mission
        assert "Audit Missions" not in result

    @patch("app.prompt_builder._get_verbose_section", return_value="")
    @patch("app.prompt_builder._is_auto_merge_enabled", return_value=False)
    @patch("app.prompt_builder._get_deep_research", return_value="")
    def test_full_autonomous_prompt(
        self, mock_deep, mock_merge, mock_verbose, prompt_env
    ):
        """Build autonomous mode prompt — no mission title."""
        result = build_agent_prompt(
            instance=prompt_env["instance"],
            project_name="koan",
            project_path=prompt_env["project_path"],
            run_num=10,
            max_runs=50,
            autonomous_mode="deep",
            focus_area="High-cost deep work",
            available_pct=47,
        )

        assert "No specific mission assigned" in result
        assert "[project:koan]" in result
        assert "deep" in result

    @patch("app.prompt_builder._get_verbose_section", return_value="")
    @patch("app.prompt_builder._is_auto_merge_enabled", return_value=False)
    @patch("app.prompt_builder._get_deep_research", return_value="")
    def test_audit_mission_includes_audit_section(
        self, mock_deep, mock_merge, mock_verbose, prompt_env
    ):
        """Audit missions should include the audit section via real template."""
        result = build_agent_prompt(
            instance=prompt_env["instance"],
            project_name="testproj",
            project_path=prompt_env["project_path"],
            run_num=1,
            max_runs=20,
            autonomous_mode="implement",
            focus_area="Execute assigned mission",
            available_pct=50,
            mission_title="Security audit of the auth module",
        )

        assert "Audit Missions" in result
        assert "GitHub Issue Follow-up" in result
        assert "gh issue create" in result

    @patch("app.prompt_builder._get_verbose_section", return_value="")
    @patch("app.prompt_builder._is_auto_merge_enabled", return_value=False)
    @patch("app.prompt_builder._get_deep_research", return_value="")
    def test_non_audit_mission_excludes_audit_section(
        self, mock_deep, mock_merge, mock_verbose, prompt_env
    ):
        """Non-audit missions should NOT include the audit section."""
        result = build_agent_prompt(
            instance=prompt_env["instance"],
            project_name="testproj",
            project_path=prompt_env["project_path"],
            run_num=1,
            max_runs=20,
            autonomous_mode="implement",
            focus_area="Execute assigned mission",
            available_pct=50,
            mission_title="Fix the login page CSS",
        )

        assert "Audit Missions" not in result

    def test_full_contemplative_prompt(self, prompt_env):
        """Build a full contemplative prompt using the real template."""
        result = build_contemplative_prompt(
            instance=prompt_env["instance"],
            project_name="testproj",
            session_info="Run 3/25 on testproj. Mode: deep.",
        )

        # Verify placeholders were substituted
        assert "{INSTANCE}" not in result
        assert "{PROJECT_NAME}" not in result
        assert "{SESSION_INFO}" not in result

        # Verify content
        assert prompt_env["instance"] in result
        assert "testproj" in result
        assert "Run 3/25" in result
        assert "contemplative" in result.lower()
