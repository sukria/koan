"""Shared fixtures for koan tests."""

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolate_env(monkeypatch):
    """Ensure tests don't touch real instance/ or send real Telegram messages."""
    monkeypatch.setenv("KOAN_TELEGRAM_TOKEN", "fake-token")
    monkeypatch.setenv("KOAN_TELEGRAM_CHAT_ID", "123456")
    monkeypatch.delenv("KOAN_PROJECT_PATH", raising=False)
    monkeypatch.delenv("KOAN_PROJECTS", raising=False)


@pytest.fixture
def instance_dir(tmp_path):
    """Create a minimal instance directory structure."""
    inst = tmp_path / "instance"
    inst.mkdir()
    (inst / "soul.md").write_text("# Test Soul")
    (inst / "memory").mkdir()
    (inst / "memory" / "summary.md").write_text("Test summary.")
    (inst / "journal").mkdir()
    (inst / "outbox.md").write_text("")
    missions = inst / "missions.md"
    missions.write_text(
        "# Missions\n\n"
        "## Pending\n\n"
        "(none)\n\n"
        "## In Progress\n\n"
        "## Done\n\n"
    )
    return inst
