"""Structural test: no silent broad exception handlers in app/ modules.

Broad exception catches (``except Exception``, ``except BaseException``)
that swallow errors without any diagnostic output make debugging extremely
difficult in production.  This test enforces the pattern established by
commit 6f2b8cc: every broad catch must emit at least one diagnostic message
(print to stderr, logging call, or the run.py log() helper).

Narrow catches (``except ValueError``, ``except FileNotFoundError``, etc.)
are intentionally excluded — those have well-understood semantics and are
often correctly handled with just ``pass`` or a default return.

Handlers that capture the exception variable (``except Exception as e``)
and reference it in the body (return, assign, pass to a function) are
considered non-silent — the error info propagates to the caller.

The allowlist covers known-acceptable patterns where a broad catch
intentionally discards the error (shutdown cleanup, best-effort display
helpers, config loading defaults, etc.).
"""

import ast
import sys
from pathlib import Path
from typing import List, Set, Tuple

import pytest

APP_DIR = Path(__file__).parent.parent / "app"

# Known-acceptable silent broad catches.
# Each entry is (filename, line_number).
# When adding: include a short justification comment.
ALLOWLIST: Set[Tuple[str, int]] = {
    # --- Shutdown / terminal cleanup (terminal may be gone) ---
    ("run.py", 78),                  # ANSI reset on shutdown
    # --- Best-effort display / info gathering ---
    ("ai_runner.py", 127),           # dir listing for prompt context
    ("startup_info.py", 25),         # config value fallback
    ("startup_info.py", 62),         # provider detection fallback
    ("startup_info.py", 80),         # project count fallback
    ("startup_info.py", 99),         # skill count fallback
    ("startup_info.py", 112),        # file size fallback
    ("dashboard.py", 99),            # pause file read for web dashboard
    # --- Config / init loading (defaults are safe) ---
    ("debug.py", 32),                # debug mode config loading
    ("pid_manager.py", 66),          # log rotation config loading
    ("provider/claude.py", 88),      # tool allowlist parsing
    ("provider/local.py", 39),       # model list parsing
    # --- Context gathering for prompts (empty string is safe) ---
    ("prompt_builder.py", 35),       # soul.md loading
    ("prompt_builder.py", 46),       # personality-evolution.md loading
    ("prompt_builder.py", 55),       # shared-journal.md loading
    ("awake.py", 173),               # pending.md read for chat context
    # --- GitHub API best-effort (None/empty is safe) ---
    ("github.py", 182),              # gh username cache miss
    ("github.py", 215),              # parent repo detection
    ("github_auth.py", 56),          # token validation
    # --- Git operations (abort after failed rebase) ---
    ("claude_step.py", 50),          # rebase --abort after failed rebase
    # --- Non-critical subsystem fallbacks ---
    ("iteration_manager.py", 318),   # recurring mission injection
    ("schedule_manager.py", 186),    # schedule check
    ("usage_tracker.py", 282),       # budget file read
    ("usage_tracker.py", 297),       # budget file read
    ("projects_merged.py", 245),     # github URL cache build
    # --- Setup wizard (interactive, errors shown in UI) ---
    ("setup_wizard.py", 85),         # config loading
    ("setup_wizard.py", 183),        # project path resolution
    # --- CLI runners: cleanup after main work done ---
    ("rebase_pr.py", 313),           # branch cleanup after success
    ("rebase_pr.py", 416),           # branch cleanup after success
    ("recreate_pr.py", 118),         # local branch delete (may not exist)
    ("recreate_pr.py", 222),         # fetch from origin/upstream fallback
    ("recreate_pr.py", 272),         # git log check fallback
    ("run.py", 1646),                # get current branch (best-effort)
    ("run.py", 1814),                # temp file cleanup
    # --- Prompt/config loading with hardcoded fallback ---
    ("local_llm_runner.py", 321),    # system prompt file fallback
    ("pid_manager.py", 468),         # provider detection fallback
    # --- Retry without optional parameter ---
    ("plan_runner.py", 123),         # issue label retry (inner catch has e2)
}


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------

# Names that indicate diagnostic output when called as a function.
_DIAG_CALL_NAMES: Set[str] = {
    "print", "warn", "warning", "debug_log",
}

# Attribute names that indicate diagnostic output (obj.method()).
_DIAG_ATTR_NAMES: Set[str] = {
    "debug", "info", "warning", "warn", "error", "critical", "exception",
    "log",
}

# Attribute chains that indicate diagnostic output (e.g. sys.stderr.write).
_DIAG_ATTR_CHAINS: Set[str] = {
    "sys.stderr",
}


def _is_broad_exception(handler: ast.ExceptHandler) -> bool:
    """Check if the handler catches Exception, BaseException, or bare except."""
    if handler.type is None:
        return True  # bare except
    if isinstance(handler.type, ast.Name) and handler.type.id in (
        "Exception", "BaseException",
    ):
        return True
    return False


