"""Tests for log_rotation module."""

import gzip
import os
import threading
import time

import pytest

from app.log_rotation import (
    DEFAULT_COMPRESS,
    DEFAULT_MAX_BACKUPS,
    DEFAULT_MAX_SIZE_MB,
    cleanup_old_backups,
    get_log_config,
    rotate_log,
    _backup_path,
    _compress_file,
    _remove_backup,
    _rename_backup,
)


class TestGetLogConfig:
    def test_defaults_when_no_config(self):
        cfg = get_log_config()
        assert cfg["max_backups"] == DEFAULT_MAX_BACKUPS
        assert cfg["max_size_bytes"] == DEFAULT_MAX_SIZE_MB * 1024 * 1024
        assert cfg["compress"] is True

    def test_defaults_when_empty_config(self):
        cfg = get_log_config({})
        assert cfg["max_backups"] == DEFAULT_MAX_BACKUPS

    def test_defaults_when_no_logs_key(self):
        cfg = get_log_config({"other": "stuff"})
        assert cfg["max_backups"] == DEFAULT_MAX_BACKUPS

    def test_custom_values(self):
        cfg = get_log_config({"logs": {"max_backups": 5, "max_size_mb": 100, "compress": False}})
        assert cfg["max_backups"] == 5
        assert cfg["max_size_bytes"] == 100 * 1024 * 1024
        assert cfg["compress"] is False

    def test_partial_override(self):
        cfg = get_log_config({"logs": {"max_backups": 7}})
        assert cfg["max_backups"] == 7
        assert cfg["max_size_bytes"] == DEFAULT_MAX_SIZE_MB * 1024 * 1024
        assert cfg["compress"] is DEFAULT_COMPRESS

    def test_none_logs_section(self):
        cfg = get_log_config({"logs": None})
        assert cfg["max_backups"] == DEFAULT_MAX_BACKUPS

    def test_negative_values_clamped(self):
        cfg = get_log_config({"logs": {"max_backups": -5, "max_size_mb": -10}})
        assert cfg["max_backups"] == 1  # Clamped to minimum
        assert cfg["max_size_bytes"] == 1 * 1024 * 1024  # Clamped to 1MB

    def test_zero_values_clamped(self):
        cfg = get_log_config({"logs": {"max_backups": 0, "max_size_mb": 0}})
        assert cfg["max_backups"] == 1
        assert cfg["max_size_bytes"] == 1 * 1024 * 1024

    def test_huge_values_clamped(self):
        cfg = get_log_config({"logs": {"max_backups": 9999, "max_size_mb": 99999}})
        assert cfg["max_backups"] == 100  # Clamped to reasonable max
        assert cfg["max_size_bytes"] == 10240 * 1024 * 1024  # Clamped to 10GB


