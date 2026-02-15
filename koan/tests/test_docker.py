"""Tests for Docker deployment configuration.

Validates Dockerfile, entrypoint, compose, setup script, and .dockerignore
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

    def test_base_image_is_python_slim(self):
        """Thin image — CLIs are mounted, not installed."""
        assert "FROM python:3.12-slim" in self.dockerfile

    def test_no_claude_cli_install(self):
        """Mounted binaries approach: no npm install of Claude CLI."""
        assert "@anthropic-ai/claude-code" not in self.dockerfile
        assert "npm install" not in self.dockerfile

    def test_no_node_install(self):
        """Node.js is mounted from host if needed, not installed in image."""
        assert "FROM node:" not in self.dockerfile

    def test_installs_git(self):
        assert "git" in self.dockerfile

    def test_installs_make(self):
        """Make is needed for workspace-level builds."""
        assert "make" in self.dockerfile

    def test_configurable_uid_gid(self):
        """UID/GID should be build args for bind mount permissions."""
        assert "ARG HOST_UID" in self.dockerfile
        assert "ARG HOST_GID" in self.dockerfile

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

    def test_healthcheck_reads_heartbeat_content(self):
        """HEALTHCHECK should read heartbeat file content (Unix timestamp)."""
        assert "cat /app/.koan-heartbeat" in self.dockerfile

    def test_copies_requirements_before_code(self):
        """Requirements should be copied first for Docker layer caching."""
        req_line = self.dockerfile.index("COPY koan/requirements.txt")
        code_line = self.dockerfile.index("COPY koan/ ")
        assert req_line < code_line

    def test_host_bin_in_path(self):
        """PATH should include /host-bin for mounted CLI binaries."""
        assert "/host-bin" in self.dockerfile

    def test_workspace_directory_created(self):
        assert "/app/workspace" in self.dockerfile

    def test_entrypoint_is_set(self):
        assert "ENTRYPOINT" in self.dockerfile

    def test_default_cmd_is_start(self):
        assert 'CMD ["start"]' in self.dockerfile

    def test_cleans_apt_cache(self):
        """Image size: apt cache should be cleaned."""
        assert "rm -rf /var/lib/apt/lists/*" in self.dockerfile

    def test_pip_no_cache(self):
        """Image size: pip should use --no-cache-dir."""
        assert "--no-cache-dir" in self.dockerfile

    def test_creates_host_bin_and_host_node_dirs(self):
        """Directories for mounted binaries must exist."""
        assert "/host-bin" in self.dockerfile
        assert "/host-node" in self.dockerfile


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

    def test_verifies_mounted_binaries(self):
        """Entrypoint must check that CLI binaries are available."""
        assert "verify_binaries" in self.entrypoint

    def test_verifies_auth_state(self):
        """Entrypoint should check for auth directories."""
        assert "verify_auth" in self.entrypoint

    def test_supports_claude_provider(self):
        assert "claude)" in self.entrypoint or "claude" in self.entrypoint

    def test_supports_copilot_provider(self):
        assert "copilot" in self.entrypoint

    def test_supports_ollama_provider(self):
        assert "ollama" in self.entrypoint

    def test_checks_claude_auth_dir(self):
        """Should check for ~/.claude directory."""
        assert ".claude" in self.entrypoint

    def test_checks_copilot_auth_dir(self):
        """Should check for ~/.copilot directory."""
        assert ".copilot" in self.entrypoint

    def test_checks_gh_auth_dir(self):
        """Should check for ~/.config/gh directory."""
        assert ".config/gh" in self.entrypoint

    def test_traps_signals(self):
        """Must handle SIGINT/SIGTERM for graceful shutdown."""
        assert "trap" in self.entrypoint
        assert "INT TERM" in self.entrypoint

    def test_creates_stop_signal_on_shutdown(self):
        """Graceful shutdown should create .koan-stop signal."""
        assert ".koan-stop" in self.entrypoint

    def test_initializes_instance_from_template(self):
        assert "instance.example" in self.entrypoint

    def test_has_process_monitoring(self):
        assert "monitor_processes" in self.entrypoint

    def test_restarts_processes_on_crash(self):
        """Both bridge and agent should auto-restart."""
        assert "restarting" in self.entrypoint.lower()

    def test_graceful_shutdown_timeout(self):
        """Should not kill -9 immediately."""
        assert "kill -9" in self.entrypoint
        # Graceful kill should come first
        graceful_pos = self.entrypoint.index("kill \"$BRIDGE_PID\"")
        force_pos = self.entrypoint.index("kill -9")
        assert graceful_pos < force_pos

    def test_writes_heartbeat_during_monitoring(self):
        """Monitor loop should write heartbeat for HEALTHCHECK."""
        assert "koan-heartbeat" in self.entrypoint

    def test_uses_run_py_not_run_sh(self):
        """Agent loop should use run.py (pure Python), not run.sh."""
        assert "app/run.py" in self.entrypoint
        assert "run.sh" not in self.entrypoint

    def test_uses_awake_py(self):
        """Bridge should use awake.py."""
        assert "app/awake.py" in self.entrypoint

    def test_no_repo_cloning(self):
        """Mounted binaries approach: no repo cloning in container."""
        assert "git clone" not in self.entrypoint

    def test_no_credential_setup(self):
        """Auth comes from mounted directories, not env vars."""
        assert "credential.helper" not in self.entrypoint
        assert "setup-token" not in self.entrypoint

    def test_setup_workspace(self):
        """Entrypoint should set up workspace directory."""
        assert "setup_workspace" in self.entrypoint


class TestSetupScript:
    """Validate setup-docker.sh structure and correctness."""

    @pytest.fixture(autouse=True)
    def load_setup(self):
        self.setup_path = REPO_ROOT / "setup-docker.sh"
        self.setup = self.setup_path.read_text()

    def test_setup_exists(self):
        assert self.setup_path.exists()

    def test_setup_is_executable(self):
        assert os.access(self.setup_path, os.X_OK)

    def test_bash_syntax_valid(self):
        result = subprocess.run(
            ["bash", "-n", str(self.setup_path)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Bash syntax error: {result.stderr}"

    def test_uses_strict_mode(self):
        assert "set -euo pipefail" in self.setup

    def test_supports_dry_run(self):
        assert "--dry-run" in self.setup

    def test_generates_override_file(self):
        assert "docker-compose.override.yml" in self.setup

    def test_detects_gh_binary(self):
        assert '"gh"' in self.setup

    def test_detects_claude_binary(self):
        assert '"claude"' in self.setup

    def test_detects_copilot_binary(self):
        """Should detect GitHub Copilot CLI."""
        assert "copilot" in self.setup

    def test_detects_ollama_binary(self):
        assert '"ollama"' in self.setup

    def test_detects_node_runtime(self):
        """Node.js is needed for Claude CLI wrapper."""
        assert "detect_node_runtime" in self.setup

    def test_detects_claude_auth_dir(self):
        assert ".claude" in self.setup

    def test_detects_copilot_auth_dir(self):
        assert ".copilot" in self.setup

    def test_detects_gh_auth_dir(self):
        assert ".config/gh" in self.setup

    def test_detects_host_uid_gid(self):
        """Should detect and write UID/GID for bind mount permissions."""
        assert "HOST_UID" in self.setup
        assert "HOST_GID" in self.setup

    def test_resolves_workspace_symlinks(self):
        """Docker can't follow symlinks — must resolve them."""
        assert "resolve_workspace" in self.setup
        assert "realpath" in self.setup

    def test_mounts_binaries_read_only(self):
        """CLI binaries should be mounted read-only."""
        assert ":ro" in self.setup

    def test_mounts_claude_auth_read_write(self):
        """Claude auth dir needs write access for session state."""
        # Find the line that mounts .claude — it should be :rw
        lines = self.setup.splitlines()
        for line in lines:
            if ".claude" in line and "container_dir" not in line and "detect_dir" in line:
                assert "rw" in line, "~/.claude should be mounted read-write"
                break


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

    def test_excludes_workspace(self):
        """Workspace is mounted at runtime, not copied."""
        assert "workspace/" in self.patterns

    def test_excludes_docker_override(self):
        """Generated override should not be in build context."""
        assert "docker-compose.override.yml" in self.patterns

    def test_excludes_logs(self):
        assert "logs/" in self.patterns


