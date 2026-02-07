"""Tests for the /chat skill handler."""

from unittest.mock import MagicMock

from app.skills import SkillContext


def _make_ctx(args="", handle_chat=None):
    """Create a minimal SkillContext for testing."""
    ctx = MagicMock(spec=SkillContext)
    ctx.command_name = "chat"
    ctx.args = args
    ctx.handle_chat = handle_chat
    return ctx


class TestChatSkill:
    """Tests for /chat command."""

    def test_no_args_returns_usage(self):
        from skills.core.chat.handler import handle

        ctx = _make_ctx("")
        result = handle(ctx)

        assert "Usage:" in result
        assert "/chat" in result

    def test_whitespace_only_calls_handler(self):
        from skills.core.chat.handler import handle

        # Whitespace-only args are truthy, so they get passed to handle_chat
        mock_chat = MagicMock()
        ctx = _make_ctx("   ", handle_chat=mock_chat)
        result = handle(ctx)

        mock_chat.assert_called_once_with("   ")
        assert result == ""

    def test_calls_handle_chat_with_args(self):
        from skills.core.chat.handler import handle

        mock_chat = MagicMock()
        ctx = _make_ctx("hello world", handle_chat=mock_chat)
        result = handle(ctx)

        mock_chat.assert_called_once_with("hello world")
        assert result == ""

    def test_returns_empty_string_on_success(self):
        from skills.core.chat.handler import handle

        mock_chat = MagicMock()
        ctx = _make_ctx("test message", handle_chat=mock_chat)
        result = handle(ctx)

        assert result == ""

    def test_no_handler_returns_warning(self):
        from skills.core.chat.handler import handle

        ctx = _make_ctx("test message", handle_chat=None)
        result = handle(ctx)

        assert "⚠️" in result or "not available" in result.lower()

    def test_preserves_message_content(self):
        from skills.core.chat.handler import handle

        mock_chat = MagicMock()
        message = "fix the login bug for user authentication"
        ctx = _make_ctx(message, handle_chat=mock_chat)
        handle(ctx)

        mock_chat.assert_called_once_with(message)

    def test_handles_multiline_messages(self):
        from skills.core.chat.handler import handle

        mock_chat = MagicMock()
        message = "line one\nline two\nline three"
        ctx = _make_ctx(message, handle_chat=mock_chat)
        result = handle(ctx)

        mock_chat.assert_called_once_with(message)
        assert result == ""

    def test_handles_special_characters(self):
        from skills.core.chat.handler import handle

        mock_chat = MagicMock()
        message = "test with émojis 🎉 and spëcial chars"
        ctx = _make_ctx(message, handle_chat=mock_chat)
        result = handle(ctx)

        mock_chat.assert_called_once_with(message)
        assert result == ""
