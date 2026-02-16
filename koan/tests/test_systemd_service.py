"""Tests for systemd service PATH building and template rendering."""

import os
import textwrap

from app.systemd_service import build_safe_path, render_all_templates, render_service_template


class TestBuildSafePath:
    """Tests for build_safe_path()."""

    def test_filters_home_dirs(self):
        raw = "/usr/bin:/home/alice/.local/bin:/home/alice/bin:/usr/local/bin"
        result = build_safe_path(raw, "/home/alice")
        assert "/home/alice/.local/bin" not in result.split(":")
        assert "/home/alice/bin" not in result.split(":")
        assert "/usr/bin" in result.split(":")
        assert "/usr/local/bin" in result.split(":")

    def test_filters_root_home_dirs(self):
        raw = "/root/.local/bin:/root/.cargo/bin:/usr/bin:/usr/local/bin"
        result = build_safe_path(raw, "/root")
        assert "/root/.local/bin" not in result.split(":")
        assert "/root/.cargo/bin" not in result.split(":")
        assert "/usr/bin" in result.split(":")

    def test_ensures_essentials(self):
        raw = "/opt/custom/bin"
        result = build_safe_path(raw, "/home/user")
        parts = result.split(":")
        for d in ["/usr/local/sbin", "/usr/local/bin", "/usr/sbin", "/usr/bin", "/sbin", "/bin"]:
            assert d in parts, f"essential dir {d} missing"

    def test_deduplicates(self):
        raw = "/usr/bin:/usr/bin:/usr/local/bin:/usr/local/bin"
        result = build_safe_path(raw, "/home/user")
        parts = result.split(":")
        assert parts.count("/usr/bin") == 1
        assert parts.count("/usr/local/bin") == 1

    def test_preserves_order(self):
        raw = "/opt/cpanel/bin:/usr/local/cpanel/bin:/var/opt/bin:/usr/bin"
        result = build_safe_path(raw, "/home/user")
        parts = result.split(":")
        # Original entries should appear before appended essentials
        assert parts.index("/opt/cpanel/bin") < parts.index("/usr/local/cpanel/bin")
        assert parts.index("/usr/local/cpanel/bin") < parts.index("/var/opt/bin")

    def test_empty_input_gets_essentials(self):
        result = build_safe_path("", "/home/user")
        parts = result.split(":")
        assert len(parts) == 6
        for d in ["/usr/local/sbin", "/usr/local/bin", "/usr/sbin", "/usr/bin", "/sbin", "/bin"]:
            assert d in parts

    def test_sudo_stripped_path_scenario(self):
        """Simulates the bug: sudo resets PATH to minimal set."""
        sudo_path = "/sbin:/bin:/usr/sbin:/usr/bin"
        caller_path = (
            "/root/.local/bin:/root/.dotfiles/bin:"
            "/usr/local/cpanel/bin:/usr/local/cpanel/3rdparty/bin:"
            "/root/.cargo/bin:/usr/local/cpanel/3rdparty/node/22/bin:"
            "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        )
        # With sudo-stripped PATH (the bug)
        bad = build_safe_path(sudo_path, "/root")
        assert "/usr/local/cpanel/bin" not in bad.split(":")

        # With caller's full PATH (the fix)
        good = build_safe_path(caller_path, "/root")
        parts = good.split(":")
        assert "/usr/local/cpanel/bin" in parts
        assert "/usr/local/cpanel/3rdparty/bin" in parts
        assert "/usr/local/cpanel/3rdparty/node/22/bin" in parts
        # Home dirs filtered out
        assert "/root/.local/bin" not in parts
        assert "/root/.cargo/bin" not in parts

    def test_trailing_slash_on_home(self):
        raw = "/root/.local/bin:/usr/bin"
        result = build_safe_path(raw, "/root/")
        assert "/root/.local/bin" not in result.split(":")

    def test_exact_home_dir_filtered(self):
        """Home dir itself (not just subdirs) should be filtered."""
        raw = "/home/user:/usr/bin"
        result = build_safe_path(raw, "/home/user")
        assert "/home/user" not in result.split(":")
        assert "/usr/bin" in result.split(":")


class TestRenderServiceTemplate:
    """Tests for template rendering."""

    def test_render_replaces_placeholders(self, tmp_path):
        template = tmp_path / "koan.service.template"
        template.write_text(textwrap.dedent("""\
            [Service]
            WorkingDirectory=__KOAN_ROOT__/koan
            Environment=PATH=__PATH__
            ExecStart=/usr/bin/script -qefc "__PYTHON__ app/run.py" /dev/null
        """))

        result = render_service_template(
            str(template), "/opt/koan", "/opt/koan/.venv/bin/python3",
            "/usr/local/bin:/usr/bin:/bin"
        )

        assert "WorkingDirectory=/opt/koan/koan" in result
        assert "Environment=PATH=/usr/local/bin:/usr/bin:/bin" in result
        assert 'ExecStart=/usr/bin/script -qefc "/opt/koan/.venv/bin/python3 app/run.py" /dev/null' in result
        assert "__KOAN_ROOT__" not in result
        assert "__PYTHON__" not in result
        assert "__PATH__" not in result

    def test_render_all_templates(self, tmp_path):
        for name in ["koan.service.template", "koan-awake.service.template"]:
            (tmp_path / name).write_text("WorkingDirectory=__KOAN_ROOT__\nExecStart=__PYTHON__\nPATH=__PATH__\n")
        # Non-matching file should be ignored
        (tmp_path / "other.conf").write_text("ignore me")

        result = render_all_templates(
            str(tmp_path), "/opt/koan", "/usr/bin/python3", "/usr/bin:/bin"
        )

        assert "koan.service" in result
        assert "koan-awake.service" in result
        assert len(result) == 2
        for content in result.values():
            assert "__KOAN_ROOT__" not in content
            assert "/opt/koan" in content

    def test_render_all_templates_empty_dir(self, tmp_path):
        result = render_all_templates(
            str(tmp_path), "/opt/koan", "/usr/bin/python3", "/usr/bin"
        )
        assert result == {}
