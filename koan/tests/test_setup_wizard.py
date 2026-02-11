"""
Tests for the setup wizard web app.
"""

import json
import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture
def temp_koan_root():
    """Create a temporary KOAN_ROOT for testing."""
    temp_dir = tempfile.mkdtemp()
    old_root = os.environ.get("KOAN_ROOT")
    os.environ["KOAN_ROOT"] = temp_dir

    # Create instance.example structure
    instance_example = Path(temp_dir) / "instance.example"
    instance_example.mkdir(parents=True)
    (instance_example / "config.yaml").write_text("# Config\n")
    (instance_example / "soul.md").write_text("# Soul\n")
    (instance_example / "missions.md").write_text("# Missions\n")

    # Create env.example
    env_example = Path(temp_dir) / "env.example"
    env_example.write_text("""KOAN_ROOT=/path/to/this/repo
KOAN_TELEGRAM_TOKEN=your-bot-token
KOAN_TELEGRAM_CHAT_ID=your-chat-id
KOAN_PROJECT_PATH=/path/to/your/project
""")

    yield temp_dir

    # Cleanup
    if old_root:
        os.environ["KOAN_ROOT"] = old_root
    else:
        del os.environ["KOAN_ROOT"]
    shutil.rmtree(temp_dir)


@pytest.fixture
def wizard_app(temp_koan_root):
    """Create a test client for the wizard app."""
    # Need to import after setting KOAN_ROOT
    from app.setup_wizard import app, KOAN_ROOT, INSTANCE_DIR, ENV_FILE

    # Update module-level paths to match temp dir
    import app.setup_wizard as wizard_module
    wizard_module.KOAN_ROOT = Path(temp_koan_root)
    wizard_module.INSTANCE_DIR = Path(temp_koan_root) / "instance"
    wizard_module.INSTANCE_EXAMPLE = Path(temp_koan_root) / "instance.example"
    wizard_module.ENV_FILE = Path(temp_koan_root) / ".env"
    wizard_module.ENV_EXAMPLE = Path(temp_koan_root) / "env.example"
    wizard_module.CONFIG_FILE = Path(temp_koan_root) / "instance" / "config.yaml"

    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client, Path(temp_koan_root)


class TestInstallationStatus:
    """Tests for get_installation_status()."""

    def test_fresh_install(self, wizard_app):
        """Fresh install should have no instance, no env, no venv."""
        client, root = wizard_app

        response = client.get("/api/status")
        assert response.status_code == 200

        data = json.loads(response.data)
        assert data["instance_exists"] is False
        assert data["env_exists"] is False
        assert data["telegram_configured"] is False
        assert data["projects_configured"] is False

    def test_after_init(self, wizard_app):
        """After init, instance and env should exist."""
        client, root = wizard_app

        # Run init
        response = client.post("/step/welcome/init")
        assert response.status_code == 200

        # Check status
        response = client.get("/api/status")
        data = json.loads(response.data)
        assert data["instance_exists"] is True
        assert data["env_exists"] is True


class TestWizardRoutes:
    """Tests for wizard navigation."""

    def test_index_redirects_to_welcome(self, wizard_app):
        """Index should redirect to welcome step on fresh install."""
        client, root = wizard_app

        response = client.get("/", follow_redirects=False)
        assert response.status_code == 302
        assert "/step/welcome" in response.location

    def test_welcome_page_loads(self, wizard_app):
        """Welcome page should load successfully."""
        client, root = wizard_app

        response = client.get("/step/welcome")
        assert response.status_code == 200
        assert b"Prerequisites" in response.data

    def test_telegram_page_loads(self, wizard_app):
        """Telegram page should load after init."""
        client, root = wizard_app

        # Init first
        client.post("/step/welcome/init")

        response = client.get("/step/telegram")
        assert response.status_code == 200
        assert b"Telegram Bot" in response.data

    def test_projects_page_loads(self, wizard_app):
        """Projects page should load."""
        client, root = wizard_app

        # Init first
        client.post("/step/welcome/init")

        response = client.get("/step/projects")
        assert response.status_code == 200
        assert b"Add Projects" in response.data


