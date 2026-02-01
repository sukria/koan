"""Tests for git_auto_merge.py — automatic branch merging."""

import pytest
from app.git_auto_merge import (
    find_matching_rule,
    should_auto_merge,
    run_git,
    is_working_tree_clean,
    is_branch_pushed,
    perform_merge,
    cleanup_branch,
    auto_merge_branch,
    write_merge_success_to_journal,
    write_merge_failure_to_journal,
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

    def test_missing_config_section(self):
        """When git_auto_merge section missing, return safe defaults."""
        config = {}
        result = get_auto_merge_config(config, "koan")

        assert result["enabled"] is True  # Default
        assert result["base_branch"] == "main"  # Default
        assert result["strategy"] == "squash"  # Default
        assert result["rules"] == []  # Empty


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
