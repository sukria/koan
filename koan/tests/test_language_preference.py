"""Tests for language_preference.py — language preference management."""

import json
from unittest.mock import patch

import pytest

from app.language_preference import (
    get_language,
    set_language,
    reset_language,
    get_language_instruction,
)


class TestGetLanguage:
    def test_no_file_returns_empty(self, tmp_path):
        with patch("app.language_preference._get_language_file", return_value=tmp_path / "language.json"):
            assert get_language() == ""

    def test_reads_language_from_file(self, tmp_path):
        lang_file = tmp_path / "language.json"
        lang_file.write_text(json.dumps({"language": "english"}))
        with patch("app.language_preference._get_language_file", return_value=lang_file):
            assert get_language() == "english"

    def test_invalid_json_returns_empty(self, tmp_path):
        lang_file = tmp_path / "language.json"
        lang_file.write_text("not json")
        with patch("app.language_preference._get_language_file", return_value=lang_file):
            assert get_language() == ""

    def test_missing_key_returns_empty(self, tmp_path):
        lang_file = tmp_path / "language.json"
        lang_file.write_text(json.dumps({"other": "value"}))
        with patch("app.language_preference._get_language_file", return_value=lang_file):
            assert get_language() == ""


class TestSetLanguage:
    def test_creates_file(self, tmp_path):
        lang_file = tmp_path / "language.json"
        with patch("app.language_preference._get_language_file", return_value=lang_file):
            set_language("French")
        data = json.loads(lang_file.read_text())
        assert data["language"] == "french"

    def test_normalizes_to_lowercase(self, tmp_path):
        lang_file = tmp_path / "language.json"
        with patch("app.language_preference._get_language_file", return_value=lang_file):
            set_language("SPANISH")
        data = json.loads(lang_file.read_text())
        assert data["language"] == "spanish"

    def test_strips_whitespace(self, tmp_path):
        lang_file = tmp_path / "language.json"
        with patch("app.language_preference._get_language_file", return_value=lang_file):
            set_language("  english  ")
        data = json.loads(lang_file.read_text())
        assert data["language"] == "english"

    def test_overwrites_existing(self, tmp_path):
        lang_file = tmp_path / "language.json"
        lang_file.write_text(json.dumps({"language": "english"}))
        with patch("app.language_preference._get_language_file", return_value=lang_file):
            set_language("german")
        data = json.loads(lang_file.read_text())
        assert data["language"] == "german"


class TestResetLanguage:
    def test_removes_file(self, tmp_path):
        lang_file = tmp_path / "language.json"
        lang_file.write_text(json.dumps({"language": "english"}))
        with patch("app.language_preference._get_language_file", return_value=lang_file):
            reset_language()
        assert not lang_file.exists()

    def test_no_error_if_file_missing(self, tmp_path):
        lang_file = tmp_path / "language.json"
        with patch("app.language_preference._get_language_file", return_value=lang_file):
            reset_language()  # Should not raise


class TestGetLanguageInstruction:
    def test_no_language_returns_empty(self, tmp_path):
        with patch("app.language_preference._get_language_file", return_value=tmp_path / "language.json"):
            assert get_language_instruction() == ""

    def test_with_language_returns_instruction(self, tmp_path):
        lang_file = tmp_path / "language.json"
        lang_file.write_text(json.dumps({"language": "english"}))
        with patch("app.language_preference._get_language_file", return_value=lang_file):
            instruction = get_language_instruction()
        assert "english" in instruction
        assert "MUST" in instruction

    def test_instruction_contains_language_name(self, tmp_path):
        lang_file = tmp_path / "language.json"
        lang_file.write_text(json.dumps({"language": "japanese"}))
        with patch("app.language_preference._get_language_file", return_value=lang_file):
            instruction = get_language_instruction()
        assert "japanese" in instruction
