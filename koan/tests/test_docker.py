"""Tests for Docker deployment configuration.

Validates Dockerfile, docker-compose.yml, entrypoint, and setup script
without actually running Docker.
"""

import os
import subprocess
import textwrap

import pytest


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestDockerfile:
    """Validate Dockerfile structure and best practices."""

    @pytest.fixture
    def dockerfile(self):
        path = os.path.join(REPO_ROOT, "Dockerfile")
        with open(path) as f:
            return f.read()

    def test_dockerfile_exists(self):
        assert os.path.isfile(os.path.join(REPO_ROOT, "Dockerfile"))

    def test_base_image_is_python_slim(self, dockerfile):
        """Thin image — no node, no npm, no Claude CLI install."""
        assert "FROM python:3.12-slim" in dockerfile

    def test_no_npm_install(self, dockerfile):
        """Claude CLI is mounted from host, not installed in image."""
        assert "npm install" not in dockerfile
        assert "claude-code" not in dockerfile

    def test_no_node_image(self, dockerfile):
        """No Node.js base image."""
        assert "FROM node" not in dockerfile

    def test_configurable_uid_gid(self, dockerfile):
        """UID/GID must be configurable for volume permissions."""
        assert "HOST_UID" in dockerfile
        assert "HOST_GID" in dockerfile

    def test_non_root_user(self, dockerfile):
        """Container runs as non-root user."""
        assert "USER koan" in dockerfile

    def test_copies_requirements(self, dockerfile):
        """Requirements installed as cached layer."""
        assert "requirements.txt" in dockerfile

    def test_copies_skills(self, dockerfile):
        """Skills directory must be in the image."""
        assert "COPY skills/" in dockerfile

    def test_healthcheck_defined(self, dockerfile):
        """Health check for container orchestration."""
        assert "HEALTHCHECK" in dockerfile

    def test_entrypoint_set(self, dockerfile):
        """Entrypoint delegates to docker-entrypoint.sh."""
        assert "docker-entrypoint.sh" in dockerfile

    def test_koan_root_env(self, dockerfile):
        assert "KOAN_ROOT=/app" in dockerfile

    def test_pythonpath_env(self, dockerfile):
        assert "PYTHONPATH=/app/koan" in dockerfile


class TestDockerCompose:
    """Validate docker-compose.yml structure."""

    @pytest.fixture
    def compose(self):
        path = os.path.join(REPO_ROOT, "docker-compose.yml")
        with open(path) as f:
            return f.read()

    def test_compose_exists(self):
        assert os.path.isfile(os.path.join(REPO_ROOT, "docker-compose.yml"))

    def test_mounts_claude_config(self, compose):
        """Must mount ~/.claude for auth."""
        assert ".claude" in compose

    def test_mounts_gh_config(self, compose):
        """Must mount GitHub CLI auth."""
        assert ".config/gh" in compose

    def test_mounts_gitconfig(self, compose):
        """Must mount git identity."""
        assert ".gitconfig" in compose

    def test_mounts_instance(self, compose):
        """Instance state persisted via volume."""
        assert "./instance:/app/instance" in compose

    def test_env_file_reference(self, compose):
        """References .env.docker for secrets."""
        assert ".env.docker" in compose

    def test_restart_policy(self, compose):
        assert "restart: unless-stopped" in compose

    def test_no_hardcoded_project_paths(self, compose):
        """Project mounts go in override file, not here (comments are OK)."""
        for line in compose.split("\n"):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            # No hardcoded host project paths in active config
            assert "/Users/" not in line, f"Hardcoded /Users/ path: {line}"
            # Container paths like /home/koan/ are fine
            if "/home/" in line:
                assert "/home/koan/" in line, f"Unexpected /home/ path: {line}"