class TestTelegramVerification:
    """Tests for Telegram token verification."""

    def test_verify_empty_token(self, wizard_app):
        """Empty token should fail."""
        client, root = wizard_app

        response = client.post(
            "/step/telegram/verify",
            json={"token": ""},
            content_type="application/json"
        )
        data = json.loads(response.data)
        assert data["ok"] is False
        assert "required" in data["error"].lower()

    @patch("app.setup_wizard.verify_telegram_token")
    def test_verify_valid_token(self, mock_verify, wizard_app):
        """Valid token should return bot info."""
        client, root = wizard_app
        mock_verify.return_value = {
            "valid": True,
            "username": "TestBot",
            "first_name": "Test"
        }

        response = client.post(
            "/step/telegram/verify",
            json={"token": "123456:ABC"},
            content_type="application/json"
        )
        data = json.loads(response.data)
        assert data["valid"] is True
        assert data["username"] == "TestBot"

    @patch("app.setup_wizard.verify_telegram_token")
    def test_verify_invalid_token(self, mock_verify, wizard_app):
        """Invalid token should fail gracefully."""
        client, root = wizard_app
        mock_verify.return_value = {"valid": False, "error": "Bad token"}

        response = client.post(
            "/step/telegram/verify",
            json={"token": "invalid"},
            content_type="application/json"
        )
        data = json.loads(response.data)
        assert data["valid"] is False


class TestTelegramSave:
    """Tests for saving Telegram configuration."""

    def test_save_telegram_config(self, wizard_app):
        """Should save token and chat ID to .env."""
        client, root = wizard_app

        # Init first
        client.post("/step/welcome/init")

        # Save telegram config
        response = client.post(
            "/step/telegram/save",
            json={"token": "123456:ABC", "chat_id": "987654"},
            content_type="application/json"
        )
        data = json.loads(response.data)
        assert data["ok"] is True

        # Verify .env was updated
        env_content = (root / ".env").read_text()
        assert "KOAN_TELEGRAM_TOKEN=123456:ABC" in env_content
        assert "KOAN_TELEGRAM_CHAT_ID=987654" in env_content


class TestProjectValidation:
    """Tests for project path validation."""

    def test_validate_empty_path(self, wizard_app):
        """Empty path should fail."""
        client, root = wizard_app

        response = client.post(
            "/step/projects/validate",
            json={"path": ""},
            content_type="application/json"
        )
        data = json.loads(response.data)
        assert data["valid"] is False
        assert "required" in data["error"].lower()

    def test_validate_nonexistent_path(self, wizard_app):
        """Non-existent path should fail."""
        client, root = wizard_app

        response = client.post(
            "/step/projects/validate",
            json={"path": "/nonexistent/path/12345"},
            content_type="application/json"
        )
        data = json.loads(response.data)
        assert data["valid"] is False
        assert "not exist" in data["error"].lower()

    def test_validate_valid_path(self, wizard_app):
        """Valid directory should pass."""
        client, root = wizard_app

        # Create a test project directory
        project_dir = root / "test-project"
        project_dir.mkdir()

        response = client.post(
            "/step/projects/validate",
            json={"path": str(project_dir)},
            content_type="application/json"
        )
        data = json.loads(response.data)
        assert data["valid"] is True
        assert data["absolute_path"] == str(project_dir)
        assert data["has_claude_md"] is False
        assert data["is_git_repo"] is False

    def test_validate_path_with_claude_md(self, wizard_app):
        """Directory with CLAUDE.md should be detected."""
        client, root = wizard_app

        project_dir = root / "test-project"
        project_dir.mkdir()
        (project_dir / "CLAUDE.md").write_text("# Project")

        response = client.post(
            "/step/projects/validate",
            json={"path": str(project_dir)},
            content_type="application/json"
        )
        data = json.loads(response.data)
        assert data["valid"] is True
        assert data["has_claude_md"] is True

    def test_validate_git_repo(self, wizard_app):
        """Git repo should be detected."""
        client, root = wizard_app

        project_dir = root / "test-project"
        project_dir.mkdir()
        (project_dir / ".git").mkdir()

        response = client.post(
            "/step/projects/validate",
            json={"path": str(project_dir)},
            content_type="application/json"
        )
        data = json.loads(response.data)
        assert data["valid"] is True
        assert data["is_git_repo"] is True


    @pytest.mark.skipif(os.getuid() == 0, reason="root ignores file permissions")
    def test_validate_non_writable_path(self, wizard_app):
        """Non-writable directory should fail validation."""
        client, root = wizard_app

        project_dir = root / "readonly-project"
        project_dir.mkdir()
        project_dir.chmod(0o444)

        try:
            response = client.post(
                "/step/projects/validate",
                json={"path": str(project_dir)},
                content_type="application/json"
            )
            data = json.loads(response.data)
            assert data["valid"] is False
            assert "not writable" in data["error"].lower()
        finally:
            project_dir.chmod(0o755)