class TestRotateLog:
    def test_noop_when_file_missing(self, tmp_path):
        log_path = tmp_path / "run.log"
        rotate_log(log_path)
        assert not log_path.exists()

    def test_noop_when_file_empty(self, tmp_path):
        log_path = tmp_path / "run.log"
        log_path.write_text("")
        rotate_log(log_path)
        # File still exists (not moved), no backup created
        assert log_path.exists()
        assert not (tmp_path / "run.log.1").exists()

    def test_basic_rotation(self, tmp_path):
        log_path = tmp_path / "run.log"
        log_path.write_text("session 1 content\n")

        rotate_log(log_path, max_backups=3, compress=False)

        assert not log_path.exists()
        backup_1 = tmp_path / "run.log.1"
        assert backup_1.exists()
        assert backup_1.read_text() == "session 1 content\n"

    def test_shifts_existing_backups(self, tmp_path):
        log_path = tmp_path / "run.log"
        # Create existing backups
        (tmp_path / "run.log.1").write_text("backup 1\n")
        (tmp_path / "run.log.2").write_text("backup 2\n")
        log_path.write_text("current\n")

        rotate_log(log_path, max_backups=3, compress=False)

        assert (tmp_path / "run.log.1").read_text() == "current\n"
        assert (tmp_path / "run.log.2").read_text() == "backup 1\n"
        assert (tmp_path / "run.log.3").read_text() == "backup 2\n"

    def test_deletes_oldest_beyond_max(self, tmp_path):
        log_path = tmp_path / "run.log"
        (tmp_path / "run.log.1").write_text("old 1\n")
        (tmp_path / "run.log.2").write_text("old 2\n")
        (tmp_path / "run.log.3").write_text("oldest\n")
        log_path.write_text("current\n")

        rotate_log(log_path, max_backups=3, compress=False)

        assert (tmp_path / "run.log.1").read_text() == "current\n"
        assert (tmp_path / "run.log.2").read_text() == "old 1\n"
        assert (tmp_path / "run.log.3").read_text() == "old 2\n"
        # "oldest" was at .3, shifted to .4 which exceeds max_backups=3, so deleted

    def test_compress_older_backups(self, tmp_path):
        log_path = tmp_path / "run.log"
        (tmp_path / "run.log.1").write_text("prev session\n")
        log_path.write_text("current session\n")

        rotate_log(log_path, max_backups=3, compress=True)

        # .log.1 = uncompressed (current → backup 1)
        assert (tmp_path / "run.log.1").exists()
        assert (tmp_path / "run.log.1").read_text() == "current session\n"
        # .log.2 = compressed (old backup 1 → backup 2)
        gz_path = tmp_path / "run.log.2.gz"
        assert gz_path.exists()
        with gzip.open(gz_path, "rb") as f:
            assert f.read() == b"prev session\n"

    def test_handles_already_compressed_backups(self, tmp_path):
        log_path = tmp_path / "run.log"
        # Pre-existing compressed backup
        gz_content = b"ancient content\n"
        with gzip.open(tmp_path / "run.log.2.gz", "wb") as f:
            f.write(gz_content)
        (tmp_path / "run.log.1").write_text("recent\n")
        log_path.write_text("current\n")

        rotate_log(log_path, max_backups=3, compress=True)

        assert (tmp_path / "run.log.1").read_text() == "current\n"
        # .log.2 should have "recent" content (compressed)
        assert (tmp_path / "run.log.2.gz").exists() or (tmp_path / "run.log.2").exists()
        # .log.3 should have "ancient content" (compressed)
        gz3 = tmp_path / "run.log.3.gz"
        assert gz3.exists()
        with gzip.open(gz3, "rb") as f:
            assert f.read() == gz_content

    def test_max_backups_1(self, tmp_path):
        log_path = tmp_path / "run.log"
        log_path.write_text("content\n")

        rotate_log(log_path, max_backups=1, compress=False)

        assert (tmp_path / "run.log.1").read_text() == "content\n"
        assert not log_path.exists()

    def test_max_backups_1_replaces_old(self, tmp_path):
        log_path = tmp_path / "run.log"
        (tmp_path / "run.log.1").write_text("old\n")
        log_path.write_text("new\n")

        rotate_log(log_path, max_backups=1, compress=False)

        assert (tmp_path / "run.log.1").read_text() == "new\n"

    def test_different_process_names(self, tmp_path):
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        for name in ("run", "awake", "ollama"):
            log_path = logs_dir / f"{name}.log"
            log_path.write_text(f"{name} content\n")
            rotate_log(log_path, max_backups=2, compress=False)
            assert (logs_dir / f"{name}.log.1").read_text() == f"{name} content\n"

    def test_symlink_not_rotated(self, tmp_path):
        """Security test: symlinks should not be followed."""
        real_log = tmp_path / "real.log"
        real_log.write_text("sensitive data\n")
        
        symlink = tmp_path / "run.log"
        symlink.symlink_to(real_log)
        
        # Rotate should no-op on symlink
        rotate_log(symlink, max_backups=3, compress=False)
        
        # Real file should be unchanged
        assert real_log.read_text() == "sensitive data\n"
        assert not (tmp_path / "run.log.1").exists()

    def test_permission_denied_graceful(self, tmp_path):
        """Test graceful handling of permission errors."""
        log_path = tmp_path / "run.log"
        log_path.write_text("content\n")
        
        # Make directory read-only
        os.chmod(tmp_path, 0o444)
        
        try:
            # Should not raise, just silently skip
            rotate_log(log_path, max_backups=3, compress=False)
        finally:
            # Restore permissions for cleanup
            os.chmod(tmp_path, 0o755)

    def test_concurrent_rotation_safe(self, tmp_path):
        """Test that concurrent rotations don't corrupt files."""
        log_path = tmp_path / "run.log"
        log_path.write_text("initial content\n")
        
        results = []
        errors = []
        
        def rotate_worker():
            try:
                rotate_log(log_path, max_backups=3, compress=False)
                results.append("done")
            except Exception as e:
                errors.append(e)
        
        # Launch multiple concurrent rotations
        threads = [threading.Thread(target=rotate_worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        # Should not crash
        assert not errors
        
        # At least one rotation should succeed
        # (others may skip due to lock contention)
        assert len(results) >= 1


class TestBackupPath:
    def test_plain_file(self, tmp_path):
        log = tmp_path / "run.log"
        # No files exist — returns plain path
        path = _backup_path(log, 1)
        assert str(path).endswith("run.log.1")

    def test_prefers_gz(self, tmp_path):
        log = tmp_path / "run.log"
        plain = tmp_path / "run.log.2"
        gz = tmp_path / "run.log.2.gz"
        plain.write_text("plain")
        gz.write_bytes(b"compressed")
        path = _backup_path(log, 2)
        assert str(path).endswith(".gz")


class TestRemoveBackup:
    def test_removes_plain(self, tmp_path):
        f = tmp_path / "run.log.1"
        f.write_text("data")
        _remove_backup(f)
        assert not f.exists()

    def test_removes_gz(self, tmp_path):
        f = tmp_path / "run.log.2.gz"
        f.write_bytes(b"data")
        _remove_backup(f)
        assert not f.exists()

    def test_removes_both_forms(self, tmp_path):
        plain = tmp_path / "run.log.3"
        gz = tmp_path / "run.log.3.gz"
        plain.write_text("a")
        gz.write_bytes(b"b")
        _remove_backup(plain)
        assert not plain.exists()
        assert not gz.exists()

    def test_noop_when_missing(self, tmp_path):
        _remove_backup(tmp_path / "nonexistent.log.1")


class TestRenameBackup:
    def test_rename_plain(self, tmp_path):
        src = tmp_path / "run.log.1"
        dst = tmp_path / "run.log.2"
        src.write_text("data")
        _rename_backup(src, dst)
        assert not src.exists()
        assert dst.read_text() == "data"

    def test_rename_gz(self, tmp_path):
        src = tmp_path / "run.log.1.gz"
        dst = tmp_path / "run.log.2"
        src.write_bytes(b"compressed")
        _rename_backup(src, dst)
        assert not src.exists()
        # gz file should remain gz
        assert (tmp_path / "run.log.2.gz").exists()

    def test_noop_when_src_missing(self, tmp_path):
        _rename_backup(tmp_path / "nope", tmp_path / "dest")


class TestCompressFile:
    def test_compresses_and_removes_original(self, tmp_path):
        f = tmp_path / "test.log.2"
        f.write_text("hello world\n")
        _compress_file(f)
        assert not f.exists()
        gz = tmp_path / "test.log.2.gz"
        assert gz.exists()
        with gzip.open(gz, "rb") as g:
            assert g.read() == b"hello world\n"

    def test_preserves_original_on_failure(self, tmp_path):
        f = tmp_path / "test.log"
        f.write_text("data")
        # Make gz path a directory to cause failure
        gz_dir = tmp_path / "test.log.gz"
        gz_dir.mkdir()
        _compress_file(f)
        # Original should still exist
        assert f.exists()

    def test_symlink_not_compressed(self, tmp_path):
        """Security test: symlinks should not be compressed."""
        real_file = tmp_path / "real.log"
        real_file.write_text("real content\n")
        
        symlink = tmp_path / "test.log.2"
        symlink.symlink_to(real_file)
        
        # Should not compress symlink
        _compress_file(symlink)
        
        # Original file should be unchanged
        assert real_file.read_text() == "real content\n"
        assert not (tmp_path / "test.log.2.gz").exists()

    def test_atomic_compression(self, tmp_path):
        """Test that compression is atomic (temp file, then rename)."""
        f = tmp_path / "test.log.2"
        f.write_text("content\n" * 1000)
        
        # Compress in thread while checking for partial files
        import threading
        
        def compress():
            _compress_file(f)
        
        thread = threading.Thread(target=compress)
        thread.start()
        
        # Check for temp files during compression
        temp_files_seen = []
        for _ in range(10):
            time.sleep(0.01)
            for item in tmp_path.iterdir():
                if ".log_compress_" in item.name:
                    temp_files_seen.append(item.name)
        
        thread.join()
        
        # Compression should succeed
        assert (tmp_path / "test.log.2.gz").exists()
        assert not f.exists()
        
        # No temp files left behind
        for item in tmp_path.iterdir():
            assert ".log_compress_" not in item.name


class TestCleanupOldBackups:
    def test_removes_excess_backups(self, tmp_path):
        log_dir = tmp_path
        for i in range(1, 8):
            (log_dir / f"run.log.{i}").write_text(f"backup {i}")
        cleanup_old_backups(log_dir, "run", max_backups=3)
        assert (log_dir / "run.log.1").exists()
        assert (log_dir / "run.log.3").exists()
        assert not (log_dir / "run.log.4").exists()
        assert not (log_dir / "run.log.7").exists()

    def test_noop_when_no_excess(self, tmp_path):
        (tmp_path / "run.log.1").write_text("a")
        (tmp_path / "run.log.2").write_text("b")
        cleanup_old_backups(tmp_path, "run", max_backups=3)
        assert (tmp_path / "run.log.1").exists()
        assert (tmp_path / "run.log.2").exists()

    def test_removes_compressed_backups(self, tmp_path):
        """Test that cleanup removes both plain and compressed forms."""
        for i in range(1, 8):
            if i > 3:
                # Old backups are compressed
                with gzip.open(tmp_path / f"run.log.{i}.gz", "wb") as f:
                    f.write(f"backup {i}".encode())
            else:
                (tmp_path / f"run.log.{i}").write_text(f"backup {i}")
        
        cleanup_old_backups(tmp_path, "run", max_backups=3)
        
        assert (tmp_path / "run.log.3").exists()
        assert not (tmp_path / "run.log.4.gz").exists()
        assert not (tmp_path / "run.log.7.gz").exists()


class TestIntegrationOpenLogFileRotation:
    """Test that _open_log_file in pid_manager triggers rotation."""

    def test_open_log_file_rotates(self, tmp_path, monkeypatch):
        from app.pid_manager import _open_log_file

        monkeypatch.setattr("app.pid_manager.load_config", lambda: {}, raising=False)

        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        log_file = log_dir / "run.log"
        log_file.write_text("previous session output\n")

        fh = _open_log_file(tmp_path, "run")
        fh.write("new session\n")
        fh.close()

        # Current log has new content
        assert log_file.read_text() == "new session\n"
        # Previous content backed up
        backup = log_dir / "run.log.1"
        assert backup.exists()
        assert backup.read_text() == "previous session output\n"

    def test_open_log_file_no_rotation_when_empty(self, tmp_path, monkeypatch):
        from app.pid_manager import _open_log_file

        monkeypatch.setattr("app.pid_manager.load_config", lambda: {}, raising=False)

        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        log_file = log_dir / "run.log"
        log_file.write_text("")

        fh = _open_log_file(tmp_path, "run")
        fh.close()

        assert not (log_dir / "run.log.1").exists()
