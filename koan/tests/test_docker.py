"""Tests for Docker deployment configuration.

Validates Dockerfile, entrypoint, compose, and .dockerignore files
without requiring Docker to be installed.
"""

import os
import re
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class TestDockerfile:
    """Validate Dockerfile structure and best practices."""

    @pytest.fixture(autouse=True)
    def load_dockerfile(self):
        self.dockerfile = (REPO_ROOT / "Dockerfile").read_text()

    def test_dockerfile_exists(self):
        assert (REPO_ROOT / "Dockerfile").exists()

    def test_base_image_is_node(self):
        """Node base for Claude Code CLI (npm install)."""
        assert "FROM node:" in self.dockerfile

    def test_installs_python3(self):
        assert "python3" in self.dockerfile

    def test_installs_git(self):
        assert "git" in self.dockerfile

    def test_installs_claude_code(self):
        assert "@anthropic-ai/claude-code" in self.dockerfile

    def test_creates_nonroot_user(self):
        """Security: must not run as root."""
        assert "useradd" in self.dockerfile
        assert "USER koan" in self.dockerfile

    def test_sets_koan_root(self):
        assert "KOAN_ROOT=/app" in self.dockerfile

    def test_sets_pythonpath(self):
        assert "PYTHONPATH" in self.dockerfile

    def test_has_healthcheck(self):
        assert "HEALTHCHECK" in self.dockerfile

    def test_copies_requirements_before_code(self):
        """Requirements should be copied first for Docker layer caching."""
        req_line = self.dockerfile.index("COPY koan/requirements.txt")
        code_line = self.dockerfile.index("COPY koan/ ")
        assert req_line < code_line

    def test_entrypoint_is_set(self):
        assert "ENTRYPOINT" in self.dockerfile

    def test_default_cmd_is_start(self):
        assert 'CMD ["start"]' in self.dockerfile

    def test_healthcheck_reads_heartbeat_content(self):
        """HEALTHCHECK should read heartbeat file content (Unix timestamp), not mtime."""
        assert "cat /app/.koan-heartbeat" in self.dockerfile

    def test_cleans_apt_cache(self):
        """Image size: apt cache should be cleaned."""
        assert "rm -rf /var/lib/apt/lists/*" in self.dockerfile

    def test_pip_no_cache(self):
        """Image size: pip should use --no-cache-dir."""
        assert "--no-cache-dir" in self.dockerfile