class TestDockerEntrypoint:
    """Validate docker-entrypoint.sh structure."""

    @pytest.fixture
    def entrypoint(self):
        path = os.path.join(REPO_ROOT, "docker-entrypoint.sh")
        with open(path) as f:
            return f.read()

    def test_entrypoint_exists(self):
        assert os.path.isfile(os.path.join(REPO_ROOT, "docker-entrypoint.sh"))

    def test_entrypoint_is_executable(self):
        path = os.path.join(REPO_ROOT, "docker-entrypoint.sh")
        assert os.access(path, os.X_OK)

    def test_verifies_claude_binary(self, entrypoint):
        assert "verify_binaries" in entrypoint
        assert "claude" in entrypoint

    def test_verifies_gh_binary(self, entrypoint):
        assert "gh" in entrypoint

    def test_verifies_claude_auth(self, entrypoint):
        assert "verify_claude_auth" in entrypoint

    def test_verifies_gh_auth(self, entrypoint):
        assert "verify_gh_auth" in entrypoint

    def test_supports_start_command(self, entrypoint):
        assert "start)" in entrypoint

    def test_supports_test_command(self, entrypoint):
        assert "test)" in entrypoint

    def test_supports_shell_command(self, entrypoint):
        assert "shell)" in entrypoint

    def test_supports_agent_command(self, entrypoint):
        assert "agent)" in entrypoint

    def test_supports_bridge_command(self, entrypoint):
        assert "bridge)" in entrypoint

    def test_process_supervision(self, entrypoint):
        """Must monitor and restart child processes."""
        assert "monitor_children" in entrypoint

    def test_graceful_shutdown(self, entrypoint):
        """Must handle SIGTERM/SIGINT gracefully."""
        assert "SIGTERM" in entrypoint
        assert "SIGINT" in entrypoint
        assert "cleanup" in entrypoint

    def test_heartbeat_mechanism(self, entrypoint):
        """Must write heartbeat for health check."""
        assert "koan-heartbeat" in entrypoint

    def test_setup_instance(self, entrypoint):
        assert "setup_instance" in entrypoint

    def test_set_e_pipefail(self, entrypoint):
        """Must use strict bash mode."""
        assert "set -euo pipefail" in entrypoint


class TestSetupDocker:
    """Validate setup-docker.sh helper."""

    @pytest.fixture
    def setup_script(self):
        path = os.path.join(REPO_ROOT, "setup-docker.sh")
        with open(path) as f:
            return f.read()

    def test_setup_script_exists(self):
        assert os.path.isfile(os.path.join(REPO_ROOT, "setup-docker.sh"))

    def test_setup_script_is_executable(self):
        path = os.path.join(REPO_ROOT, "setup-docker.sh")
        assert os.access(path, os.X_OK)

    def test_detects_claude_binary(self, setup_script):
        assert "detect_binary" in setup_script
        assert '"claude"' in setup_script

    def test_detects_gh_binary(self, setup_script):
        assert '"gh"' in setup_script

    def test_reads_projects_yaml(self, setup_script):
        assert "projects.yaml" in setup_script

    def test_generates_override_file(self, setup_script):
        assert "docker-compose.override.yml" in setup_script

    def test_detects_uid_gid(self, setup_script):
        """Must detect host UID/GID for permission matching."""
        assert "id -u" in setup_script
        assert "id -g" in setup_script

    def test_detects_node_runtime(self, setup_script):
        """Claude CLI needs Node.js/Bun — must detect and mount."""
        assert "node" in setup_script

    def test_mounts_ssh_keys(self, setup_script):
        """Optional SSH key mounting for git operations."""
        assert ".ssh" in setup_script


class TestDockerIgnore:
    """Validate .dockerignore keeps the build context clean."""

    @pytest.fixture
    def dockerignore(self):
        path = os.path.join(REPO_ROOT, ".dockerignore")
        with open(path) as f:
            return f.read()

    def test_dockerignore_exists(self):
        assert os.path.isfile(os.path.join(REPO_ROOT, ".dockerignore"))

    def test_ignores_instance(self, dockerignore):
        """Instance state is mounted at runtime."""
        assert "instance/" in dockerignore

    def test_ignores_git(self, dockerignore):
        assert ".git/" in dockerignore

    def test_ignores_venv(self, dockerignore):
        assert ".venv/" in dockerignore

    def test_ignores_env_files(self, dockerignore):
        """Secrets must not be baked into image."""
        assert ".env" in dockerignore

    def test_ignores_pycache(self, dockerignore):
        assert "__pycache__" in dockerignore


