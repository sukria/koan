"""Tests for git_auto_merge.py — automatic branch merging."""

import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from app.git_auto_merge import (
    find_matching_rule,
    should_auto_merge,
    run_git,
    get_author_env,
    get_origin_url,
    normalize_git_url,
    is_upstream_origin,
    create_pull_request,
    is_working_tree_clean,
    is_branch_pushed,
    perform_merge,
    cleanup_branch,
    cleanup_local_branch,
    cleanup_remote_branch,
    auto_merge_branch,
    write_merge_success_to_journal,
    write_merge_failure_to_journal,
    write_pr_success_to_journal,
)
from app.utils import get_auto_merge_config


# --- get_auto_merge_config ---

class TestGetAutoMergeConfig:
    def test_global_defaults_only(self):
        """When no project override, return global defaults."""
        config = {
            "git_auto_merge": {
                "enabled": True,
                "base_branch": "main",
                "strategy": "squash",
                "rules": [{"pattern": "koan/*", "auto_merge": True}]
            }
        }
        result = get_auto_merge_config(config, "unknown-project")

        assert result["enabled"] is True
        assert result["base_branch"] == "main"
        assert result["strategy"] == "squash"
        assert len(result["rules"]) == 1

    def test_project_overrides_enabled(self):
        """Project can override enabled flag."""
        config = {
            "git_auto_merge": {"enabled": True, "base_branch": "main", "strategy": "squash"},
            "projects": {
                "frontend": {
                    "git_auto_merge": {"enabled": False}
                }
            }
        }
        result = get_auto_merge_config(config, "frontend")

        assert result["enabled"] is False
        assert result["base_branch"] == "main"  # Inherited
        assert result["strategy"] == "squash"  # Inherited

    def test_project_overrides_base_branch(self):
        """Project can override base_branch."""
        config = {
            "git_auto_merge": {"enabled": True, "base_branch": "main", "strategy": "squash"},
            "projects": {
                "backend": {
                    "git_auto_merge": {"base_branch": "develop"}
                }
            }
        }
        result = get_auto_merge_config(config, "backend")

        assert result["enabled"] is True  # Inherited
        assert result["base_branch"] == "develop"  # Overridden
        assert result["strategy"] == "squash"  # Inherited

    def test_project_overrides_strategy(self):
        """Project can override merge strategy."""
        config = {
            "git_auto_merge": {"enabled": True, "base_branch": "main", "strategy": "squash"},
            "projects": {
                "backend": {
                    "git_auto_merge": {"strategy": "merge"}
                }
            }
        }
        result = get_auto_merge_config(config, "backend")

        assert result["strategy"] == "merge"  # Overridden
        assert result["base_branch"] == "main"  # Inherited

    def test_project_overrides_rules(self):
        """Project rules replace global rules entirely."""
        config = {
            "git_auto_merge": {
                "enabled": True,
                "base_branch": "main",
                "rules": [{"pattern": "koan/*", "auto_merge": True}]
            },
            "projects": {
                "backend": {
                    "git_auto_merge": {
                        "rules": [
                            {"pattern": "koan/hotfix-*", "auto_merge": True, "base_branch": "main"},
                            {"pattern": "koan/*", "auto_merge": True, "base_branch": "develop"}
                        ]
                    }
                }
            }
        }
        result = get_auto_merge_config(config, "backend")

        assert len(result["rules"]) == 2  # Project rules replace global
        assert result["rules"][0]["pattern"] == "koan/hotfix-*"
        assert result["rules"][1]["pattern"] == "koan/*"

    def test_upstream_url_from_global(self):
        """upstream_url from global config."""
        config = {
            "git_auto_merge": {"upstream_url": "https://github.com/sukria/koan.git"}
        }
        result = get_auto_merge_config(config, "koan")
        assert result["upstream_url"] == "https://github.com/sukria/koan.git"

    def test_upstream_url_from_project_override(self):
        """Project-level upstream_url overrides global."""
        config = {
            "git_auto_merge": {"upstream_url": "https://github.com/global/repo.git"},
            "projects": {
                "koan": {"git_auto_merge": {"upstream_url": "https://github.com/sukria/koan.git"}}
            }
        }
        result = get_auto_merge_config(config, "koan")
        assert result["upstream_url"] == "https://github.com/sukria/koan.git"

    def test_upstream_url_defaults_empty(self):
        """upstream_url defaults to empty string."""
        config = {}
        result = get_auto_merge_config(config, "koan")
        assert result["upstream_url"] == ""

    def test_missing_config_section(self):
        """When git_auto_merge section missing, return safe defaults."""
        config = {}
        result = get_auto_merge_config(config, "koan")

        assert result["enabled"] is True  # Default
        assert result["base_branch"] == "main"  # Default
        assert result["strategy"] == "squash"  # Default
        assert result["rules"] == []  # Empty
        assert result["upstream_url"] == ""  # Default


# --- find_matching_rule ---

class TestFindMatchingRule:
    def test_exact_match(self):
        """Exact pattern match."""
        rules = [{"pattern": "koan/fix-*", "auto_merge": True}]
        rule = find_matching_rule("koan/fix-cors", rules)

        assert rule is not None
        assert rule["pattern"] == "koan/fix-*"

    def test_wildcard_match(self):
        """Glob wildcard matching."""
        rules = [{"pattern": "koan/*", "auto_merge": True}]

        assert find_matching_rule("koan/fix-bug", rules) is not None
        assert find_matching_rule("koan/feature-x", rules) is not None
        assert find_matching_rule("main", rules) is None
        assert find_matching_rule("feature/new", rules) is None

    def test_first_match_wins(self):
        """When multiple rules match, first one wins."""
        rules = [
            {"pattern": "koan/hotfix-*", "priority": 1},
            {"pattern": "koan/*", "priority": 2}
        ]
        rule = find_matching_rule("koan/hotfix-cors", rules)

        assert rule["priority"] == 1  # First match

    def test_no_match(self):
        """When no rule matches, return None."""
        rules = [{"pattern": "koan/*", "auto_merge": True}]
        rule = find_matching_rule("main", rules)

        assert rule is None

    def test_empty_rules(self):
        """When rules list is empty, return None."""
        rules = []
        rule = find_matching_rule("koan/fix-bug", rules)

        assert rule is None


