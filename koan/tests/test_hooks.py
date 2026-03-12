"""Tests for the hook registry and discovery system."""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from app.hooks import (
    HookRegistry,
    fire_hook,
    get_registry,
    init_hooks,
    reset_registry,
)


def _write_hook(hooks_dir: Path, name: str, code: str):
    """Write a hook module to the hooks directory."""
    (hooks_dir / f"{name}.py").write_text(code)


# ---------------------------------------------------------------------------
# HookRegistry
# ---------------------------------------------------------------------------


class TestHookRegistry:
    def test_empty_dir(self, tmp_path):
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        reg = HookRegistry(hooks_dir)
        assert not reg.has_hooks("post_mission")

    def test_nonexistent_dir(self, tmp_path):
        reg = HookRegistry(tmp_path / "missing")
        assert not reg.has_hooks("post_mission")

    def test_discover_single_hook(self, tmp_path):
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        _write_hook(hooks_dir, "tracker", (
            "calls = []\n"
            "def handler(ctx): calls.append(ctx)\n"
            "HOOKS = {'post_mission': handler}\n"
        ))
        reg = HookRegistry(hooks_dir)
        assert reg.has_hooks("post_mission")
        assert not reg.has_hooks("session_start")

    def test_fire_calls_handler(self, tmp_path):
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        _write_hook(hooks_dir, "collector", (
            "calls = []\n"
            "def handler(ctx): calls.append(ctx)\n"
            "HOOKS = {'post_mission': handler}\n"
        ))
        reg = HookRegistry(hooks_dir)
        reg.fire("post_mission", project_name="test", exit_code=0)
        mod = sys.modules["koan_hook_collector"]
        assert len(mod.calls) == 1
        assert mod.calls[0]["project_name"] == "test"
        assert mod.calls[0]["exit_code"] == 0

    def test_fire_multiple_handlers(self, tmp_path):
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        _write_hook(hooks_dir, "alpha", (
            "calls = []\n"
            "def handler(ctx): calls.append('alpha')\n"
            "HOOKS = {'session_start': handler}\n"
        ))
        _write_hook(hooks_dir, "beta", (
            "calls = []\n"
            "def handler(ctx): calls.append('beta')\n"
            "HOOKS = {'session_start': handler}\n"
        ))
        reg = HookRegistry(hooks_dir)
        reg.fire("session_start", instance_dir="/tmp")
        assert len(sys.modules["koan_hook_alpha"].calls) == 1
        assert len(sys.modules["koan_hook_beta"].calls) == 1

    def test_fire_nonexistent_event(self, tmp_path):
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        reg = HookRegistry(hooks_dir)
        # Should not raise
        reg.fire("nonexistent_event", data="hello")

    def test_handler_error_isolated(self, tmp_path, capsys):
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        _write_hook(hooks_dir, "broken", (
            "def handler(ctx): raise ValueError('boom')\n"
            "HOOKS = {'post_mission': handler}\n"
        ))
        _write_hook(hooks_dir, "working", (
            "calls = []\n"
            "def handler(ctx): calls.append(True)\n"
            "HOOKS = {'post_mission': handler}\n"
        ))
        reg = HookRegistry(hooks_dir)
        reg.fire("post_mission", data="test")
        # Working handler should still be called despite broken one
        assert len(sys.modules["koan_hook_working"].calls) == 1
        # Error should be logged to stderr
        captured = capsys.readouterr()
        assert "boom" in captured.err

    def test_skip_underscore_files(self, tmp_path):
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        _write_hook(hooks_dir, "_private", (
            "def handler(ctx): pass\n"
            "HOOKS = {'post_mission': handler}\n"
        ))
        reg = HookRegistry(hooks_dir)
        assert not reg.has_hooks("post_mission")

    def test_skip_module_without_hooks_dict(self, tmp_path):
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        _write_hook(hooks_dir, "no_hooks", "x = 42\n")
        reg = HookRegistry(hooks_dir)
        assert not reg.has_hooks("post_mission")

    def test_skip_non_callable(self, tmp_path):
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        _write_hook(hooks_dir, "bad_handler", (
            "HOOKS = {'post_mission': 'not_a_function'}\n"
        ))
        reg = HookRegistry(hooks_dir)
        assert not reg.has_hooks("post_mission")

    def test_load_error_logged(self, tmp_path, capsys):
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        _write_hook(hooks_dir, "syntax_error", "def (\n")
        reg = HookRegistry(hooks_dir)
        captured = capsys.readouterr()
        assert "Failed to load syntax_error.py" in captured.err

    def test_multiple_events_same_module(self, tmp_path):
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        _write_hook(hooks_dir, "multi", (
            "pre_calls = []\n"
            "post_calls = []\n"
            "def on_pre(ctx): pre_calls.append(True)\n"
            "def on_post(ctx): post_calls.append(True)\n"
            "HOOKS = {'pre_mission': on_pre, 'post_mission': on_post}\n"
        ))
        reg = HookRegistry(hooks_dir)
        assert reg.has_hooks("pre_mission")
        assert reg.has_hooks("post_mission")
        reg.fire("pre_mission", data="a")
        reg.fire("post_mission", data="b")
        mod = sys.modules["koan_hook_multi"]
        assert len(mod.pre_calls) == 1
        assert len(mod.post_calls) == 1


# ---------------------------------------------------------------------------
# Module-level API
# ---------------------------------------------------------------------------


class TestFireHookConvenience:
    def setup_method(self):
        reset_registry()

    def teardown_method(self):
        reset_registry()

    def test_fire_hook_noop_without_init(self):
        # Should not raise when registry is None
        fire_hook("post_mission", project_name="test")

    def test_fire_hook_after_init(self, tmp_path):
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        _write_hook(hooks_dir, "tracker", (
            "calls = []\n"
            "def handler(ctx): calls.append(ctx)\n"
            "HOOKS = {'post_mission': handler}\n"
        ))
        # init_hooks expects instance_dir, hooks_dir = instance_dir/hooks
        init_hooks(str(tmp_path))
        fire_hook("post_mission", project_name="proj")
        mod = sys.modules["koan_hook_tracker"]
        assert len(mod.calls) == 1


class TestInitHooks:
    def setup_method(self):
        reset_registry()

    def teardown_method(self):
        reset_registry()

    def test_creates_hooks_dir(self, tmp_path):
        init_hooks(str(tmp_path))
        assert (tmp_path / "hooks").is_dir()
        assert get_registry() is not None

    def test_reinitialize(self, tmp_path):
        init_hooks(str(tmp_path))
        reg1 = get_registry()
        init_hooks(str(tmp_path))
        reg2 = get_registry()
        assert reg1 is not reg2


class TestResetRegistry:
    def test_reset(self, tmp_path):
        init_hooks(str(tmp_path))
        assert get_registry() is not None
        reset_registry()
        assert get_registry() is None