class TestDockerCompose:
    """Validate docker-compose.yml structure."""

    @pytest.fixture(autouse=True)
    def load_compose(self):
        self.compose = (REPO_ROOT / "docker-compose.yml").read_text()

    def test_compose_exists(self):
        assert (REPO_ROOT / "docker-compose.yml").exists()

    def test_defines_koan_service(self):
        assert "koan:" in self.compose

    def test_uses_env_file(self):
        """Should use .env for credentials."""
        assert ".env" in self.compose

    def test_has_restart_policy(self):
        assert "restart:" in self.compose

    def test_mounts_instance_dir(self):
        """Instance state should be a bind mount for persistence."""
        assert "./instance:/app/instance" in self.compose

    def test_mounts_workspace_dir(self):
        """Workspace should be mounted for project access."""
        assert "./workspace:/app/workspace" in self.compose

    def test_mounts_logs_dir(self):
        assert "./logs:/app/logs" in self.compose

    def test_configurable_uid_gid(self):
        """Build args should support HOST_UID/HOST_GID."""
        assert "HOST_UID" in self.compose
        assert "HOST_GID" in self.compose

    def test_no_ports_exposed_by_default(self):
        """Security: dashboard should NOT be exposed by default."""
        lines = [
            line.strip()
            for line in self.compose.splitlines()
            if not line.strip().startswith("#")
        ]
        for line in lines:
            assert not re.match(r'^-\s*"\d+:\d+"', line), \
                f"Port exposed by default: {line}"

    def test_no_named_volumes(self):
        """Bind mounts preferred over named volumes for transparency."""
        # Named volumes use a volumes: section at the root level
        lines = self.compose.splitlines()
        root_volumes = False
        for line in lines:
            # Root-level "volumes:" (not indented) indicates named volumes
            if re.match(r'^volumes:', line):
                root_volumes = True
                break
        assert not root_volumes, "Prefer bind mounts over named Docker volumes"


