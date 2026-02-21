"""Tests for module-level prompt imports in CLI runner modules.

When a CLI runner subprocess operates on the koan repo itself, git checkouts
can change the Python source files on disk.  If prompts functions are imported
lazily (inside function bodies), the import may resolve against a different
branch's code.  Moving imports to module level ensures they are bound at
subprocess start time, before any git checkout can change the working tree.

This test suite verifies:
1. All CLI runner modules import load_skill_prompt at module level
2. No CLI runner has lazy (function-body) imports of prompt functions
3. The imported names are usable (not stale or missing)
"""

import ast
import importlib
import sys
from pathlib import Path

import pytest


# CLI runner modules that run as subprocesses and may do git checkouts
# on the project (which could be the koan repo itself).
CLI_RUNNER_MODULES = [
    "app.recreate_pr",
    "app.rebase_pr",
    "app.plan_runner",
    "app.ai_runner",
    "app.claudemd_refresh",
    "app.pr_review",
    "skills.core.implement.implement_runner",
    "skills.core.fix.fix_runner",
]


class TestModuleLevelPromptImports:
    """Verify prompt functions are imported at module level, not lazily."""

    @pytest.mark.parametrize("module_name", CLI_RUNNER_MODULES)
    def test_load_skill_prompt_at_module_level(self, module_name):
        """Each CLI runner must import load_skill_prompt at module level."""
        mod = importlib.import_module(module_name)
        assert hasattr(mod, "load_skill_prompt"), (
            f"{module_name} must import load_skill_prompt at module level "
            f"to avoid ImportError when the working tree changes during "
            f"git checkout (e.g. when project_path is the koan repo)."
        )

    @pytest.mark.parametrize("module_name", CLI_RUNNER_MODULES)
    def test_no_lazy_prompt_imports(self, module_name):
        """No lazy 'from app.prompts import ...' inside function bodies."""
        mod = importlib.import_module(module_name)
        source_file = Path(mod.__file__)
        tree = ast.parse(source_file.read_text())

        lazy_imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "app.prompts":
                # Check if this import is inside a function
                # (i.e., not at module level — col_offset alone isn't enough,
                # we need to check the parent)
                pass

        # More precise: walk the tree and track function nesting
        lazy_imports = _find_lazy_prompt_imports(tree)
        assert not lazy_imports, (
            f"{module_name} has lazy prompt imports inside functions at "
            f"line(s) {lazy_imports}. Move them to module level."
        )

    @pytest.mark.parametrize("module_name", CLI_RUNNER_MODULES)
    def test_imported_functions_are_callable(self, module_name):
        """The imported prompt functions must be the real functions."""
        mod = importlib.import_module(module_name)
        lsp = getattr(mod, "load_skill_prompt", None)
        assert callable(lsp), (
            f"{module_name}.load_skill_prompt is not callable"
        )
        # Verify it's the actual function from app.prompts
        from app.prompts import load_skill_prompt as original
        assert lsp is original, (
            f"{module_name}.load_skill_prompt is not the same object as "
            f"app.prompts.load_skill_prompt — possible stale import"
        )


def _find_lazy_prompt_imports(tree: ast.AST) -> list:
    """Find 'from app.prompts import ...' inside function bodies.

    Returns a list of line numbers where lazy imports were found.
    """
    lazy_lines = []

    class LazyImportFinder(ast.NodeVisitor):
        def __init__(self):
            self._in_function = False

        def visit_FunctionDef(self, node):
            old = self._in_function
            self._in_function = True
            self.generic_visit(node)
            self._in_function = old

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_ImportFrom(self, node):
            if (
                self._in_function
                and node.module == "app.prompts"
                and any(
                    alias.name in ("load_skill_prompt", "load_prompt")
                    for alias in node.names
                )
            ):
                lazy_lines.append(node.lineno)

    LazyImportFinder().visit(tree)
    return lazy_lines


class TestBuildPromptFunctions:
    """Test that prompt-building functions work with both skill_dir and without."""

    @pytest.fixture
    def pr_context(self):
        return {
            "title": "feat: test feature",
            "body": "Test body",
            "branch": "koan/test-branch",
            "base": "main",
            "diff": "+some changes",
            "review_comments": "looks fine",
            "reviews": "APPROVED",
            "issue_comments": "please fix",
        }

    @pytest.fixture
    def recreate_skill_dir(self):
        return Path(__file__).parent.parent / "skills" / "core" / "recreate"

    @pytest.fixture
    def rebase_skill_dir(self):
        return Path(__file__).parent.parent / "skills" / "core" / "rebase"

    def test_recreate_prompt_with_skill_dir(self, pr_context, recreate_skill_dir):
        """_build_recreate_prompt works with skill_dir using module-level import."""
        from app.recreate_pr import _build_recreate_prompt
        prompt = _build_recreate_prompt(pr_context, skill_dir=recreate_skill_dir)
        assert "test feature" in prompt
        assert "koan/test-branch" in prompt

    def test_recreate_prompt_without_skill_dir(self, pr_context):
        """_build_recreate_prompt falls back to load_prompt without skill_dir."""
        from unittest.mock import patch
        from app.recreate_pr import _build_recreate_prompt
        with patch("app.claude_step.load_prompt", return_value="fallback") as mock:
            result = _build_recreate_prompt(pr_context, skill_dir=None)
            mock.assert_called_once()
            assert result == "fallback"

    def test_rebase_prompt_with_skill_dir(self, pr_context, rebase_skill_dir):
        """_build_rebase_prompt works with skill_dir using module-level import."""
        from app.rebase_pr import _build_rebase_prompt
        prompt = _build_rebase_prompt(pr_context, skill_dir=rebase_skill_dir)
        assert "test feature" in prompt
        assert "koan/test-branch" in prompt

    def test_rebase_prompt_without_skill_dir(self, pr_context):
        """_build_rebase_prompt falls back to load_prompt without skill_dir."""
        from unittest.mock import patch
        from app.rebase_pr import _build_rebase_prompt
        with patch("app.claude_step.load_prompt", return_value="fallback") as mock:
            result = _build_rebase_prompt(pr_context, skill_dir=None)
            mock.assert_called_once()
            assert result == "fallback"
