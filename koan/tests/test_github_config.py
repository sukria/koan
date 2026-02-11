"""Tests for github_config.py — GitHub notification configuration helpers."""

import pytest

from app.github_config import (
    get_github_authorized_users,
    get_github_commands_enabled,
    get_github_max_age_hours,
    get_github_nickname,
    validate_github_config,
)


class TestGetGithubNickname:
    def test_present(self):
        assert get_github_nickname({"github": {"nickname": "koan-bot"}}) == "koan-bot"

    def test_missing(self):
        assert get_github_nickname({}) == ""

    def test_empty_github_section(self):
        assert get_github_nickname({"github": {}}) == ""

    def test_none_github_section(self):
        assert get_github_nickname({"github": None}) == ""

    def test_whitespace_stripped(self):
        assert get_github_nickname({"github": {"nickname": "  bot  "}}) == "bot"


class TestGetGithubCommandsEnabled:
    def test_enabled(self):
        assert get_github_commands_enabled({"github": {"commands_enabled": True}}) is True

    def test_disabled(self):
        assert get_github_commands_enabled({"github": {"commands_enabled": False}}) is False

    def test_missing(self):
        assert get_github_commands_enabled({}) is False

    def test_none_section(self):
        assert get_github_commands_enabled({"github": None}) is False


class TestGetGithubAuthorizedUsers:
    def test_global_wildcard(self):
        config = {"github": {"authorized_users": ["*"]}}
        assert get_github_authorized_users(config) == ["*"]

    def test_global_explicit_list(self):
        config = {"github": {"authorized_users": ["alice", "bob"]}}
        assert get_github_authorized_users(config) == ["alice", "bob"]

    def test_missing_config(self):
        assert get_github_authorized_users({}) == []

    def test_per_project_override(self):
        config = {"github": {"authorized_users": ["*"]}}
        projects_config = {
            "defaults": {},
            "projects": {
                "myapp": {
                    "path": "/tmp/myapp",
                    "github": {"authorized_users": ["alice"]},
                }
            },
        }
        result = get_github_authorized_users(
            config, project_name="myapp", projects_config=projects_config
        )
        assert result == ["alice"]

    def test_per_project_fallback_to_global(self):
        config = {"github": {"authorized_users": ["bob"]}}
        projects_config = {
            "defaults": {},
            "projects": {"myapp": {"path": "/tmp/myapp"}},
        }
        result = get_github_authorized_users(
            config, project_name="myapp", projects_config=projects_config
        )
        assert result == ["bob"]


class TestGetGithubMaxAgeHours:
    def test_default(self):
        assert get_github_max_age_hours({}) == 24

    def test_custom(self):
        assert get_github_max_age_hours({"github": {"max_age_hours": 48}}) == 48

    def test_invalid_value(self):
        assert get_github_max_age_hours({"github": {"max_age_hours": "bad"}}) == 24


class TestValidateGithubConfig:
    def test_disabled_is_valid(self):
        assert validate_github_config({}) is None
        assert validate_github_config({"github": {"commands_enabled": False}}) is None

    def test_enabled_without_nickname_fails(self):
        result = validate_github_config({"github": {"commands_enabled": True}})
        assert result is not None
        assert "nickname" in result

    def test_enabled_with_nickname_passes(self):
        config = {"github": {"commands_enabled": True, "nickname": "bot"}}
        assert validate_github_config(config) is None

    def test_enabled_with_empty_nickname_fails(self):
        config = {"github": {"commands_enabled": True, "nickname": ""}}
        result = validate_github_config(config)
        assert result is not None