def _references_var(body: List[ast.stmt], var_name: str) -> bool:
    """Check if handler body references the exception variable anywhere."""
    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        if isinstance(node, ast.Name) and node.id == var_name:
            return True
    return False


def _has_diagnostic_output(body: List[ast.stmt]) -> bool:
    """Check if a handler body contains any diagnostic output statement.

    Looks for:
    - print(..., file=sys.stderr) or any print() call (common in koan)
    - log(...), log.error(...), log.warning(...), etc.
    - logging.error(...), logging.warning(...), etc.
    - sys.stderr.write(...)
    - _log_iteration(...), debug_log(...)
    - raise (re-raising counts as not-silent)
    """
    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        # raise statement — exception is not swallowed
        if isinstance(node, ast.Raise):
            return True

        if not isinstance(node, ast.Call):
            continue

        func = node.func

        # Simple function call: print(...), log(...), _log_iteration(...)
        if isinstance(func, ast.Name):
            if func.id in _DIAG_CALL_NAMES:
                return True
            # log() and _log_iteration() are used in run.py and iteration_manager
            if func.id in ("log", "_log_iteration"):
                return True

        # Attribute call: log.error(...), logging.warning(...), sys.stderr.write(...)
        if isinstance(func, ast.Attribute):
            if func.attr in _DIAG_ATTR_NAMES:
                return True
            # Check for sys.stderr.write pattern
            if isinstance(func.value, ast.Attribute):
                if isinstance(func.value.value, ast.Name):
                    chain = f"{func.value.value.id}.{func.value.attr}"
                    if chain in _DIAG_ATTR_CHAINS:
                        return True

        # print(..., file=sys.stderr) — check keyword args
        if isinstance(func, ast.Name) and func.id == "print":
            for kw in node.keywords:
                if kw.arg == "file":
                    return True

    return False


def _find_silent_broad_catches(filepath: Path) -> List[Tuple[int, str]]:
    """Find broad exception catches without diagnostic output.

    Returns list of (line_number, handler_text) for violations.
    """
    try:
        source = filepath.read_text()
        tree = ast.parse(source, str(filepath))
    except SyntaxError:
        return []

    lines = source.splitlines()
    violations = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue

        if not _is_broad_exception(node):
            continue

        # If handler captures the exception variable and references it
        # in the body, the error info is propagated (not silent).
        if node.name and _references_var(node.body, node.name):
            continue

        # Check if handler body has diagnostic output
        if _has_diagnostic_output(node.body):
            continue

        # Check allowlist (supports both flat filenames and subdir paths)
        rel_name = filepath.name
        try:
            rel_path = str(filepath.relative_to(APP_DIR))
        except ValueError:
            rel_path = rel_name
        if (rel_name, node.lineno) in ALLOWLIST or \
           (rel_path, node.lineno) in ALLOWLIST:
            continue

        # Extract the except line for context
        line_text = lines[node.lineno - 1].strip() if node.lineno <= len(lines) else ""
        violations.append((node.lineno, line_text))

    return violations


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def _collect_all_app_files() -> List[Path]:
    """Collect all Python files under app/ (including subdirectories)."""
    files = []
    for py_file in sorted(APP_DIR.rglob("*.py")):
        if py_file.name == "__init__.py":
            continue
        files.append(py_file)
    return files


class TestNoSilentBroadExceptions:
    """AST scan: every broad exception catch must have diagnostic output."""

    def test_no_silent_broad_catches_in_app(self):
        """Scan all app/**/*.py files for silent broad exception handlers.

        A 'silent broad catch' is an ``except Exception`` (or BaseException,
        or bare ``except``) whose handler body contains no diagnostic output
        (no print, no logging, no log(), no raise).

        This prevents the anti-pattern where errors are silently swallowed,
        making debugging nearly impossible.
        """
        all_violations = {}

        for py_file in _collect_all_app_files():
            violations = _find_silent_broad_catches(py_file)
            if violations:
                # Use path relative to app/ for readability
                try:
                    rel = py_file.relative_to(APP_DIR)
                except ValueError:
                    rel = py_file.name
                all_violations[str(rel)] = violations

        if all_violations:
            msg_parts = [
                "Silent broad exception handler(s) detected.",
                "Every `except Exception` must have diagnostic output",
                "(print to stderr, log(), logging.*, or raise).",
                "",
                "Violations:",
            ]
            for fname, violations in sorted(all_violations.items()):
                for line_no, context in violations:
                    msg_parts.append(f"  app/{fname}:{line_no} — {context}")
            msg_parts.append("")
            msg_parts.append(
                "Fix: add `print(f'[module] error: {e}', file=sys.stderr)` "
                "or use logging/log()."
            )
            msg_parts.append(
                "If the catch is intentionally silent, add it to ALLOWLIST "
                "in test_silent_exceptions.py with a comment."
            )
            pytest.fail("\n".join(msg_parts))


