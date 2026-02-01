"""Tests for health_check.py — bridge heartbeat monitoring."""

import time
from unittest.mock import patch

import pytest

from health_check import write_heartbeat, check_heartbeat, check_and_alert


class TestWriteHeartbeat:

    def test_creates_file(self, tmp_path):
        write_heartbeat(str(tmp_path))
        hb = tmp_path / ".koan-heartbeat"
        assert hb.exists()
        ts = float(hb.read_text().strip())
        assert abs(ts - time.time()) < 2

    def test_overwrites(self, tmp_path):
        write_heartbeat(str(tmp_path))
        first = float((tmp_path / ".koan-heartbeat").read_text())
        time.sleep(0.01)
        write_heartbeat(str(tmp_path))
        second = float((tmp_path / ".koan-heartbeat").read_text())
        assert second >= first


class TestCheckHeartbeat:

    def test_no_file_is_healthy(self, tmp_path):
        assert check_heartbeat(str(tmp_path)) is True

    def test_fresh_heartbeat(self, tmp_path):
        write_heartbeat(str(tmp_path))
        assert check_heartbeat(str(tmp_path), max_age=60) is True

    def test_stale_heartbeat(self, tmp_path):
        hb = tmp_path / ".koan-heartbeat"
        hb.write_text(str(time.time() - 120))
        assert check_heartbeat(str(tmp_path), max_age=60) is False

    def test_corrupt_file(self, tmp_path):
        hb = tmp_path / ".koan-heartbeat"
        hb.write_text("not a number")
        assert check_heartbeat(str(tmp_path)) is False

    def test_exact_boundary(self, tmp_path):
        hb = tmp_path / ".koan-heartbeat"
        hb.write_text(str(time.time() - 59))
        assert check_heartbeat(str(tmp_path), max_age=60) is True


class TestCheckAndAlert:

    @patch("health_check.send_telegram")
    def test_healthy_no_alert(self, mock_send, tmp_path):
        write_heartbeat(str(tmp_path))
        result = check_and_alert(str(tmp_path))
        assert result is True
        mock_send.assert_not_called()

    @patch("health_check.send_telegram")
    def test_stale_sends_alert(self, mock_send, tmp_path):
        hb = tmp_path / ".koan-heartbeat"
        hb.write_text(str(time.time() - 300))
        result = check_and_alert(str(tmp_path), max_age=60)
        assert result is False
        mock_send.assert_called_once()
        assert "down" in mock_send.call_args[0][0]

    @patch("health_check.send_telegram")
    def test_corrupt_sends_alert(self, mock_send, tmp_path):
        hb = tmp_path / ".koan-heartbeat"
        hb.write_text("garbage")
        result = check_and_alert(str(tmp_path), max_age=60)
        assert result is False
        mock_send.assert_called_once()
        assert "unreadable" in mock_send.call_args[0][0]

    @patch("health_check.send_telegram")
    def test_no_file_no_alert(self, mock_send, tmp_path):
        result = check_and_alert(str(tmp_path))
        assert result is True
        mock_send.assert_not_called()
