"""Tests for outbox_scanner — defense against agent data exfiltration."""

import pytest
from app.outbox_scanner import scan_outbox_content, scan_and_log, ScanResult


class TestScanOutboxContent:
    """Tests for the content scanning function."""

    # --- Safe content (should NOT be blocked) ---

    def test_normal_message_passes(self):
        result = scan_outbox_content("Mission completed. Pushed koan/fix-auth branch.")
        assert not result.blocked
        assert result.warnings is None

    def test_koan_message_passes(self):
        result = scan_outbox_content(
            "The agent who guards all doors cannot guard the door to its own room."
        )
        assert not result.blocked

    def test_code_snippet_passes(self):
        result = scan_outbox_content(
            "Fixed the bug in parse_missions():\n"
            "  sections = content.split('## ')\n"
            "  return [s.strip() for s in sections]"
        )
        assert not result.blocked

    def test_branch_name_passes(self):
        result = scan_outbox_content("Branch: koan/security-audit-fixes pushed")
        assert not result.blocked

    def test_empty_content_passes(self):
        result = scan_outbox_content("")
        assert not result.blocked

    def test_whitespace_only_passes(self):
        result = scan_outbox_content("   \n  \n  ")
        assert not result.blocked

    def test_pr_link_passes(self):
        result = scan_outbox_content(
            "PR: https://github.com/sukria/koan/pull/42\n"
            "Branch koan/fix-xyz pushed."
        )
        assert not result.blocked

    def test_git_hash_passes(self):
        """Short git hashes should not trigger hex detection."""
        result = scan_outbox_content("Force-pushed e06588d to koan/security-audit")
        assert not result.blocked

    # --- Secrets (should be BLOCKED) ---

    def test_blocks_telegram_bot_token(self):
        result = scan_outbox_content("Token: 123456789:ABCdefGHIjklMNOpqrsTUVwxyz0123456789")
        assert result.blocked
        assert "Bot token" in result.reason

    def test_blocks_slack_bot_token(self):
        # Use clearly fake token (enough to match regex, not triggering GitHub push protection)
        fake_token = "xoxb-" + "0" * 20 + "-" + "a" * 24
        result = scan_outbox_content(f"Token is {fake_token}")
        assert result.blocked
        assert "Slack bot token" in result.reason

    def test_blocks_slack_user_token(self):
        fake_token = "xoxp-" + "0" * 20 + "-" + "a" * 24
        result = scan_outbox_content(fake_token)
        assert result.blocked
        assert "Slack user token" in result.reason

    def test_blocks_aws_access_key(self):
        result = scan_outbox_content("Key: AKIAIOSFODNN7EXAMPLE")
        assert result.blocked
        assert "AWS" in result.reason

    def test_blocks_github_token(self):
        result = scan_outbox_content("ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij1234")
        assert result.blocked
        assert "GitHub token" in result.reason

    def test_blocks_github_pat(self):
        result = scan_outbox_content("github_pat_ABCDEFGHIJKLMNOPabcdefghij")
        assert result.blocked
        assert "GitHub PAT" in result.reason

    def test_blocks_api_key_assignment(self):
        result = scan_outbox_content("api_key=sk-abcdefghijklmnopqrstuvwxyz123456")
        assert result.blocked
        assert "API key" in result.reason

    def test_blocks_bearer_token(self):
        result = scan_outbox_content("bearer: eyJhbGciOiJIUzI1NiJ9something12345678")
        assert result.blocked
        assert "API key" in result.reason

    def test_blocks_password_assignment(self):
        result = scan_outbox_content("password=mysecretpassword123!")
        assert result.blocked
        assert "Password" in result.reason

    def test_blocks_ssh_private_key(self):
        result = scan_outbox_content(
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEpAIBAAKCAQEA...\n"
            "-----END RSA PRIVATE KEY-----"
        )
        assert result.blocked
        assert "SSH private key" in result.reason

    def test_blocks_jwt_token(self):
        result = scan_outbox_content(
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
            "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIn0."
            "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        )
        assert result.blocked
        assert "JWT" in result.reason

    # --- Environment variable leaks ---

    def test_blocks_telegram_token_variable(self):
        result = scan_outbox_content("KOAN_TELEGRAM_TOKEN=123456:ABCDEFG")
        assert result.blocked
        # May match generic secret pattern or env-specific pattern
        assert "secret" in result.reason.lower() or "token" in result.reason.lower()

    def test_blocks_slack_token_variable(self):
        result = scan_outbox_content("KOAN_SLACK_BOT_TOKEN=xoxb-something")
        assert result.blocked
        # May match generic secret pattern or env-specific pattern
        assert "secret" in result.reason.lower() or "token" in result.reason.lower()

    def test_blocks_database_credential(self):
        result = scan_outbox_content("DATABASE_URL=postgres://user:pass@host/db")
        assert result.blocked
        assert "credential" in result.reason.lower() or "Database" in result.reason

    def test_blocks_env_dump(self):
        """Multiple KEY=VALUE lines suggest .env file dump."""
        content = (
            "HOME=/Users/nicolas\n"
            "PATH=/usr/local/bin:/usr/bin\n"
            "SHELL=/bin/zsh\n"
            "USER=nicolas\n"
        )
        result = scan_outbox_content(content)
        assert result.blocked
        assert "env dump" in result.reason.lower() or "variable" in result.reason.lower()

    def test_single_env_line_warns_only(self):
        """A single KEY=VALUE line is just a warning, not blocked."""
        result = scan_outbox_content("Here's the config:\nKOAN_MAX_RUNS=25")
        assert not result.blocked
        assert result.warnings is not None

    # --- Encoded data exfiltration ---

    def test_blocks_large_base64(self):
        result = scan_outbox_content(
            "Data: " + "A" * 120 + "=="
        )
        assert result.blocked
        assert "base64" in result.reason.lower()

    def test_blocks_large_hex(self):
        result = scan_outbox_content("Hash: " + "a1b2c3d4" * 10)
        assert result.blocked
        assert "hex" in result.reason.lower()

    def test_short_base64_passes(self):
        """Short base64 strings (like git hashes, short tokens) should pass."""
        result = scan_outbox_content("Commit: e06588d merged successfully")
        assert not result.blocked

    # --- File content dumps ---

    def test_warns_on_env_file_dump(self):
        result = scan_outbox_content("Contents of ~/.env:\nsome stuff here")
        assert not result.blocked  # warn, not block
        assert result.warnings is not None
        assert any("sensitive file" in w.lower() for w in result.warnings)

    def test_warns_on_key_file_dump(self):
        result = scan_outbox_content("Content of /home/user/.ssh/id_rsa.pem")
        assert not result.blocked
        assert result.warnings is not None

    # --- Edge cases ---

    def test_none_content(self):
        result = scan_outbox_content(None)
        assert not result.blocked

    def test_very_long_safe_content(self):
        """Long messages that are safe should pass."""
        result = scan_outbox_content("This is a normal message. " * 100)
        assert not result.blocked

    def test_mixed_content_secret_in_message(self):
        """Secret embedded in otherwise normal text should be caught."""
        result = scan_outbox_content(
            "Here's the summary:\n"
            "- Fixed 3 bugs\n"
            "- api_key=sk-proj-1234567890abcdef1234567890\n"
            "- Updated tests"
        )
        assert result.blocked

    def test_scan_result_dataclass(self):
        result = ScanResult(blocked=False)
        assert not result.blocked
        assert result.reason is None
        assert result.warnings is None
        assert result.redacted_content is None


class TestScanAndLog:
    """Tests for the logging wrapper."""

    def test_logs_blocked_to_stderr(self, capsys):
        scan_and_log("api_key=secret_12345678901234567890")
        captured = capsys.readouterr()
        assert "BLOCKED" in captured.err
        assert "API key" in captured.err

    def test_logs_warning_to_stderr(self, capsys):
        scan_and_log("Contents of ~/.env: test")
        captured = capsys.readouterr()
        assert "WARNING" in captured.err

    def test_no_log_for_safe_content(self, capsys):
        scan_and_log("Mission completed successfully.")
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_returns_scan_result(self):
        result = scan_and_log("Normal message")
        assert isinstance(result, ScanResult)
        assert not result.blocked