class TestMakefileDockerTargets:
    """Validate Docker-related Makefile targets."""

    @pytest.fixture(autouse=True)
    def load_makefile(self):
        self.makefile = (REPO_ROOT / "Makefile").read_text()

    def test_docker_setup_target(self):
        assert "docker-setup:" in self.makefile

    def test_docker_up_target(self):
        assert "docker-up:" in self.makefile

    def test_docker_down_target(self):
        assert "docker-down:" in self.makefile

    def test_docker_logs_target(self):
        assert "docker-logs:" in self.makefile

    def test_docker_test_target(self):
        assert "docker-test:" in self.makefile

    def test_docker_phony_declarations(self):
        """Docker targets should be declared .PHONY."""
        assert "docker-setup" in self.makefile
        assert "docker-up" in self.makefile


class TestGitIgnoreDockerEntries:
    """Validate Docker entries in .gitignore."""

    @pytest.fixture(autouse=True)
    def load_gitignore(self):
        self.gitignore = (REPO_ROOT / ".gitignore").read_text()

    def test_ignores_docker_override(self):
        assert "docker-compose.override.yml" in self.gitignore

    def test_ignores_env_docker(self):
        assert ".env.docker" in self.gitignore


class TestDesignPrinciples:
    """Validate key architectural decisions of the mounted-binaries approach."""

    def test_no_npm_in_dockerfile(self):
        """Dockerfile should NOT install npm packages."""
        dockerfile = (REPO_ROOT / "Dockerfile").read_text()
        assert "npm install" not in dockerfile
        assert "npm i " not in dockerfile

    def test_no_setup_token_in_entrypoint(self):
        """Auth comes from mounted dirs, not setup-token."""
        entrypoint = (REPO_ROOT / "docker-entrypoint.sh").read_text()
        assert "setup-token" not in entrypoint

    def test_no_api_key_in_entrypoint(self):
        """No ANTHROPIC_API_KEY credential helper — auth is mounted."""
        entrypoint = (REPO_ROOT / "docker-entrypoint.sh").read_text()
        # Should not set up credential helpers with embedded tokens
        assert "credential.helper" not in entrypoint

    def test_entrypoint_references_python_processes(self):
        """Both processes should be Python (run.py, awake.py)."""
        entrypoint = (REPO_ROOT / "docker-entrypoint.sh").read_text()
        assert "app/run.py" in entrypoint
        assert "app/awake.py" in entrypoint

    def test_workspace_not_repos(self):
        """Projects directory should be 'workspace/', not 'repos/'."""
        compose = (REPO_ROOT / "docker-compose.yml").read_text()
        assert "workspace" in compose
        assert "koan-repos" not in compose
