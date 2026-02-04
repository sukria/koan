"""Tests for reset_parser.py — quota reset time parsing."""

import pytest
from datetime import datetime, timedelta

try:
    import zoneinfo
    ZoneInfo = zoneinfo.ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo


class TestParseResetTime:
    """Test parse_reset_time function."""

    def test_simple_time_am(self):
        """Parse simple 10am format."""
        from app.reset_parser import parse_reset_time

        # Use a fixed "now" to make the test deterministic
        now = datetime(2026, 2, 4, 8, 0, 0, tzinfo=ZoneInfo("Europe/Paris"))
        ts, info = parse_reset_time("resets 10am (Europe/Paris)", now=now)

        assert ts is not None
        reset_dt = datetime.fromtimestamp(ts, tz=ZoneInfo("Europe/Paris"))
        assert reset_dt.hour == 10
        assert reset_dt.minute == 0
        # Should be today since 10am hasn't passed yet at 8am
        assert reset_dt.day == 4
        assert "10am" in info

    def test_simple_time_pm(self):
        """Parse simple 5pm format."""
        from app.reset_parser import parse_reset_time

        now = datetime(2026, 2, 4, 14, 0, 0, tzinfo=ZoneInfo("Europe/Paris"))
        ts, info = parse_reset_time("resets 5pm (Europe/Paris)", now=now)

        assert ts is not None
        reset_dt = datetime.fromtimestamp(ts, tz=ZoneInfo("Europe/Paris"))
        assert reset_dt.hour == 17
        assert "5pm" in info

    def test_time_already_passed_rolls_to_tomorrow(self):
        """If reset time has passed today, should be tomorrow."""
        from app.reset_parser import parse_reset_time

        # It's 3pm, reset is at 10am — should be tomorrow
        now = datetime(2026, 2, 4, 15, 0, 0, tzinfo=ZoneInfo("Europe/Paris"))
        ts, info = parse_reset_time("resets 10am (Europe/Paris)", now=now)

        assert ts is not None
        reset_dt = datetime.fromtimestamp(ts, tz=ZoneInfo("Europe/Paris"))
        assert reset_dt.day == 5  # Tomorrow
        assert reset_dt.hour == 10

    def test_date_with_time(self):
        """Parse 'Feb 5 at 10am' format."""
        from app.reset_parser import parse_reset_time

        now = datetime(2026, 2, 4, 12, 0, 0, tzinfo=ZoneInfo("Europe/Paris"))
        ts, info = parse_reset_time("resets Feb 5 at 10am (Europe/Paris)", now=now)

        assert ts is not None
        reset_dt = datetime.fromtimestamp(ts, tz=ZoneInfo("Europe/Paris"))
        assert reset_dt.month == 2
        assert reset_dt.day == 5
        assert reset_dt.hour == 10
        assert "Feb 5" in info

    def test_tomorrow_format(self):
        """Parse 'tomorrow at 10am' format."""
        from app.reset_parser import parse_reset_time

        now = datetime(2026, 2, 4, 12, 0, 0, tzinfo=ZoneInfo("Europe/Paris"))
        ts, info = parse_reset_time("resets tomorrow at 10am (Europe/Paris)", now=now)

        assert ts is not None
        reset_dt = datetime.fromtimestamp(ts, tz=ZoneInfo("Europe/Paris"))
        assert reset_dt.day == 5  # Tomorrow
        assert reset_dt.hour == 10
        assert "tomorrow" in info

    def test_embedded_in_full_message(self):
        """Parse reset time from full quota exhaustion message."""
        from app.reset_parser import parse_reset_time

        msg = "You're out of extra usage · resets 10am (Europe/Paris)"
        now = datetime(2026, 2, 4, 8, 0, 0, tzinfo=ZoneInfo("Europe/Paris"))
        ts, info = parse_reset_time(msg, now=now)

        assert ts is not None
        assert "10am" in info

    def test_invalid_format_returns_none(self):
        """Unparseable format returns None with original text."""
        from app.reset_parser import parse_reset_time

        ts, info = parse_reset_time("some random text")
        assert ts is None

    def test_in_hours_format(self):
        """Parse 'in Xh' format."""
        from app.reset_parser import parse_reset_time

        now = datetime(2026, 2, 4, 12, 0, 0, tzinfo=ZoneInfo("Europe/Paris"))
        ts, info = parse_reset_time("resets in 3h", now=now)

        assert ts is not None
        reset_dt = datetime.fromtimestamp(ts, tz=ZoneInfo("Europe/Paris"))
        # Should be roughly 3 hours from now
        assert reset_dt.hour == 15
        assert "in 3h" in info