class TestProjectSave:
    """Tests for saving project configuration to projects.yaml."""

    def test_save_single_project(self, wizard_app):
        """Single project should create projects.yaml."""
        client, root = wizard_app

        # Init and create project dir
        client.post("/step/welcome/init")
        project_dir = root / "my-project"
        project_dir.mkdir()

        response = client.post(
            "/step/projects/save",
            json={"projects": [{"name": "myproject", "path": str(project_dir)}]},
            content_type="application/json"
        )
        data = json.loads(response.data)
        assert data["ok"] is True

        import yaml
        projects_yaml = root / "projects.yaml"
        assert projects_yaml.exists()
        config = yaml.safe_load(projects_yaml.read_text())
        assert "myproject" in config["projects"]
        assert config["projects"]["myproject"]["path"] == str(project_dir)

    def test_save_multiple_projects(self, wizard_app):
        """Multiple projects should create projects.yaml with all entries."""
        client, root = wizard_app

        # Init and create project dirs
        client.post("/step/welcome/init")
        project1 = root / "project1"
        project2 = root / "project2"
        project1.mkdir()
        project2.mkdir()

        response = client.post(
            "/step/projects/save",
            json={
                "projects": [
                    {"name": "proj1", "path": str(project1)},
                    {"name": "proj2", "path": str(project2)}
                ]
            },
            content_type="application/json"
        )
        data = json.loads(response.data)
        assert data["ok"] is True

        import yaml
        projects_yaml = root / "projects.yaml"
        assert projects_yaml.exists()
        config = yaml.safe_load(projects_yaml.read_text())
        assert "proj1" in config["projects"]
        assert "proj2" in config["projects"]
        assert config["projects"]["proj1"]["path"] == str(project1)
        assert config["projects"]["proj2"]["path"] == str(project2)


class TestSetupExecution:
    """Tests for running make setup."""

    @patch("app.setup_wizard.run_make_setup")
    def test_run_setup_success(self, mock_setup, wizard_app):
        """Successful setup should return ok."""
        client, root = wizard_app
        mock_setup.return_value = (True, "Dependencies installed")

        response = client.post("/step/ready/setup")
        data = json.loads(response.data)
        assert data["ok"] is True

    @patch("app.setup_wizard.run_make_setup")
    def test_run_setup_failure(self, mock_setup, wizard_app):
        """Failed setup should return error."""
        client, root = wizard_app
        mock_setup.return_value = (False, "pip install failed")

        response = client.post("/step/ready/setup")
        data = json.loads(response.data)
        assert data["ok"] is False
        assert "pip install failed" in data["output"]
