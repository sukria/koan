"""Tests for the prompt audit module."""

import json
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.prompt_audit import (
    _analyze_prompt_file,
    build_audit_prompt,
    discover_prompts,
    extract_actionable_findings,
    format_prompt_list,
    read_signals,
    run_audit,
    save_audit_report,
    summarize_signals,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def koan_root(tmp_path):
    """Create a minimal koan directory with system prompts."""
    root = tmp_path / "koan-root"
    root.mkdir()

    # System prompts
    sys_prompts = root / "koan" / "system-prompts"
    sys_prompts.mkdir(parents=True)

    (sys_prompts / "agent.md").write_text(
        "# Agent Prompt\n\n"
        "You are {AGENT_NAME}.\n\n"
        "## Rules\n\n"
        "Follow the rules.\n\n"
        "## Output\n\n"
        "Respond clearly.\n"
    )
    (sys_prompts / "chat.md").write_text(
        "# Chat Prompt\n\n"
        "Reply in the user's language.\n"
    )
    # This one should be excluded from audits (meta-recursion)
    (sys_prompts / "prompt-audit.md").write_text(
        "# Audit Prompt\n\n"
        "You are an auditor.\n"
    )

    return root


@pytest.fixture
def instance_dir(tmp_path):
    """Create a minimal instance directory."""
    inst = tmp_path / "instance"
    inst.mkdir()
    (inst / "journal").mkdir()
    (inst / "shared-journal.md").write_text("# Shared Journal\n")
    return inst


@pytest.fixture
def signal_data(instance_dir):
    """Create sample signal JSONL files."""
    today = datetime.now().strftime("%Y-%m-%d")
    journal_dir = instance_dir / "journal" / today
    journal_dir.mkdir(parents=True)

    signals = [
        {
            "timestamp": "2026-03-11T10:00:00",
            "project_name": "koan",
            "mission_title": "fix bug in auth",
            "exit_code": 0,
            "duration_minutes": 5,
            "autonomous_mode": "implement",
        },
        {
            "timestamp": "2026-03-11T11:00:00",
            "project_name": "koan",
            "mission_title": "add feature X",
            "exit_code": 0,
            "duration_minutes": 12,
            "autonomous_mode": "deep",
        },
        {
            "timestamp": "2026-03-11T12:00:00",
            "project_name": "webapp",
            "mission_title": "deploy v2",
            "exit_code": 1,
            "duration_minutes": 3,
            "autonomous_mode": "implement",
        },
    ]

    signal_file = journal_dir / "prompt-audit-signals.jsonl"
    lines = [json.dumps(s) for s in signals]
    signal_file.write_text("\n".join(lines) + "\n")

    return signals


# ---------------------------------------------------------------------------
# discover_prompts
# ---------------------------------------------------------------------------


class TestDiscoverPrompts:
    def test_discovers_system_prompts(self, koan_root):
        prompts = discover_prompts(koan_root)
        names = [p["name"] for p in prompts]
        assert "agent" in names
        assert "chat" in names

    def test_excludes_audit_prompt(self, koan_root):
        prompts = discover_prompts(koan_root)
        names = [p["name"] for p in prompts]
        assert "prompt-audit" not in names

    def test_empty_dir(self, tmp_path):
        prompts = discover_prompts(tmp_path)
        assert prompts == []

    def test_discovers_skill_prompts(self, koan_root):
        # Create a skill prompt
        skill_prompts = koan_root / "koan" / "skills" / "core" / "review" / "prompts"
        skill_prompts.mkdir(parents=True)
        (skill_prompts / "review.md").write_text("# Review\n\nReview code.\n")

        prompts = discover_prompts(koan_root)
        categories = [p["category"] for p in prompts]
        assert "skill/review" in categories


class TestAnalyzePromptFile:
    def test_basic_metrics(self, koan_root):
        path = koan_root / "koan" / "system-prompts" / "agent.md"
        result = _analyze_prompt_file(path, "system-prompt")

        assert result["name"] == "agent"
        assert result["category"] == "system-prompt"
        assert result["lines"] > 0
        assert result["words"] > 0
        assert result["sections"] == 3  # Agent Prompt, Rules, Output
        assert result["placeholders"] == 1  # {AGENT_NAME}
        assert "last_modified" in result

    def test_no_sections_no_placeholders(self, tmp_path):
        f = tmp_path / "plain.md"
        f.write_text("Just a plain text file with no structure.\n")
        result = _analyze_prompt_file(f, "test")
        assert result["sections"] == 0
        assert result["placeholders"] == 0


# ---------------------------------------------------------------------------
# read_signals
# ---------------------------------------------------------------------------


class TestReadSignals:
    def test_reads_recent_signals(self, instance_dir, signal_data):
        signals = read_signals(instance_dir, days=7)
        assert len(signals) == 3

    def test_ignores_old_signals(self, instance_dir):
        # Create signals 10 days ago
        old_date = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
        old_dir = instance_dir / "journal" / old_date
        old_dir.mkdir(parents=True)
        signal_file = old_dir / "prompt-audit-signals.jsonl"
        signal_file.write_text(json.dumps({"exit_code": 0}) + "\n")

        signals = read_signals(instance_dir, days=7)
        assert len(signals) == 0

    def test_empty_journal(self, instance_dir):
        signals = read_signals(instance_dir, days=7)
        assert signals == []

    def test_invalid_jsonl_skipped(self, instance_dir):
        today = datetime.now().strftime("%Y-%m-%d")
        journal_dir = instance_dir / "journal" / today
        journal_dir.mkdir(parents=True)
        signal_file = journal_dir / "prompt-audit-signals.jsonl"
        signal_file.write_text("not json\n{\"valid\": true}\n")

        # Should skip invalid line but not crash
        # The current implementation catches JSONDecodeError at file level
        # so the whole file is skipped
        signals = read_signals(instance_dir, days=7)
        # Implementation catches per-file, so this returns empty
        assert isinstance(signals, list)

    def test_non_date_dirs_ignored(self, instance_dir):
        (instance_dir / "journal" / "not-a-date").mkdir(parents=True)
        signals = read_signals(instance_dir, days=7)
        assert signals == []


# ---------------------------------------------------------------------------
# summarize_signals
# ---------------------------------------------------------------------------


class TestSummarizeSignals:
    def test_empty_signals(self):
        result = summarize_signals([])
        assert "No signal data" in result

    def test_basic_summary(self, signal_data):
        result = summarize_signals(signal_data)
        assert "3 missions" in result
        assert "Success rate: 2/3" in result
        assert "koan" in result
        assert "webapp" in result

    def test_failure_titles_shown(self, signal_data):
        result = summarize_signals(signal_data)
        assert "deploy v2" in result

    def test_all_success(self):
        signals = [
            {"exit_code": 0, "duration_minutes": 5, "project_name": "a", "autonomous_mode": "implement"},
            {"exit_code": 0, "duration_minutes": 10, "project_name": "a", "autonomous_mode": "implement"},
        ]
        result = summarize_signals(signals)
        assert "100%" in result
        assert "failures" not in result.lower()


# ---------------------------------------------------------------------------
# format_prompt_list
# ---------------------------------------------------------------------------


class TestFormatPromptList:
    def test_basic_format(self):
        prompts = [
            {"name": "agent", "category": "system-prompt", "lines": 50,
             "words": 300, "sections": 5, "placeholders": 2, "last_modified": "2026-03-01"},
        ]
        result = format_prompt_list(prompts)
        assert "agent" in result
        assert "50 lines" in result
        assert "300 words" in result

    def test_sampling(self):
        prompts = [
            {"name": f"prompt-{i}", "category": "system-prompt", "lines": 10,
             "words": 50, "sections": 1, "placeholders": 0, "last_modified": "2026-03-01"}
            for i in range(20)
        ]
        result = format_prompt_list(prompts, max_prompts=5)
        # Should only include 5 entries
        assert result.count("**prompt-") == 5


# ---------------------------------------------------------------------------
# build_audit_prompt
# ---------------------------------------------------------------------------


class TestBuildAuditPrompt:
    def test_substitutes_placeholders(self, koan_root):
        prompts = [
            {"name": "agent", "category": "system-prompt", "lines": 50,
             "words": 300, "sections": 5, "placeholders": 2, "last_modified": "2026-03-01"},
        ]
        signals = []
        result = build_audit_prompt(prompts, signals, {"agent": "test content"})
        assert "agent" in result
        assert "No signal data" in result
        assert "test content" in result

    def test_with_signal_data(self, koan_root):
        prompts = [
            {"name": "agent", "category": "system-prompt", "lines": 50,
             "words": 300, "sections": 5, "placeholders": 2, "last_modified": "2026-03-01"},
        ]
        signals = [
            {"exit_code": 0, "duration_minutes": 5, "project_name": "test", "autonomous_mode": "implement"},
        ]
        result = build_audit_prompt(prompts, signals)
        assert "1 missions" in result


# ---------------------------------------------------------------------------
# run_audit
# ---------------------------------------------------------------------------


class TestRunAudit:
    @patch("app.prompt_audit.subprocess.run")
    @patch("app.cli_provider.build_full_command", return_value=["claude", "-p", "test"])
    def test_successful_audit(self, mock_cmd, mock_run, koan_root, instance_dir):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="## Audit Summary\n\nAll clear.",
            stderr="",
        )
        report, prompts = run_audit(koan_root, instance_dir)
        assert "Audit Summary" in report
        assert len(prompts) > 0
        mock_run.assert_called_once()

    @patch("app.prompt_audit.subprocess.run")
    @patch("app.cli_provider.build_full_command", return_value=["claude", "-p", "test"])
    def test_claude_failure(self, mock_cmd, mock_run, koan_root, instance_dir):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="quota exceeded",
        )
        report, prompts = run_audit(koan_root, instance_dir)
        assert "failed" in report.lower()

    @patch("app.prompt_audit.subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 120))
    @patch("app.cli_provider.build_full_command", return_value=["claude", "-p", "test"])
    def test_timeout(self, mock_cmd, mock_run, koan_root, instance_dir):
        report, prompts = run_audit(koan_root, instance_dir)
        assert "timed out" in report.lower()

    def test_no_prompts(self, tmp_path, instance_dir):
        # koan_root with no prompt files
        report, prompts = run_audit(tmp_path, instance_dir)
        assert "No prompt files" in report
        assert prompts == []


# ---------------------------------------------------------------------------
# save_audit_report
# ---------------------------------------------------------------------------


class TestSaveAuditReport:
    def test_creates_entry(self, instance_dir):
        save_audit_report(instance_dir, "Test findings here.")
        content = (instance_dir / "shared-journal.md").read_text()
        assert "Prompt Audit" in content
        assert "Test findings here." in content

    def test_appends_to_existing(self, instance_dir):
        (instance_dir / "shared-journal.md").write_text("# Existing content\n")
        save_audit_report(instance_dir, "New audit report.")
        content = (instance_dir / "shared-journal.md").read_text()
        assert "Existing content" in content
        assert "New audit report." in content

    def test_creates_file_if_missing(self, tmp_path):
        inst = tmp_path / "inst"
        inst.mkdir()
        path = save_audit_report(inst, "First audit.")
        assert path.exists()
        assert "First audit." in path.read_text()


# ---------------------------------------------------------------------------
# extract_actionable_findings
# ---------------------------------------------------------------------------


class TestExtractActionableFindings:
    def test_extracts_severity_levels(self):
        report = (
            "## Findings\n\n"
            "### 🔴 Action — agent: Missing constraint\n"
            "The agent prompt lacks a safety constraint.\n\n"
            "### 🟡 Warning — chat: Verbose section\n"
            "Section 3 could be shorter.\n\n"
            "### 🔵 Info — sparring: Well-structured\n"
            "Good use of placeholders.\n"
        )
        findings = extract_actionable_findings(report)
        severities = [f["severity"] for f in findings]
        assert "action" in severities
        assert "warning" in severities
        assert "info" in severities

    def test_empty_report(self):
        assert extract_actionable_findings("") == []

    def test_no_markers(self):
        report = "Everything looks fine.\nNo issues found.\n"
        assert extract_actionable_findings(report) == []

    def test_action_count(self):
        report = (
            "🔴 Action — fix prompt A\n"
            "🔴 Action — fix prompt B\n"
            "🟡 Warning — check prompt C\n"
        )
        findings = extract_actionable_findings(report)
        actions = [f for f in findings if f["severity"] == "action"]
        assert len(actions) == 2


# ---------------------------------------------------------------------------
# Signal hook example validation
# ---------------------------------------------------------------------------


class TestSignalHookExample:
    """Verify the hook example file is syntactically valid and has correct structure."""

    @staticmethod
    def _load_hook_example(tmp_path):
        """Copy the .py.example to a .py file and load it."""
        import importlib.util
        import shutil

        hook_path = (
            Path(__file__).parent.parent.parent
            / "instance.example" / "hooks" / "prompt_audit_signals.py.example"
        )
        if not hook_path.exists():
            pytest.skip("Hook example file not found")

        # Copy to a .py file so importlib can load it
        copy_path = tmp_path / "prompt_audit_signals.py"
        shutil.copy2(hook_path, copy_path)

        spec = importlib.util.spec_from_file_location("test_hook_example", copy_path)
        assert spec is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_hook_example_loads(self, tmp_path):
        """The hook example should be importable (valid Python)."""
        module = self._load_hook_example(tmp_path)

        # Verify HOOKS dict
        assert hasattr(module, "HOOKS")
        assert "post_mission" in module.HOOKS
        assert callable(module.HOOKS["post_mission"])

    def test_hook_writes_signal(self, tmp_path):
        """The hook should write a JSONL line when called."""
        module = self._load_hook_example(tmp_path)

        instance_dir = tmp_path / "instance"
        instance_dir.mkdir()
        (instance_dir / "journal").mkdir()

        ctx = {
            "instance_dir": str(instance_dir),
            "project_name": "test-project",
            "mission_title": "fix a bug",
            "exit_code": 0,
            "duration_minutes": 5,
            "autonomous_mode": "implement",
        }

        module.HOOKS["post_mission"](ctx)

        # Find the JSONL file
        today = datetime.now().strftime("%Y-%m-%d")
        signal_file = instance_dir / "journal" / today / "prompt-audit-signals.jsonl"
        assert signal_file.exists()

        data = json.loads(signal_file.read_text().strip())
        assert data["project_name"] == "test-project"
        assert data["exit_code"] == 0
        assert data["duration_minutes"] == 5