# --- should_auto_merge ---

class TestShouldAutoMerge:
    def test_enabled_with_matching_rule(self):
        """Should merge when enabled and rule matches."""
        config = {
            "enabled": True,
            "base_branch": "main",
            "rules": [{"pattern": "koan/*", "auto_merge": True}]
        }
        should_merge, rule, base_branch = should_auto_merge(config, "koan/fix-bug")

        assert should_merge is True
        assert rule is not None
        assert base_branch == "main"

    def test_disabled_globally(self):
        """Should not merge when disabled globally."""
        config = {
            "enabled": False,
            "base_branch": "main",
            "rules": [{"pattern": "koan/*", "auto_merge": True}]
        }
        should_merge, rule, base_branch = should_auto_merge(config, "koan/fix-bug")

        assert should_merge is False
        assert rule is None
        assert base_branch == ""

    def test_no_matching_rule(self):
        """Should not merge when no rule matches."""
        config = {
            "enabled": True,
            "base_branch": "main",
            "rules": [{"pattern": "koan/*", "auto_merge": True}]
        }
        should_merge, rule, base_branch = should_auto_merge(config, "feature/new-thing")

        assert should_merge is False
        assert rule is None
        assert base_branch == ""

    def test_rule_auto_merge_false(self):
        """Should not merge when rule exists but auto_merge is False."""
        config = {
            "enabled": True,
            "base_branch": "main",
            "rules": [{"pattern": "koan/*", "auto_merge": False}]
        }
        should_merge, rule, base_branch = should_auto_merge(config, "koan/fix-bug")

        assert should_merge is False
        assert rule is None
        assert base_branch == ""

    def test_rule_overrides_base_branch(self):
        """Rule-level base_branch overrides config base_branch."""
        config = {
            "enabled": True,
            "base_branch": "develop",  # Config default
            "rules": [
                {"pattern": "koan/hotfix-*", "auto_merge": True, "base_branch": "main"}  # Rule override
            ]
        }
        should_merge, rule, base_branch = should_auto_merge(config, "koan/hotfix-cors")

        assert should_merge is True
        assert base_branch == "main"  # Rule override wins

    def test_base_branch_precedence(self):
        """Test base_branch resolution precedence: rule > config > default."""
        # Case 1: Rule specifies base_branch
        config1 = {
            "enabled": True,
            "base_branch": "develop",
            "rules": [{"pattern": "koan/*", "auto_merge": True, "base_branch": "staging"}]
        }
        _, _, base1 = should_auto_merge(config1, "koan/test")
        assert base1 == "staging"  # Rule wins

        # Case 2: Config specifies base_branch, rule doesn't
        config2 = {
            "enabled": True,
            "base_branch": "develop",
            "rules": [{"pattern": "koan/*", "auto_merge": True}]
        }
        _, _, base2 = should_auto_merge(config2, "koan/test")
        assert base2 == "develop"  # Config wins

        # Case 3: Neither specified, use default
        config3 = {
            "enabled": True,
            "rules": [{"pattern": "koan/*", "auto_merge": True}]
        }
        _, _, base3 = should_auto_merge(config3, "koan/test")
        assert base3 == "main"  # Default

    def test_multiple_rules_first_match(self):
        """When multiple rules match, first match determines base_branch."""
        config = {
            "enabled": True,
            "base_branch": "develop",
            "rules": [
                {"pattern": "koan/hotfix-*", "auto_merge": True, "base_branch": "main"},
                {"pattern": "koan/*", "auto_merge": True, "base_branch": "staging"}
            ]
        }

        # Hotfix matches first rule
        should_merge1, _, base1 = should_auto_merge(config, "koan/hotfix-auth")
        assert should_merge1 is True
        assert base1 == "main"

        # Regular koan branch matches second rule
        should_merge2, _, base2 = should_auto_merge(config, "koan/feature-x")
        assert should_merge2 is True
        assert base2 == "staging"

    def test_empty_rules_list(self):
        """When rules list is empty, should not merge."""
        config = {
            "enabled": True,
            "base_branch": "main",
            "rules": []
        }
        should_merge, rule, base_branch = should_auto_merge(config, "koan/fix-bug")

        assert should_merge is False
        assert rule is None
        assert base_branch == ""


# --- get_author_env ---