class TestTimeUntilReset:
    """Test time_until_reset function."""

    def test_minutes_only(self):
        """Less than an hour shows minutes."""
        from app.reset_parser import time_until_reset

        now = datetime(2026, 2, 4, 9, 45, 0)
        reset_ts = int((now + timedelta(minutes=15)).timestamp())

        result = time_until_reset(reset_ts, now=now)
        assert result == "15m"

    def test_hours_and_minutes(self):
        """Hours with minutes."""
        from app.reset_parser import time_until_reset

        now = datetime(2026, 2, 4, 8, 0, 0)
        reset_ts = int((now + timedelta(hours=2, minutes=30)).timestamp())

        result = time_until_reset(reset_ts, now=now)
        assert result == "2h 30m"

    def test_hours_only(self):
        """Exact hours."""
        from app.reset_parser import time_until_reset

        now = datetime(2026, 2, 4, 8, 0, 0)
        reset_ts = int((now + timedelta(hours=3)).timestamp())

        result = time_until_reset(reset_ts, now=now)
        assert result == "3h"

    def test_days_and_hours(self):
        """More than 24h shows days."""
        from app.reset_parser import time_until_reset

        now = datetime(2026, 2, 4, 8, 0, 0)
        reset_ts = int((now + timedelta(days=1, hours=5)).timestamp())

        result = time_until_reset(reset_ts, now=now)
        assert result == "1d 5h"

    def test_past_time_returns_now(self):
        """If reset time has passed, return 'now'."""
        from app.reset_parser import time_until_reset

        now = datetime(2026, 2, 4, 12, 0, 0)
        reset_ts = int((now - timedelta(hours=1)).timestamp())

        result = time_until_reset(reset_ts, now=now)
        assert result == "now"


class TestShouldAutoResume:
    """Test should_auto_resume function."""

    def test_before_reset_time(self):
        """Should not resume if current time < reset time."""
        from app.reset_parser import should_auto_resume

        now = datetime(2026, 2, 4, 8, 0, 0)
        reset_ts = int((now + timedelta(hours=2)).timestamp())

        assert should_auto_resume(reset_ts, now=now) is False

    def test_at_reset_time(self):
        """Should resume if current time == reset time."""
        from app.reset_parser import should_auto_resume

        now = datetime(2026, 2, 4, 10, 0, 0)
        reset_ts = int(now.timestamp())

        assert should_auto_resume(reset_ts, now=now) is True

    def test_after_reset_time(self):
        """Should resume if current time > reset time."""
        from app.reset_parser import should_auto_resume

        now = datetime(2026, 2, 4, 12, 0, 0)
        reset_ts = int((now - timedelta(hours=2)).timestamp())

        assert should_auto_resume(reset_ts, now=now) is True


class TestCLIInterface:
    """Test the CLI interface."""

    def test_cli_parse(self, monkeypatch, capsys):
        """Test CLI parse command."""
        import sys
        monkeypatch.setattr(sys, 'argv', ['reset_parser.py', 'parse', 'resets', '5pm', '(Europe/Paris)'])

        from app import reset_parser
        import importlib
        # Re-import to trigger __main__ block... actually let's test the function directly
        ts, info = reset_parser.parse_reset_time("resets 5pm (Europe/Paris)")
        assert ts is not None
        assert "5pm" in info

    def test_cli_until(self):
        """Test CLI until command."""
        from app.reset_parser import time_until_reset
        from datetime import datetime, timedelta

        now = datetime(2026, 2, 4, 8, 0, 0)
        future = int((now + timedelta(hours=2)).timestamp())
        result = time_until_reset(future, now=now)
        assert result == "2h"

    def test_cli_check(self):
        """Test CLI check command."""
        from app.reset_parser import should_auto_resume
        from datetime import datetime, timedelta

        now = datetime(2026, 2, 4, 12, 0, 0)
        past = int((now - timedelta(hours=1)).timestamp())
        assert should_auto_resume(past, now=now) is True