class TestScannerAccuracy:
    """Verify the scanner correctly identifies known patterns."""

    def test_detects_silent_except_pass(self):
        """Catches `except Exception: pass`."""
        code = "try:\n    x()\nexcept Exception:\n    pass\n"
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                assert _is_broad_exception(node)
                assert not _has_diagnostic_output(node.body)

    def test_detects_silent_except_return(self):
        """Catches `except Exception: return None`."""
        code = "def f():\n  try:\n    x()\n  except Exception:\n    return None\n"
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                assert _is_broad_exception(node)
                assert not _has_diagnostic_output(node.body)

    def test_allows_except_with_print_stderr(self):
        """Allows `except Exception as e: print(f'error: {e}', file=sys.stderr)`."""
        code = (
            "import sys\n"
            "try:\n    x()\nexcept Exception as e:\n"
            "    print(f'error: {e}', file=sys.stderr)\n"
        )
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                assert _has_diagnostic_output(node.body)

    def test_allows_except_with_log_call(self):
        """Allows `except Exception as e: log('error', f'failed: {e}')`."""
        code = "try:\n    x()\nexcept Exception as e:\n    log('error', f'failed: {e}')\n"
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                assert _has_diagnostic_output(node.body)

    def test_allows_except_with_logging(self):
        """Allows `except Exception as e: logging.error(...)`."""
        code = "try:\n    x()\nexcept Exception as e:\n    logging.error(f'failed: {e}')\n"
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                assert _has_diagnostic_output(node.body)

    def test_allows_except_with_log_attribute(self):
        """Allows `except Exception as e: log.error(...)`."""
        code = "try:\n    x()\nexcept Exception as e:\n    log.error(f'failed: {e}')\n"
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                assert _has_diagnostic_output(node.body)

    def test_allows_except_with_raise(self):
        """Allows `except Exception: raise` (re-raise, not silent)."""
        code = "try:\n    x()\nexcept Exception:\n    cleanup()\n    raise\n"
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                assert _has_diagnostic_output(node.body)

    def test_ignores_specific_exceptions(self):
        """Does not flag `except ValueError: pass`."""
        code = "try:\n    x()\nexcept ValueError:\n    pass\n"
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                assert not _is_broad_exception(node)

    def test_ignores_specific_tuple_exceptions(self):
        """Does not flag `except (ValueError, KeyError): pass`."""
        code = "try:\n    x()\nexcept (ValueError, KeyError):\n    pass\n"
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                assert not _is_broad_exception(node)

    def test_detects_bare_except(self):
        """Catches bare `except:` (no type specified)."""
        code = "try:\n    x()\nexcept:\n    pass\n"
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                assert _is_broad_exception(node)

    def test_allows_print_no_file_kwarg(self):
        """Allows `except Exception: print(...)` (any print counts)."""
        code = "try:\n    x()\nexcept Exception as e:\n    print(f'error: {e}')\n"
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                assert _has_diagnostic_output(node.body)

    def test_allows_log_iteration(self):
        """Allows _log_iteration() helper used in iteration_manager."""
        code = (
            "try:\n    x()\nexcept Exception as e:\n"
            "    _log_iteration('error', f'failed: {e}')\n"
        )
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                assert _has_diagnostic_output(node.body)

    def test_allows_debug_log(self):
        """Allows debug_log() helper used in skill_dispatch etc."""
        code = (
            "try:\n    x()\nexcept Exception as e:\n"
            "    debug_log(f'error: {e}')\n"
        )
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                assert _has_diagnostic_output(node.body)


class TestExceptionVarPropagation:
    """Handlers that reference the exception variable are not silent."""

    def test_return_with_error_var_is_not_silent(self):
        """Return with exception variable means error propagates."""
        code = (
            "def f():\n  try:\n    x()\n  except Exception as e:\n"
            "    return False, f'failed: {e}'\n"
        )
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                assert node.name == "e"
                assert _references_var(node.body, node.name)

    def test_notify_with_error_var_is_not_silent(self):
        """Passing exception to a notify function means error propagates."""
        code = (
            "try:\n    x()\nexcept Exception as e:\n"
            "    notify_fn(f'error: {e}')\n"
        )
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                assert _references_var(node.body, node.name)

    def test_assign_error_var_is_not_silent(self):
        """Assigning exception to variable for later use is not silent."""
        code = (
            "try:\n    x()\nexcept Exception as push_error:\n"
            "    error_msg = str(push_error)\n"
        )
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                assert _references_var(node.body, node.name)

    def test_unused_var_is_still_silent(self):
        """Handler with `as e` but no reference to e is still silent."""
        code = (
            "def f():\n  try:\n    x()\n  except Exception as e:\n"
            "    return None\n"
        )
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                assert not _references_var(node.body, node.name)

    def test_no_var_is_silent(self):
        """Handler without `as` variable has no var to reference."""
        code = "try:\n    x()\nexcept Exception:\n    pass\n"
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                assert node.name is None