class TestGetAuthorEnv:
    def test_returns_env_vars_when_email_set(self):
        """When KOAN_EMAIL is set, return GIT_AUTHOR/COMMITTER env vars."""
        with patch.dict("os.environ", {"KOAN_EMAIL": "koan@example.com"}):
            env = get_author_env()
            assert env == {
                "GIT_AUTHOR_NAME": "Koan",
                "GIT_AUTHOR_EMAIL": "koan@example.com",
                "GIT_COMMITTER_NAME": "Koan",
                "GIT_COMMITTER_EMAIL": "koan@example.com",
            }

    def test_returns_empty_dict_when_no_email(self):
        """When KOAN_EMAIL is not set, return empty dict."""
        with patch.dict("os.environ", {}, clear=False):
            # Ensure KOAN_EMAIL is not present
            import os
            env_backup = os.environ.pop("KOAN_EMAIL", None)
            try:
                env = get_author_env()
                assert env == {}
            finally:
                if env_backup is not None:
                    os.environ["KOAN_EMAIL"] = env_backup

    def test_no_author_flag_in_merge_calls(self):
        """Verify merge strategy passes env kwarg, not --author args."""
        calls = [
            (0, "fix stuff", ""),  # git log
            (0, "", ""),  # checkout
            (0, "", ""),  # pull
            (0, "", ""),  # merge --no-ff
            (0, "", ""),  # push
            (0, "", ""),  # checkout (finally)
        ]
        with patch("app.git_auto_merge.run_git", side_effect=calls) as mock, \
             patch.dict("os.environ", {"KOAN_EMAIL": "koan@test.com"}):
            ok, err = perform_merge("/tmp", "koan/fix", "main", "merge")
            assert ok is True
            # The merge call (4th call, index 3) should NOT have --author
            merge_call = mock.call_args_list[3]
            args_passed = list(merge_call[0])  # positional args
            assert "--author" not in args_passed
            # Should have env kwarg
            assert "env" in merge_call[1]
            assert merge_call[1]["env"]["GIT_AUTHOR_EMAIL"] == "koan@test.com"

    def test_squash_commit_uses_env(self):
        """Verify squash strategy passes env to commit, not --author args."""
        calls = [
            (0, "fix stuff", ""),  # git log
            (0, "", ""),  # checkout
            (0, "", ""),  # pull
            (0, "", ""),  # merge --squash
            (0, "", ""),  # commit
            (0, "", ""),  # push
            (0, "", ""),  # checkout (finally)
        ]
        with patch("app.git_auto_merge.run_git", side_effect=calls) as mock, \
             patch.dict("os.environ", {"KOAN_EMAIL": "koan@test.com"}):
            ok, err = perform_merge("/tmp", "koan/fix", "main", "squash")
            assert ok is True
            # The commit call (5th call, index 4) should use env, not --author
            commit_call = mock.call_args_list[4]
            args_passed = list(commit_call[0])
            assert "--author" not in args_passed
            assert "env" in commit_call[1]


# --- get_origin_url ---

class TestGetOriginUrl:
    def test_success(self):
        """Returns origin URL when git succeeds."""
        with patch("app.git_auto_merge.run_git", return_value=(0, "https://github.com/atoomic/koan.git", "")):
            assert get_origin_url("/tmp") == "https://github.com/atoomic/koan.git"

    def test_failure(self):
        """Returns empty string when git fails."""
        with patch("app.git_auto_merge.run_git", return_value=(1, "", "error")):
            assert get_origin_url("/tmp") == ""


# --- normalize_git_url ---

class TestNormalizeGitUrl:
    def test_https_with_git_suffix(self):
        assert normalize_git_url("https://github.com/sukria/koan.git") == "github.com/sukria/koan"

    def test_https_without_git_suffix(self):
        assert normalize_git_url("https://github.com/sukria/koan") == "github.com/sukria/koan"

    def test_ssh_format(self):
        assert normalize_git_url("git@github.com:sukria/koan.git") == "github.com/sukria/koan"

    def test_ssh_without_git_suffix(self):
        assert normalize_git_url("git@github.com:sukria/koan") == "github.com/sukria/koan"

    def test_case_insensitive(self):
        assert normalize_git_url("https://GitHub.com/Sukria/Koan.git") == "github.com/sukria/koan"

    def test_trailing_slash(self):
        assert normalize_git_url("https://github.com/sukria/koan/") == "github.com/sukria/koan"

    def test_same_repo_different_formats(self):
        """SSH and HTTPS URLs for same repo should normalize identically."""
        ssh = normalize_git_url("git@github.com:sukria/koan.git")
        https = normalize_git_url("https://github.com/sukria/koan.git")
        assert ssh == https


# --- is_upstream_origin ---

class TestIsUpstreamOrigin:
    def test_origin_matches_upstream(self):
        """When origin matches upstream URL, returns True."""
        with patch("app.git_auto_merge.get_origin_url", return_value="https://github.com/sukria/koan.git"):
            assert is_upstream_origin("/tmp", "https://github.com/sukria/koan.git") is True

    def test_origin_is_fork(self):
        """When origin is a fork, returns False."""
        with patch("app.git_auto_merge.get_origin_url", return_value="https://github.com/atoomic/koan.git"):
            assert is_upstream_origin("/tmp", "https://github.com/sukria/koan.git") is False

    def test_no_upstream_configured(self):
        """When no upstream_url is configured, assume origin IS upstream (backward compat)."""
        assert is_upstream_origin("/tmp", "") is True

    def test_origin_ssh_upstream_https(self):
        """Cross-format comparison works (SSH origin vs HTTPS upstream)."""
        with patch("app.git_auto_merge.get_origin_url", return_value="git@github.com:sukria/koan.git"):
            assert is_upstream_origin("/tmp", "https://github.com/sukria/koan.git") is True

    def test_origin_url_empty(self):
        """When origin URL is empty (no remote), returns False."""
        with patch("app.git_auto_merge.get_origin_url", return_value=""):
            assert is_upstream_origin("/tmp", "https://github.com/sukria/koan.git") is False


# --- create_pull_request ---