class TestEntrypoint:
    """Validate docker-entrypoint.sh structure and correctness."""

    @pytest.fixture(autouse=True)
    def load_entrypoint(self):
        self.entrypoint_path = REPO_ROOT / "docker-entrypoint.sh"
        self.entrypoint = self.entrypoint_path.read_text()

    def test_entrypoint_exists(self):
        assert self.entrypoint_path.exists()

    def test_entrypoint_is_executable(self):
        assert os.access(self.entrypoint_path, os.X_OK)

    def test_bash_syntax_valid(self):
        result = subprocess.run(
            ["bash", "-n", str(self.entrypoint_path)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Bash syntax error: {result.stderr}"

    def test_uses_strict_mode(self):
        assert "set -euo pipefail" in self.entrypoint

    def test_supports_start_command(self):
        assert "start)" in self.entrypoint

    def test_supports_agent_command(self):
        assert "agent)" in self.entrypoint

    def test_supports_bridge_command(self):
        assert "bridge)" in self.entrypoint

    def test_supports_test_command(self):
        assert "test)" in self.entrypoint

    def test_supports_shell_command(self):
        assert "shell)" in self.entrypoint

    def test_handles_anthropic_api_key(self):
        assert "ANTHROPIC_API_KEY" in self.entrypoint

    def test_handles_claude_auth_token(self):
        assert "CLAUDE_AUTH_TOKEN" in self.entrypoint

    def test_handles_github_token(self):
        assert "GITHUB_TOKEN" in self.entrypoint

    def test_handles_ssh_key(self):
        assert "KOAN_GIT_SSH_KEY" in self.entrypoint

    def test_traps_signals(self):
        """Must handle SIGINT/SIGTERM for graceful shutdown."""
        assert "trap" in self.entrypoint
        assert "INT TERM" in self.entrypoint

    def test_initializes_instance_from_template(self):
        assert "instance.example" in self.entrypoint

    def test_has_process_monitoring(self):
        assert "monitor_processes" in self.entrypoint

    def test_restarts_bridge_on_crash(self):
        """Bridge should auto-restart if it crashes."""
        assert "Bridge crashed" in self.entrypoint
        assert "start_bridge" in self.entrypoint

    def test_graceful_shutdown_timeout(self):
        """Should not kill -9 immediately."""
        assert "kill -9" in self.entrypoint
        # Graceful kill should come first
        graceful_pos = self.entrypoint.index("kill \"$BRIDGE_PID\"")
        force_pos = self.entrypoint.index("kill -9")
        assert graceful_pos < force_pos

    def test_clones_repos_from_env(self):
        assert "KOAN_DOCKER_REPOS" in self.entrypoint

    def test_builds_koan_projects_from_repos(self):
        """Entrypoint should auto-generate KOAN_PROJECTS from cloned repos."""
        assert "export KOAN_PROJECTS" in self.entrypoint

    def test_credential_helper_uses_env_at_runtime(self):
        """Token should be read from env at call-time, not embedded in gitconfig."""
        # The credential helper should reference $GITHUB_TOKEN, not embed the value
        # Old pattern: '"$GITHUB_TOKEN"' (breaks out of quotes to embed)
        # New pattern: '$GITHUB_TOKEN' (literal, expanded by shell at call-time)
        assert "password=$GITHUB_TOKEN" in self.entrypoint

    def test_touches_heartbeat_on_start(self):
        """Heartbeat file should be created at boot to avoid healthcheck failure."""
        assert "koan-heartbeat" in self.entrypoint


class TestDockerIgnore:
    """Validate .dockerignore prevents sensitive files from being copied."""

    @pytest.fixture(autouse=True)
    def load_dockerignore(self):
        self.dockerignore = (REPO_ROOT / ".dockerignore").read_text()
        self.patterns = [
            line.strip()
            for line in self.dockerignore.splitlines()
            if line.strip() and not line.startswith("#")
        ]

    def test_dockerignore_exists(self):
        assert (REPO_ROOT / ".dockerignore").exists()

    def test_excludes_env_file(self):
        assert ".env" in self.patterns

    def test_excludes_instance(self):
        assert "instance/" in self.patterns

    def test_excludes_venv(self):
        assert ".venv/" in self.patterns

    def test_excludes_git(self):
        assert ".git/" in self.patterns

    def test_excludes_pycache(self):
        assert "__pycache__/" in self.patterns


class TestDockerCompose:
    """Validate docker-compose.yml structure."""

    @pytest.fixture(autouse=True)
    def load_compose(self):
        self.compose = (REPO_ROOT / "docker-compose.yml").read_text()

    def test_compose_exists(self):
        assert (REPO_ROOT / "docker-compose.yml").exists()

    def test_defines_koan_service(self):
        assert "koan:" in self.compose

    def test_uses_env_docker_file(self):
        assert ".env.docker" in self.compose

    def test_has_restart_policy(self):
        assert "restart:" in self.compose

    def test_has_instance_volume(self):
        assert "koan-instance" in self.compose

    def test_has_repos_volume(self):
        assert "koan-repos" in self.compose

    def test_no_ports_exposed_by_default(self):
        """Security: dashboard should NOT be exposed by default."""
        lines = [
            line.strip()
            for line in self.compose.splitlines()
            if not line.strip().startswith("#")
        ]
        for line in lines:
            assert not re.match(r'^\s*-\s*"\d+:\d+"', line), \
                f"Port exposed by default: {line}"


class TestEnvDockerExample:
    """Validate env.docker.example template."""

    @pytest.fixture(autouse=True)
    def load_env(self):
        self.env_path = REPO_ROOT / "env.docker.example"
        self.env = self.env_path.read_text()

    def test_env_example_exists(self):
        assert self.env_path.exists()

    def test_documents_anthropic_api_key(self):
        assert "ANTHROPIC_API_KEY" in self.env

    def test_documents_claude_auth_token(self):
        assert "CLAUDE_AUTH_TOKEN" in self.env

    def test_documents_telegram_token(self):
        assert "KOAN_TELEGRAM_TOKEN" in self.env

    def test_documents_github_token(self):
        assert "GITHUB_TOKEN" in self.env

    def test_documents_docker_repos(self):
        assert "KOAN_DOCKER_REPOS" in self.env

    def test_no_actual_secrets(self):
        """Template must not contain real credentials."""
        # Check for common secret patterns
        assert "sk-ant-" not in self.env
        assert "ghp_" not in self.env
        assert "xoxb-" not in self.env
