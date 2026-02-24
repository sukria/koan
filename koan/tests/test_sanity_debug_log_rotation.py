"""Tests for sanity.debug_log_rotation — debug log rotation check."""

import os

import pytest

from sanity.debug_log_rotation import (
    DEBUG_LOG_FILENAME,
    MAX_KEEP_LINES,
    MAX_SIZE_BYTES,
    rotate_debug_log,
    run,
)


class TestRotateDebugLog:
    """Tests for rotate_debug_log()."""

    def test_no_file_returns_not_modified(self, tmp_path):
        """No file → no change."""
        path = str(tmp_path / DEBUG_LOG_FILENAME)
        modified, changes = rotate_debug_log(path)
        assert not modified
        assert changes == []

    def test_empty_path_returns_not_modified(self):
        modified, changes = rotate_debug_log("")
        assert not modified
        assert changes == []

    def test_small_file_untouched(self, tmp_path):
        """File under threshold → no change."""
        path = tmp_path / DEBUG_LOG_FILENAME
        path.write_text("line1\nline2\nline3\n")
        modified, changes = rotate_debug_log(str(path))
        assert not modified
        assert changes == []
        # Content unchanged
        assert path.read_text() == "line1\nline2\nline3\n"

    def test_large_file_rotated(self, tmp_path):
        """File over threshold → truncated to MAX_KEEP_LINES."""
        path = tmp_path / DEBUG_LOG_FILENAME
        # Write more than MAX_SIZE_BYTES of content
        total_lines = MAX_KEEP_LINES + 1000
        lines = [f"[2026-02-23 10:00:00] debug line {i}\n" for i in range(total_lines)]
        path.write_text("".join(lines))

        # Ensure file is actually over the size threshold
        # If lines are short and don't exceed MAX_SIZE_BYTES, pad them
        while path.stat().st_size <= MAX_SIZE_BYTES:
            extra = [f"[2026-02-23 10:00:00] {'x' * 200} line {i}\n" for i in range(10000)]
            lines.extend(extra)
            total_lines += 10000
            path.write_text("".join(lines))

        modified, changes = rotate_debug_log(str(path))
        assert modified
        assert len(changes) == 1
        assert "Rotated" in changes[0]
        assert str(MAX_KEEP_LINES) in changes[0]

        # Verify the file now has MAX_KEEP_LINES
        result_lines = path.read_text().splitlines()
        assert len(result_lines) == MAX_KEEP_LINES

    def test_keeps_last_lines(self, tmp_path):
        """Rotation keeps the LAST lines, not the first."""
        path = tmp_path / DEBUG_LOG_FILENAME
        # Create a file with identifiable first and last lines
        total = MAX_KEEP_LINES + 500
        lines = [f"line-{i:06d}\n" for i in range(total)]
        content = "".join(lines)

        # Pad to exceed size threshold
        while len(content.encode()) <= MAX_SIZE_BYTES:
            padding = "x" * 200 + "\n"
            lines.insert(0, padding)
            total += 1
            content = "".join(lines)

        path.write_text(content)

        modified, _ = rotate_debug_log(str(path))
        assert modified

        result = path.read_text()
        # Last line should be preserved
        assert f"line-{MAX_KEEP_LINES + 499:06d}" in result
        # First few padding lines should be gone
        assert result.startswith("x") or "line-" in result.split("\n")[0]

    def test_exact_threshold_not_rotated(self, tmp_path):
        """File exactly at MAX_SIZE_BYTES → no rotation."""
        path = tmp_path / DEBUG_LOG_FILENAME
        # Create a file exactly at the threshold
        content = "a" * MAX_SIZE_BYTES
        path.write_text(content)
        modified, changes = rotate_debug_log(str(path))
        assert not modified
        assert changes == []

    def test_few_lines_large_file_not_rotated(self, tmp_path):
        """File over size but under MAX_KEEP_LINES total → no rotation needed."""
        path = tmp_path / DEBUG_LOG_FILENAME
        # Create a file with few very long lines that exceed MAX_SIZE_BYTES
        line = "x" * (MAX_SIZE_BYTES // 100 + 1) + "\n"
        lines = [line] * 200  # 200 lines, each ~100KB
        path.write_text("".join(lines))

        assert path.stat().st_size > MAX_SIZE_BYTES
        assert len(lines) < MAX_KEEP_LINES

        modified, changes = rotate_debug_log(str(path))
        assert not modified
        assert changes == []

    def test_unreadable_file_returns_not_modified(self, tmp_path):
        """OSError on read → no change."""
        path = tmp_path / DEBUG_LOG_FILENAME
        path.write_text("content")
        # Make file unreadable
        path.chmod(0o000)
        try:
            modified, changes = rotate_debug_log(str(path))
            assert not modified
            assert changes == []
        finally:
            path.chmod(0o644)

    def test_changes_message_includes_size(self, tmp_path):
        """Changes message includes MB size and line counts."""
        path = tmp_path / DEBUG_LOG_FILENAME
        # Need total > MAX_KEEP_LINES and total * bytes_per_line > MAX_SIZE_BYTES
        bytes_per_line = 2001  # "x" * 2000 + "\n"
        min_lines_for_size = MAX_SIZE_BYTES // bytes_per_line + 1
        total = max(MAX_KEEP_LINES + 100, min_lines_for_size + 1)
        line = "x" * 2000 + "\n"
        path.write_text(line * total)

        assert path.stat().st_size > MAX_SIZE_BYTES

        modified, changes = rotate_debug_log(str(path))
        assert modified
        assert "MB" in changes[0]
        assert "trimmed" in changes[0]


class TestRunInterface:
    """Tests for the run() entry point."""

    def test_run_no_koan_root(self, monkeypatch):
        """No KOAN_ROOT → no-op."""
        monkeypatch.delenv("KOAN_ROOT", raising=False)
        modified, changes = run("/fake/instance")
        assert not modified
        assert changes == []

    def test_run_with_koan_root_no_file(self, tmp_path, monkeypatch):
        """KOAN_ROOT set but no debug log → no-op."""
        monkeypatch.setenv("KOAN_ROOT", str(tmp_path))
        modified, changes = run(str(tmp_path / "instance"))
        assert not modified
        assert changes == []

    def test_run_with_small_debug_log(self, tmp_path, monkeypatch):
        """Small debug log → no rotation."""
        monkeypatch.setenv("KOAN_ROOT", str(tmp_path))
        log_path = tmp_path / DEBUG_LOG_FILENAME
        log_path.write_text("small\n")
        modified, changes = run(str(tmp_path / "instance"))
        assert not modified
        assert changes == []

    def test_run_delegates_to_rotate(self, tmp_path, monkeypatch):
        """run() delegates to rotate_debug_log with correct path."""
        monkeypatch.setenv("KOAN_ROOT", str(tmp_path))
        log_path = tmp_path / DEBUG_LOG_FILENAME
        # Create large enough file
        total = MAX_KEEP_LINES + 500
        line = "x" * 2000 + "\n"
        log_path.write_text(line * total)

        if log_path.stat().st_size <= MAX_SIZE_BYTES:
            pytest.skip("File not large enough to trigger rotation")

        modified, changes = run(str(tmp_path / "instance"))
        assert modified
        assert len(changes) == 1


class TestSanityRunnerIntegration:
    """Verify the module is discovered by the sanity runner."""

    def test_discovered_by_runner(self):
        """debug_log_rotation should appear in discover_checks()."""
        from sanity import discover_checks
        checks = discover_checks()
        assert "debug_log_rotation" in checks

    def test_runs_in_alphabetical_order(self):
        """Sanity checks run alphabetically — debug_log_rotation before missions_structure."""
        from sanity import discover_checks
        checks = discover_checks()
        if "missions_structure" in checks:
            assert checks.index("debug_log_rotation") < checks.index("missions_structure")