class TestCreatePullRequest:
    def test_success(self):
        """PR created successfully returns URL."""
        with patch("app.git_auto_merge.get_origin_url", return_value="https://github.com/atoomic/koan.git"), \
             patch("app.git_auto_merge.get_branch_commit_messages", return_value=["fix bug"]), \
             patch("app.git_auto_merge.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="https://github.com/sukria/koan/pull/42", stderr="")
            ok, url = create_pull_request("/tmp", "koan/fix", "main", "https://github.com/sukria/koan.git")
            assert ok is True
            assert "pull/42" in url
            # Verify gh CLI was called with correct args
            call_args = mock_run.call_args[0][0]
            assert "gh" in call_args
            assert "--repo" in call_args
            assert "sukria/koan" in call_args
            assert "--head" in call_args
            # Head should be "atoomic:koan/fix" for cross-fork PR
            head_idx = call_args.index("--head") + 1
            assert call_args[head_idx] == "atoomic:koan/fix"

    def test_gh_failure(self):
        """gh pr create failure returns error."""
        with patch("app.git_auto_merge.get_origin_url", return_value="https://github.com/atoomic/koan.git"), \
             patch("app.git_auto_merge.get_branch_commit_messages", return_value=[]), \
             patch("app.git_auto_merge.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="already exists")
            ok, err = create_pull_request("/tmp", "koan/fix", "main", "https://github.com/sukria/koan.git")
            assert ok is False
            assert "already exists" in err

    def test_gh_not_installed(self):
        """Missing gh CLI returns helpful error."""
        with patch("app.git_auto_merge.get_origin_url", return_value="https://github.com/atoomic/koan.git"), \
             patch("app.git_auto_merge.get_branch_commit_messages", return_value=[]), \
             patch("app.git_auto_merge.subprocess.run", side_effect=FileNotFoundError):
            ok, err = create_pull_request("/tmp", "koan/fix", "main", "https://github.com/sukria/koan.git")
            assert ok is False
            assert "gh CLI not found" in err

    def test_gh_timeout(self):
        """gh CLI timeout returns error."""
        import subprocess as sp
        with patch("app.git_auto_merge.get_origin_url", return_value="https://github.com/atoomic/koan.git"), \
             patch("app.git_auto_merge.get_branch_commit_messages", return_value=[]), \
             patch("app.git_auto_merge.subprocess.run", side_effect=sp.TimeoutExpired("gh", 30)):
            ok, err = create_pull_request("/tmp", "koan/fix", "main", "https://github.com/sukria/koan.git")
            assert ok is False
            assert "timed out" in err

    def test_bad_upstream_url(self):
        """Unparseable upstream URL returns error."""
        with patch("app.git_auto_merge.get_origin_url", return_value="https://github.com/atoomic/koan.git"):
            ok, err = create_pull_request("/tmp", "koan/fix", "main", "notaurl")
            assert ok is False
            assert "Cannot parse" in err


# --- write_pr_success_to_journal ---

class TestWritePrSuccessToJournal:
    def test_writes_pr_entry(self, tmp_path):
        """PR success entry written to journal."""
        inst = str(tmp_path)
        write_pr_success_to_journal(inst, "koan", "koan/fix", "main", "https://github.com/sukria/koan/pull/42")

        from datetime import datetime
        journal_file = tmp_path / "journal" / datetime.now().strftime("%Y-%m-%d") / "koan.md"
        assert journal_file.exists()
        content = journal_file.read_text()
        assert "Pull Request Created" in content
        assert "pull/42" in content
        assert "Fork detected" in content


# --- Integration Tests ---

class TestIntegration:
    def test_full_config_resolution_koan_project(self):
        """Test full config resolution for koan project."""
        config = {
            "git_auto_merge": {
                "enabled": True,
                "base_branch": "main",
                "strategy": "squash",
                "rules": [{"pattern": "koan/*", "auto_merge": True, "delete_after_merge": True}]
            },
            "projects": {
                "koan": {
                    "git_auto_merge": {
                        "enabled": True,
                        "base_branch": "main",
                        "strategy": "squash"
                    }
                }
            }
        }

        merged = get_auto_merge_config(config, "koan")
        should_merge, rule, base_branch = should_auto_merge(merged, "koan/fix-cors")

        assert should_merge is True
        assert base_branch == "main"
        assert merged["strategy"] == "squash"

    def test_full_config_resolution_backend_project(self):
        """Test full config resolution for backend project with overrides."""
        config = {
            "git_auto_merge": {
                "enabled": True,
                "base_branch": "main",
                "strategy": "squash",
                "rules": [{"pattern": "koan/*", "auto_merge": True}]
            },
            "projects": {
                "backend": {
                    "git_auto_merge": {
                        "base_branch": "develop",
                        "strategy": "merge",
                        "rules": [
                            {"pattern": "koan/hotfix-*", "auto_merge": True, "base_branch": "main"},
                            {"pattern": "koan/*", "auto_merge": True, "base_branch": "develop"}
                        ]
                    }
                }
            }
        }

        merged = get_auto_merge_config(config, "backend")

        # Hotfix should go to main
        should_merge1, _, base1 = should_auto_merge(merged, "koan/hotfix-cors")
        assert should_merge1 is True
        assert base1 == "main"

        # Regular branch should go to develop
        should_merge2, _, base2 = should_auto_merge(merged, "koan/feature-auth")
        assert should_merge2 is True
        assert base2 == "develop"

        assert merged["strategy"] == "merge"  # Overridden


# --- run_git ---

class TestRunGit:
    def test_success(self):
        """run_git returns exit code, stdout, stderr."""
        with patch("app.git_auto_merge.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="output\n", stderr="")
            code, out, err = run_git("/tmp", "status")
            assert code == 0
            assert out == "output"
            assert err == ""
            mock_run.assert_called_once_with(
                ["git", "status"], cwd="/tmp", capture_output=True, text=True, timeout=30, env=None
            )

    def test_failure(self):
        """run_git returns non-zero on failure."""
        with patch("app.git_auto_merge.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=128, stdout="", stderr="fatal: error")
            code, out, err = run_git("/tmp", "checkout", "main")
            assert code == 128
            assert err == "fatal: error"

    def test_timeout(self):
        """run_git handles subprocess timeout."""
        import subprocess as sp
        with patch("app.git_auto_merge.subprocess.run", side_effect=sp.TimeoutExpired("git", 30)):
            code, out, err = run_git("/tmp", "fetch")
            assert code == 1
            assert "timed out" in err

    def test_exception(self):
        """run_git handles generic exceptions."""
        with patch("app.git_auto_merge.subprocess.run", side_effect=OSError("no git")):
            code, out, err = run_git("/tmp", "status")
            assert code == 1
            assert "no git" in err


# --- is_working_tree_clean ---

class TestIsWorkingTreeClean:
    def test_clean(self):
        """Clean working tree returns True."""
        with patch("app.git_auto_merge.run_git", return_value=(0, "", "")):
            assert is_working_tree_clean("/tmp") is True

    def test_dirty(self):
        """Dirty working tree returns False."""
        with patch("app.git_auto_merge.run_git", return_value=(0, "M file.py", "")):
            assert is_working_tree_clean("/tmp") is False

    def test_git_error(self):
        """Git error returns False."""
        with patch("app.git_auto_merge.run_git", return_value=(128, "", "not a repo")):
            assert is_working_tree_clean("/tmp") is False


# --- is_branch_pushed ---

class TestIsBranchPushed:
    def test_pushed(self):
        """Branch exists on remote."""
        with patch("app.git_auto_merge.run_git", return_value=(0, "abc123\trefs/heads/koan/fix", "")):
            assert is_branch_pushed("/tmp", "koan/fix") is True

    def test_not_pushed(self):
        """Branch not on remote."""
        with patch("app.git_auto_merge.run_git", return_value=(0, "", "")):
            assert is_branch_pushed("/tmp", "koan/fix") is False

    def test_git_error(self):
        """Git error returns False."""
        with patch("app.git_auto_merge.run_git", return_value=(128, "", "error")):
            assert is_branch_pushed("/tmp", "koan/fix") is False


# --- perform_merge ---

class TestPerformMerge:
    def _mock_git(self, call_results):
        """Helper: return different results for sequential run_git calls."""
        return patch("app.git_auto_merge.run_git", side_effect=call_results)

    def test_squash_success(self):
        """Squash merge: log, checkout, pull, merge --squash, commit, push, checkout (finally)."""
        calls = [
            (0, "fix bug\nadd test", ""),  # git log (commit messages)
            (0, "", ""),   # checkout main
            (0, "", ""),   # pull
            (0, "", ""),   # merge --squash
            (0, "", ""),   # commit
            (0, "", ""),   # push
            (0, "", ""),   # checkout main (finally)
        ]
        with self._mock_git(calls) as mock:
            ok, err = perform_merge("/tmp", "koan/fix", "main", "squash")
            assert ok is True
            assert err == ""
            # Verify commit message includes branch subjects
            commit_call = mock.call_args_list[4]
            msg = commit_call[0][2]  # 3rd positional arg to run_git is "-m", msg is after
            assert "koan: auto-merge koan/fix (squash)" in str(commit_call)
            assert "fix bug" in str(commit_call)

    def test_squash_conflict(self):
        """Squash merge conflict triggers reset --hard."""
        calls = [
            (0, "", ""),   # git log
            (0, "", ""),   # checkout
            (0, "", ""),   # pull
            (1, "", "CONFLICT"),  # merge --squash fails
            (0, "", ""),   # reset --hard
            (0, "", ""),   # checkout main (finally)
        ]
        with self._mock_git(calls):
            ok, err = perform_merge("/tmp", "koan/fix", "main", "squash")
            assert ok is False
            assert "conflict" in err.lower()

    def test_rebase_success(self):
        """Rebase merge: log, checkout, pull, rebase, checkout, ff-merge, push, checkout (finally)."""
        calls = [
            (0, "", ""),   # git log
            (0, "", ""),   # checkout main
            (0, "", ""),   # pull
            (0, "", ""),   # rebase
            (0, "", ""),   # checkout main (after rebase)
            (0, "", ""),   # merge --ff-only
            (0, "", ""),   # push
            (0, "", ""),   # checkout main (finally)
        ]
        with self._mock_git(calls):
            ok, err = perform_merge("/tmp", "koan/fix", "main", "rebase")
            assert ok is True

    def test_rebase_conflict(self):
        """Rebase conflict triggers rebase --abort."""
        calls = [
            (0, "", ""),   # git log
            (0, "", ""),   # checkout
            (0, "", ""),   # pull
            (1, "", "CONFLICT"),  # rebase fails
            (0, "", ""),   # rebase --abort
            (0, "", ""),   # checkout main (finally)
        ]
        with self._mock_git(calls):
            ok, err = perform_merge("/tmp", "koan/fix", "main", "rebase")
            assert ok is False
            assert "conflict" in err.lower()

    def test_merge_noff_success(self):
        """Regular merge with --no-ff."""
        calls = [
            (0, "", ""),   # git log
            (0, "", ""),   # checkout
            (0, "", ""),   # pull
            (0, "", ""),   # merge --no-ff
            (0, "", ""),   # push
            (0, "", ""),   # checkout main (finally)
        ]
        with self._mock_git(calls):
            ok, err = perform_merge("/tmp", "koan/fix", "main", "merge")
            assert ok is True

    def test_merge_noff_conflict(self):
        """Regular merge conflict triggers merge --abort."""
        calls = [
            (0, "", ""),   # git log
            (0, "", ""),   # checkout
            (0, "", ""),   # pull
            (1, "", "CONFLICT"),  # merge fails
            (0, "", ""),   # merge --abort
            (0, "", ""),   # checkout main (finally)
        ]
        with self._mock_git(calls):
            ok, err = perform_merge("/tmp", "koan/fix", "main", "merge")
            assert ok is False
            assert "conflict" in err.lower()

    def test_checkout_failure(self):
        """Checkout failure aborts early."""
        calls = [
            (0, "", ""),   # git log
            (1, "", "error: pathspec 'main' did not match"),  # checkout fails
            (0, "", ""),   # checkout main (finally)
        ]
        with self._mock_git(calls):
            ok, err = perform_merge("/tmp", "koan/fix", "main", "squash")
            assert ok is False
            assert "checkout" in err.lower()

    def test_pull_failure(self):
        """Pull failure aborts."""
        calls = [
            (0, "", ""),   # git log
            (0, "", ""),   # checkout ok
            (1, "", "fatal: unable to access"),  # pull fails
            (0, "", ""),   # checkout main (finally)
        ]
        with self._mock_git(calls):
            ok, err = perform_merge("/tmp", "koan/fix", "main", "squash")
            assert ok is False
            assert "pull" in err.lower()

    def test_push_failure(self):
        """Push failure after successful merge."""
        calls = [
            (0, "", ""),   # git log
            (0, "", ""),   # checkout
            (0, "", ""),   # pull
            (0, "", ""),   # merge --squash
            (0, "", ""),   # commit
            (1, "", "rejected"),  # push fails
            (0, "", ""),   # checkout main (finally)
        ]
        with self._mock_git(calls):
            ok, err = perform_merge("/tmp", "koan/fix", "main", "squash")
            assert ok is False
            assert "push" in err.lower()

    def test_rebase_ff_merge_failure(self):
        """After successful rebase, ff-merge fails."""
        calls = [
            (0, "", ""),   # git log
            (0, "", ""),   # checkout
            (0, "", ""),   # pull
            (0, "", ""),   # rebase ok
            (0, "", ""),   # checkout main
            (1, "", "not a fast-forward"),  # ff-merge fails
            (0, "", ""),   # checkout main (finally)
        ]
        with self._mock_git(calls):
            ok, err = perform_merge("/tmp", "koan/fix", "main", "rebase")
            assert ok is False
            assert "fast-forward" in err.lower()


# --- cleanup_local_branch ---

class TestCleanupLocalBranch:
    def test_safe_delete_success(self):
        """Normal delete with -d succeeds."""
        with patch("app.git_auto_merge.run_git", return_value=(0, "", "")):
            assert cleanup_local_branch("/tmp", "koan/fix") is True

    def test_force_delete_fallback(self):
        """When -d fails, falls back to -D."""
        calls = [
            (1, "", "not fully merged"),  # branch -d fails
            (0, "", ""),   # branch -D succeeds
        ]
        with patch("app.git_auto_merge.run_git", side_effect=calls):
            assert cleanup_local_branch("/tmp", "koan/fix") is True

    def test_both_deletes_fail(self):
        """When both -d and -D fail, return False."""
        calls = [
            (1, "", "error"),  # branch -d fails
            (1, "", "error"),  # branch -D fails
        ]
        with patch("app.git_auto_merge.run_git", side_effect=calls):
            assert cleanup_local_branch("/tmp", "koan/fix") is False


# --- cleanup_remote_branch ---

class TestCleanupRemoteBranch:
    def test_remote_delete_success(self):
        """Remote delete succeeds."""
        with patch("app.git_auto_merge.run_git", return_value=(0, "", "")):
            assert cleanup_remote_branch("/tmp", "koan/fix") is True

    def test_remote_delete_fails(self):
        """Remote delete fails."""
        with patch("app.git_auto_merge.run_git", return_value=(1, "", "error")):
            assert cleanup_remote_branch("/tmp", "koan/fix") is False


# --- cleanup_branch (backward compat wrapper) ---

class TestCleanupBranch:
    def test_cleanup_success(self):
        """Delete local + remote branch."""
        calls = [
            (0, "", ""),   # branch -d
            (0, "", ""),   # push --delete
        ]
        with patch("app.git_auto_merge.run_git", side_effect=calls):
            assert cleanup_branch("/tmp", "koan/fix") is True

    def test_force_delete_fallback(self):
        """When -d fails, falls back to -D then remote."""
        calls = [
            (1, "", "not fully merged"),  # branch -d fails
            (0, "", ""),   # branch -D succeeds
            (0, "", ""),   # push --delete
        ]
        with patch("app.git_auto_merge.run_git", side_effect=calls):
            assert cleanup_branch("/tmp", "koan/fix") is True

    def test_both_deletes_fail(self):
        """When both -d and -D fail, return False (don't try remote)."""
        calls = [
            (1, "", "error"),  # branch -d fails
            (1, "", "error"),  # branch -D fails
        ]
        with patch("app.git_auto_merge.run_git", side_effect=calls):
            assert cleanup_branch("/tmp", "koan/fix") is False

    def test_remote_delete_fails(self):
        """Local delete ok but remote delete fails."""
        calls = [
            (0, "", ""),   # branch -d ok
            (1, "", "remote error"),  # push --delete fails
        ]
        with patch("app.git_auto_merge.run_git", side_effect=calls):
            assert cleanup_branch("/tmp", "koan/fix") is False


# --- Journal writers ---

class TestJournalWriters:
    def test_write_merge_success(self, tmp_path):
        """Write success entry to journal."""
        inst = str(tmp_path)
        write_merge_success_to_journal(inst, "koan", "koan/fix", "main", "squash")

        from datetime import datetime
        journal_dir = tmp_path / "journal" / datetime.now().strftime("%Y-%m-%d")
        journal_file = journal_dir / "koan.md"
        assert journal_file.exists()
        content = journal_file.read_text()
        assert "✓ Merged `koan/fix`" in content
        assert "squash" in content

    def test_write_merge_failure(self, tmp_path):
        """Write failure entry to journal."""
        inst = str(tmp_path)
        write_merge_failure_to_journal(inst, "koan", "koan/fix", "Working tree dirty")

        from datetime import datetime
        journal_dir = tmp_path / "journal" / datetime.now().strftime("%Y-%m-%d")
        journal_file = journal_dir / "koan.md"
        assert journal_file.exists()
        content = journal_file.read_text()
        assert "✗ Failed to merge `koan/fix`" in content
        assert "Working tree dirty" in content
        assert "Manual intervention" in content

    def test_journal_append(self, tmp_path):
        """Multiple writes append, don't overwrite."""
        inst = str(tmp_path)
        write_merge_success_to_journal(inst, "koan", "koan/a", "main", "squash")
        write_merge_success_to_journal(inst, "koan", "koan/b", "main", "merge")

        from datetime import datetime
        journal_file = tmp_path / "journal" / datetime.now().strftime("%Y-%m-%d") / "koan.md"
        content = journal_file.read_text()
        assert "koan/a" in content
        assert "koan/b" in content


# --- auto_merge_branch (orchestrator) ---

class TestAutoMergeBranch:
    def _base_config(self):
        return {
            "git_auto_merge": {
                "enabled": True,
                "base_branch": "main",
                "strategy": "squash",
                "rules": [{"pattern": "koan/*", "auto_merge": True, "delete_after_merge": False}]
            }
        }

    @patch("app.git_auto_merge.cleanup_local_branch", return_value=True)
    @patch("app.git_auto_merge.write_merge_success_to_journal")
    @patch("app.git_auto_merge.perform_merge", return_value=(True, ""))
    @patch("app.git_auto_merge.is_branch_pushed", return_value=True)
    @patch("app.git_auto_merge.is_working_tree_clean", return_value=True)
    @patch("app.git_auto_merge.get_auto_merge_config")
    @patch("app.git_auto_merge.load_config")
    def test_success_flow(self, mock_load, mock_cfg, mock_clean, mock_pushed, mock_merge, mock_journal, mock_local_cleanup):
        """Happy path: config match, clean, pushed, merge ok. Local branch always deleted."""
        mock_load.return_value = self._base_config()
        mock_cfg.return_value = {
            "enabled": True, "base_branch": "main", "strategy": "squash",
            "rules": [{"pattern": "koan/*", "auto_merge": True}]
        }
        result = auto_merge_branch("/inst", "koan", "/proj", "koan/fix")
        assert result == 0
        mock_merge.assert_called_once_with("/proj", "koan/fix", "main", "squash")
        mock_local_cleanup.assert_called_once_with("/proj", "koan/fix")  # Always delete local
        mock_journal.assert_called_once()

    @patch("app.git_auto_merge.get_auto_merge_config")
    @patch("app.git_auto_merge.load_config")
    def test_not_configured(self, mock_load, mock_cfg):
        """Branch not matching any rule returns 0 (skip)."""
        mock_load.return_value = {}
        mock_cfg.return_value = {"enabled": True, "base_branch": "main", "strategy": "squash", "rules": []}
        result = auto_merge_branch("/inst", "koan", "/proj", "koan/fix")
        assert result == 0

    @patch("app.git_auto_merge.write_merge_failure_to_journal")
    @patch("app.git_auto_merge.is_working_tree_clean", return_value=False)
    @patch("app.git_auto_merge.get_auto_merge_config")
    @patch("app.git_auto_merge.load_config")
    def test_dirty_tree(self, mock_load, mock_cfg, mock_clean, mock_journal):
        """Dirty working tree fails with journal entry."""
        mock_load.return_value = self._base_config()
        mock_cfg.return_value = {
            "enabled": True, "base_branch": "main", "strategy": "squash",
            "rules": [{"pattern": "koan/*", "auto_merge": True}]
        }
        result = auto_merge_branch("/inst", "koan", "/proj", "koan/fix")
        assert result == 1
        mock_journal.assert_called_once()
        assert "uncommitted" in mock_journal.call_args[0][3].lower()

    @patch("app.git_auto_merge.write_merge_failure_to_journal")
    @patch("app.git_auto_merge.is_branch_pushed", return_value=False)
    @patch("app.git_auto_merge.is_working_tree_clean", return_value=True)
    @patch("app.git_auto_merge.get_auto_merge_config")
    @patch("app.git_auto_merge.load_config")
    def test_not_pushed(self, mock_load, mock_cfg, mock_clean, mock_pushed, mock_journal):
        """Branch not pushed fails with journal entry."""
        mock_load.return_value = self._base_config()
        mock_cfg.return_value = {
            "enabled": True, "base_branch": "main", "strategy": "squash",
            "rules": [{"pattern": "koan/*", "auto_merge": True}]
        }
        result = auto_merge_branch("/inst", "koan", "/proj", "koan/fix")
        assert result == 1
        mock_journal.assert_called_once()
        assert "not pushed" in mock_journal.call_args[0][3].lower()

    @patch("app.git_auto_merge.cleanup_remote_branch", return_value=True)
    @patch("app.git_auto_merge.cleanup_local_branch", return_value=True)
    @patch("app.git_auto_merge.write_merge_success_to_journal")
    @patch("app.git_auto_merge.perform_merge", return_value=(True, ""))
    @patch("app.git_auto_merge.is_branch_pushed", return_value=True)
    @patch("app.git_auto_merge.is_working_tree_clean", return_value=True)
    @patch("app.git_auto_merge.get_auto_merge_config")
    @patch("app.git_auto_merge.load_config")
    def test_delete_after_merge(self, mock_load, mock_cfg, mock_clean, mock_pushed, mock_merge, mock_journal, mock_local_cleanup, mock_remote_cleanup):
        """delete_after_merge triggers both local (always) and remote (configured) cleanup."""
        mock_load.return_value = self._base_config()
        mock_cfg.return_value = {
            "enabled": True, "base_branch": "main", "strategy": "squash",
            "rules": [{"pattern": "koan/*", "auto_merge": True, "delete_after_merge": True}]
        }
        result = auto_merge_branch("/inst", "koan", "/proj", "koan/fix")
        assert result == 0
        mock_local_cleanup.assert_called_once_with("/proj", "koan/fix")  # Always
        mock_remote_cleanup.assert_called_once_with("/proj", "koan/fix")  # When configured

    @patch("app.git_auto_merge.cleanup_remote_branch")
    @patch("app.git_auto_merge.cleanup_local_branch", return_value=True)
    @patch("app.git_auto_merge.write_merge_success_to_journal")
    @patch("app.git_auto_merge.perform_merge", return_value=(True, ""))
    @patch("app.git_auto_merge.is_branch_pushed", return_value=True)
    @patch("app.git_auto_merge.is_working_tree_clean", return_value=True)
    @patch("app.git_auto_merge.get_auto_merge_config")
    @patch("app.git_auto_merge.load_config")
    def test_no_remote_delete_without_config(self, mock_load, mock_cfg, mock_clean, mock_pushed, mock_merge, mock_journal, mock_local_cleanup, mock_remote_cleanup):
        """Without delete_after_merge, local branch is deleted but remote is kept."""
        mock_load.return_value = self._base_config()
        mock_cfg.return_value = {
            "enabled": True, "base_branch": "main", "strategy": "squash",
            "rules": [{"pattern": "koan/*", "auto_merge": True, "delete_after_merge": False}]
        }
        result = auto_merge_branch("/inst", "koan", "/proj", "koan/fix")
        assert result == 0
        mock_local_cleanup.assert_called_once_with("/proj", "koan/fix")  # Always delete local
        mock_remote_cleanup.assert_not_called()  # Remote NOT deleted

    @patch("app.git_auto_merge.write_merge_failure_to_journal")
    @patch("app.git_auto_merge.perform_merge", return_value=(False, "Merge conflict"))
    @patch("app.git_auto_merge.is_branch_pushed", return_value=True)
    @patch("app.git_auto_merge.is_working_tree_clean", return_value=True)
    @patch("app.git_auto_merge.get_auto_merge_config")
    @patch("app.git_auto_merge.load_config")
    def test_merge_failure(self, mock_load, mock_cfg, mock_clean, mock_pushed, mock_merge, mock_journal):
        """Merge failure returns 1 with journal entry."""
        mock_load.return_value = self._base_config()
        mock_cfg.return_value = {
            "enabled": True, "base_branch": "main", "strategy": "squash",
            "rules": [{"pattern": "koan/*", "auto_merge": True}]
        }
        result = auto_merge_branch("/inst", "koan", "/proj", "koan/fix")
        assert result == 1
        mock_journal.assert_called_once()

    @patch("app.git_auto_merge.write_pr_success_to_journal")
    @patch("app.git_auto_merge.create_pull_request", return_value=(True, "https://github.com/sukria/koan/pull/42"))
    @patch("app.git_auto_merge.is_upstream_origin", return_value=False)
    @patch("app.git_auto_merge.is_branch_pushed", return_value=True)
    @patch("app.git_auto_merge.is_working_tree_clean", return_value=True)
    @patch("app.git_auto_merge.get_auto_merge_config")
    @patch("app.git_auto_merge.load_config")
    def test_fork_creates_pr(self, mock_load, mock_cfg, mock_clean, mock_pushed, mock_upstream, mock_pr, mock_journal):
        """Fork detected: create PR instead of merge."""
        mock_load.return_value = self._base_config()
        mock_cfg.return_value = {
            "enabled": True, "base_branch": "main", "strategy": "squash",
            "upstream_url": "https://github.com/sukria/koan.git",
            "rules": [{"pattern": "koan/*", "auto_merge": True}]
        }
        result = auto_merge_branch("/inst", "koan", "/proj", "koan/fix")
        assert result == 0
        mock_pr.assert_called_once_with("/proj", "koan/fix", "main", "https://github.com/sukria/koan.git")
        mock_journal.assert_called_once()

    @patch("app.git_auto_merge.write_merge_failure_to_journal")
    @patch("app.git_auto_merge.create_pull_request", return_value=(False, "already exists"))
    @patch("app.git_auto_merge.is_upstream_origin", return_value=False)
    @patch("app.git_auto_merge.is_branch_pushed", return_value=True)
    @patch("app.git_auto_merge.is_working_tree_clean", return_value=True)
    @patch("app.git_auto_merge.get_auto_merge_config")
    @patch("app.git_auto_merge.load_config")
    def test_fork_pr_failure(self, mock_load, mock_cfg, mock_clean, mock_pushed, mock_upstream, mock_pr, mock_journal):
        """Fork detected but PR creation fails."""
        mock_load.return_value = self._base_config()
        mock_cfg.return_value = {
            "enabled": True, "base_branch": "main", "strategy": "squash",
            "upstream_url": "https://github.com/sukria/koan.git",
            "rules": [{"pattern": "koan/*", "auto_merge": True}]
        }
        result = auto_merge_branch("/inst", "koan", "/proj", "koan/fix")
        assert result == 1
        mock_journal.assert_called_once()
        assert "PR creation failed" in mock_journal.call_args[0][3]

    @patch("app.git_auto_merge.perform_merge", return_value=(True, ""))
    @patch("app.git_auto_merge.cleanup_local_branch", return_value=True)
    @patch("app.git_auto_merge.write_merge_success_to_journal")
    @patch("app.git_auto_merge.is_upstream_origin", return_value=True)
    @patch("app.git_auto_merge.is_branch_pushed", return_value=True)
    @patch("app.git_auto_merge.is_working_tree_clean", return_value=True)
    @patch("app.git_auto_merge.get_auto_merge_config")
    @patch("app.git_auto_merge.load_config")
    def test_upstream_origin_merges_normally(self, mock_load, mock_cfg, mock_clean, mock_pushed, mock_upstream, mock_journal, mock_local_cleanup, mock_merge):
        """When origin IS upstream, proceed with normal merge."""
        mock_load.return_value = self._base_config()
        mock_cfg.return_value = {
            "enabled": True, "base_branch": "main", "strategy": "squash",
            "upstream_url": "https://github.com/sukria/koan.git",
            "rules": [{"pattern": "koan/*", "auto_merge": True}]
        }
        result = auto_merge_branch("/inst", "koan", "/proj", "koan/fix")
        assert result == 0
        mock_merge.assert_called_once()
        mock_journal.assert_called_once()

    @patch("app.git_auto_merge.perform_merge", return_value=(True, ""))
    @patch("app.git_auto_merge.cleanup_local_branch", return_value=True)
    @patch("app.git_auto_merge.write_merge_success_to_journal")
    @patch("app.git_auto_merge.is_branch_pushed", return_value=True)
    @patch("app.git_auto_merge.is_working_tree_clean", return_value=True)
    @patch("app.git_auto_merge.get_auto_merge_config")
    @patch("app.git_auto_merge.load_config")
    def test_no_upstream_url_merges_normally(self, mock_load, mock_cfg, mock_clean, mock_pushed, mock_journal, mock_local_cleanup, mock_merge):
        """When no upstream_url configured, merge normally (backward compat)."""
        mock_load.return_value = self._base_config()
        mock_cfg.return_value = {
            "enabled": True, "base_branch": "main", "strategy": "squash",
            "upstream_url": "",
            "rules": [{"pattern": "koan/*", "auto_merge": True}]
        }
        result = auto_merge_branch("/inst", "koan", "/proj", "koan/fix")
        assert result == 0
        mock_merge.assert_called_once()