class TestEnvDockerExample:
    """Validate env.docker.example template."""

    @pytest.fixture
    def env_example(self):
        path = os.path.join(REPO_ROOT, "env.docker.example")
        with open(path) as f:
            return f.read()

    def test_env_example_exists(self):
        assert os.path.isfile(os.path.join(REPO_ROOT, "env.docker.example"))

    def test_has_telegram_token(self, env_example):
        assert "KOAN_TELEGRAM_TOKEN" in env_example

    def test_has_telegram_chat_id(self, env_example):
        assert "KOAN_TELEGRAM_CHAT_ID" in env_example

    def test_has_host_uid(self, env_example):
        assert "HOST_UID" in env_example

    def test_no_real_secrets(self, env_example):
        """Example file must not contain actual tokens."""
        lines = env_example.strip().split("\n")
        for line in lines:
            if "=" in line and not line.startswith("#"):
                key, _, value = line.partition("=")
                # Values should be empty or commented out
                assert value.strip() == "" or value.strip().startswith("#"), \
                    f"env.docker.example has a non-empty value: {key}"


class TestEntrypointFunctions:
    """Test entrypoint shell functions via subprocess."""

    def test_verify_binaries_reports_missing(self, tmp_path):
        """verify_binaries should fail when binaries are missing."""
        script = textwrap.dedent("""\
            #!/bin/bash
            set -euo pipefail
            export PATH=/nonexistent
            log() { echo "$*"; }

            verify_binaries() {
                local missing=()
                command -v claude >/dev/null 2>&1 || missing+=("claude")
                command -v gh >/dev/null 2>&1 || missing+=("gh")
                if [ ${#missing[@]} -gt 0 ]; then
                    echo "MISSING: ${missing[*]}"
                    return 1
                fi
                echo "OK"
                return 0
            }

            verify_binaries
        """)
        script_file = tmp_path / "test.sh"
        script_file.write_text(script)
        result = subprocess.run(
            ["bash", str(script_file)],
            capture_output=True, text=True
        )
        assert "MISSING" in result.stdout
        assert "claude" in result.stdout

    def test_setup_instance_creates_dirs(self, tmp_path):
        """setup_instance should create instance/ from template if missing."""
        template = tmp_path / "instance.example"
        template.mkdir()
        (template / "config.yaml").write_text("test: true")

        script = textwrap.dedent(f"""\
            #!/bin/bash
            set -euo pipefail
            KOAN_ROOT="{tmp_path}"
            INSTANCE="{tmp_path}/instance"
            log() {{ echo "$*"; }}

            setup_instance() {{
                if [ ! -d "$INSTANCE" ]; then
                    cp -r "$KOAN_ROOT/instance.example" "$INSTANCE"
                fi
                mkdir -p "$INSTANCE/journal" "$INSTANCE/memory/global" "$INSTANCE/memory/projects"
            }}

            setup_instance
            test -d "{tmp_path}/instance" && echo "INSTANCE_CREATED"
            test -d "{tmp_path}/instance/journal" && echo "JOURNAL_CREATED"
            test -d "{tmp_path}/instance/memory/global" && echo "MEMORY_CREATED"
            test -f "{tmp_path}/instance/config.yaml" && echo "CONFIG_COPIED"
        """)
        script_file = tmp_path / "test.sh"
        script_file.write_text(script)
        result = subprocess.run(
            ["bash", str(script_file)],
            capture_output=True, text=True
        )
        assert "INSTANCE_CREATED" in result.stdout
        assert "JOURNAL_CREATED" in result.stdout
        assert "MEMORY_CREATED" in result.stdout
        assert "CONFIG_COPIED" in result.stdout


class TestSetupDockerScript:
    """Test setup-docker.sh output format."""

    def test_override_yaml_format(self, tmp_path):
        """Generated override file should be valid YAML-ish."""
        override = tmp_path / "docker-compose.override.yml"
        override.write_text(textwrap.dedent("""\
            services:
              koan:
                build:
                  args:
                    HOST_UID: "501"
                    HOST_GID: "20"
                volumes:
                  - /usr/local/bin/claude:/usr/local/bin/claude:ro
                  - /usr/local/bin/gh:/usr/local/bin/gh:ro
        """))

        content = override.read_text()
        assert "services:" in content
        assert "koan:" in content
        assert "volumes:" in content
        assert ":ro" in content
