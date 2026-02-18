"""Tests for text_utils — shared text processing for messaging delivery."""

import pytest
from app.text_utils import strip_markdown, clean_cli_response, DEFAULT_MAX_LENGTH


class TestStripMarkdown:
    """Tests for strip_markdown()."""

    def test_plain_text_unchanged(self):
        assert strip_markdown("Hello world") == "Hello world"

    def test_empty_string(self):
        assert strip_markdown("") == ""

    def test_strips_bold(self):
        assert strip_markdown("This is **bold** text") == "This is bold text"

    def test_strips_underline(self):
        assert strip_markdown("This is __underlined__") == "This is underlined"

    def test_strips_strikethrough(self):
        assert strip_markdown("This is ~~deleted~~") == "This is deleted"

    def test_strips_code_fences(self):
        text = "```python\nprint('hello')\n```"
        assert strip_markdown(text) == "python\nprint('hello')\n"

    def test_strips_inline_code_fence(self):
        assert strip_markdown("Use ```code``` here") == "Use code here"

    def test_strips_heading_h1(self):
        assert strip_markdown("# Title") == "Title"

    def test_strips_heading_h2(self):
        assert strip_markdown("## Section") == "Section"

    def test_strips_heading_h3(self):
        assert strip_markdown("### Subsection") == "Subsection"

    def test_strips_heading_h6(self):
        assert strip_markdown("###### Deep heading") == "Deep heading"

    def test_hash_without_space_preserved(self):
        """A # without trailing space is NOT a heading."""
        assert strip_markdown("#hashtag") == "#hashtag"

    def test_multiline_headings(self):
        text = "# Title\n\n## Section\n\nSome text\n\n### Sub"
        expected = "Title\n\nSection\n\nSome text\n\nSub"
        assert strip_markdown(text) == expected

    def test_combined_formatting(self):
        text = "## **Bold heading**\n\nSome ~~deleted~~ and __underlined__ text"
        expected = "Bold heading\n\nSome deleted and underlined text"
        assert strip_markdown(text) == expected

    def test_nested_bold_in_code_fence(self):
        text = "```\n**not bold inside fence**\n```"
        result = strip_markdown(text)
        assert "**" not in result
        assert "```" not in result
        assert "not bold inside fence" in result

    def test_whitespace_only(self):
        assert strip_markdown("   \n  \n  ") == "   \n  \n  "

    def test_no_heading_mid_line(self):
        """Heading markers only stripped at line start."""
        assert strip_markdown("This is not ## a heading") == "This is not ## a heading"

    def test_multiple_bold_pairs(self):
        text = "**first** and **second** bold"
        assert strip_markdown(text) == "first and second bold"


class TestCleanCliResponse:
    """Tests for clean_cli_response()."""

    def test_plain_response(self):
        assert clean_cli_response("Hello world") == "Hello world"

    def test_empty_response(self):
        assert clean_cli_response("") == ""

    def test_strips_max_turns_error(self):
        text = "Error: max turns reached\nActual response"
        assert clean_cli_response(text) == "Actual response"

    def test_strips_max_turns_case_insensitive(self):
        text = "error: MAX TURNS reached in conversation\nOK"
        assert clean_cli_response(text) == "OK"

    def test_strips_markdown(self):
        text = "## Title\n\n**Bold** and ```code```"
        result = clean_cli_response(text)
        assert "##" not in result
        assert "**" not in result
        assert "```" not in result
        assert "Title" in result
        assert "Bold" in result

    def test_truncates_at_default_limit(self):
        text = "x" * 3000
        result = clean_cli_response(text)
        assert len(result) == DEFAULT_MAX_LENGTH
        assert result.endswith("...")

    def test_truncates_at_custom_limit(self):
        text = "x" * 500
        result = clean_cli_response(text, max_length=100)
        assert len(result) == 100
        assert result.endswith("...")

    def test_no_truncation_within_limit(self):
        text = "Short message"
        result = clean_cli_response(text)
        assert result == "Short message"

    def test_exact_limit_no_truncation(self):
        text = "x" * DEFAULT_MAX_LENGTH
        result = clean_cli_response(text)
        assert result == text
        assert not result.endswith("...")

    def test_strips_surrounding_whitespace(self):
        text = "  \n  Hello  \n  "
        assert clean_cli_response(text) == "Hello"

    def test_preserves_non_error_lines(self):
        text = "Line 1\nError: something else\nLine 3"
        result = clean_cli_response(text)
        assert "Line 1" in result
        assert "Error: something else" in result
        assert "Line 3" in result

    def test_only_strips_max_turns_errors(self):
        text = "Error: max turns limit\nError: network timeout\nDone"
        result = clean_cli_response(text)
        assert "max turns" not in result
        assert "Error: network timeout" in result
        assert "Done" in result

    def test_full_pipeline(self):
        """Tests the complete cleaning pipeline: error strip + markdown strip + truncate."""
        text = (
            "Error: max turns reached\n"
            "## Report\n"
            "\n"
            "**Found** 3 issues:\n"
            "- ~~old~~ new approach\n"
            "- __underline__ check\n"
        )
        result = clean_cli_response(text)
        assert "max turns" not in result
        assert "**" not in result
        assert "~~" not in result
        assert "__" not in result
        assert "Found 3 issues:" in result
        assert "Report" in result

    def test_multiple_error_lines(self):
        text = "Error: max turns 1\nError: max turns 2\nActual content"
        assert clean_cli_response(text) == "Actual content"

    def test_error_line_only(self):
        text = "Error: max turns reached"
        assert clean_cli_response(text) == ""
