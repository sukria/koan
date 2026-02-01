#!/usr/bin/env python3
"""
Koan — Main run loop

Pulls missions, executes them via Claude Code CLI, commits results.
Sends Telegram notifications at each mission lifecycle step.

Replaces run.sh with a Python implementation for better maintainability
and cross-platform compatibility (no bash 3.2 workarounds).
"""

import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

KOAN_ROOT = Path(__file__).resolve().parent.parent
INSTANCE = KOAN_ROOT / "instance"
NOTIFY = Path(__file__).resolve().parent / "notify.py"


def load_dotenv():
    """Load .env file from project root."""
    env_path = KOAN_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


load_dotenv()


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MAX_RUNS = int(os.environ.get("KOAN_MAX_RUNS", "20"))
INTERVAL = int(os.environ.get("KOAN_INTERVAL", "5"))


def parse_projects() -> dict[str, str]:
    """Parse project configuration from env vars. Returns {name: path}."""
    projects_env = os.environ.get("KOAN_PROJECTS", "")
    project_path_env = os.environ.get("KOAN_PROJECT_PATH", "")

    if projects_env:
        projects = {}
        for pair in projects_env.split(";"):
            pair = pair.strip()
            if not pair:
                continue
            if ":" not in pair:
                print(f"[koan] Invalid project format (expected name:path): {pair}")
                sys.exit(1)
            name, path = pair.split(":", 1)
            projects[name.strip()] = path.strip()
        return projects

    if project_path_env:
        return {"default": project_path_env}

    print("[koan] Error: Set KOAN_PROJECT_PATH or KOAN_PROJECTS env var.")
    sys.exit(1)


def validate_projects(projects: dict[str, str]):
    """Validate project configuration."""
    if len(projects) > 5:
        print(f"[koan] Error: Max 5 projects allowed. You have {len(projects)}.")
        sys.exit(1)
    for name, path in projects.items():
        if not Path(path).is_dir():
            print(f"[koan] Error: Project '{name}' path does not exist: {path}")
            sys.exit(1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def python_cmd() -> str:
    """Return path to venv python if available, else system python."""
    venv_python = KOAN_ROOT / ".venv" / "bin" / "python3"
    if venv_python.exists():
        return str(venv_python)
    return "python3"


PYTHON = python_cmd()


def notify(message: str):
    """Send a Telegram notification (best-effort)."""
    try:
        subprocess.run(
            [PYTHON, str(NOTIFY), message],
            capture_output=True, timeout=15,
        )
    except Exception:
        pass


def extract_mission_project(projects: dict[str, str]) -> tuple[str, str]:
    """Extract project name/path from the next pending mission in missions.md.

    Returns (project_name, project_path) — defaults to first project if no tag.
    """
    default_name = next(iter(projects))
    default_path = projects[default_name]

    if not INSTANCE.joinpath("missions.md").exists():
        return default_name, default_path

    content = INSTANCE.joinpath("missions.md").read_text()

    # Find first bullet under "## En attente" or "## Pending"
    in_pending = False
    for line in content.splitlines():
        stripped = line.strip()
        lower = stripped.lower()
        if stripped.startswith("## "):
            section = lower[3:].strip()
            in_pending = section in ("en attente", "pending")
            continue
        if in_pending and stripped.startswith("- "):
            # Found the first pending mission
            match = re.search(r"\[project:([a-zA-Z0-9_-]+)\]", stripped)
            if match:
                project_name = match.group(1)
                if project_name in projects:
                    return project_name, projects[project_name]
                print(f"[koan] Error: Mission references unknown project: {project_name}")
                print(f"[koan] Known projects: {', '.join(projects.keys())}")
                notify(f"Mission error: Unknown project '{project_name}'. Known: {', '.join(projects.keys())}")
                sys.exit(1)
            return default_name, default_path

    return default_name, default_path


def build_prompt(project_name: str, project_path: str, run_num: int) -> str:
    """Build the Claude prompt from system-prompt.md template."""
    template_path = KOAN_ROOT / "koan" / "system-prompt.md"
    template = template_path.read_text()

    replacements = {
        "{INSTANCE}": str(INSTANCE),
        "{PROJECT_PATH}": project_path,
        "{PROJECT_NAME}": project_name,
        "{RUN_NUM}": str(run_num),
        "{MAX_RUNS}": str(MAX_RUNS),
    }
    for placeholder, value in replacements.items():
        template = template.replace(placeholder, value)

    return template


QUOTA_PATTERNS = [
    "out of extra usage",
    "quota.*reached",
    "rate limit",
]
QUOTA_RE = re.compile("|".join(QUOTA_PATTERNS), re.IGNORECASE)


def check_quota_exhausted(output: str) -> str | None:
    """Check if Claude output indicates quota exhaustion.

    Returns reset info string if quota is exhausted, None otherwise.
    """
    if not QUOTA_RE.search(output):
        return None
    # Try to extract reset info
    match = re.search(r"resets.*", output)
    return match.group(0) if match else ""


def write_quota_journal(project_name: str, count: int, reset_info: str):
    """Write a quota exhaustion entry to the project journal."""
    now = datetime.now()
    journal_dir = INSTANCE / "journal" / now.strftime("%Y-%m-%d")
    journal_dir.mkdir(parents=True, exist_ok=True)
    journal_file = journal_dir / f"{project_name}.md"

    entry = (
        f"\n## Quota Exhausted — {now.strftime('%H:%M:%S')}\n\n"
        f"Claude quota reached after {count} runs (project: {project_name}). {reset_info}\n\n"
        f"Koan paused. Use `/resume` command via Telegram when ready to restart.\n"
    )

    with open(journal_file, "a") as f:
        f.write(entry)


def save_quota_marker(reset_info: str):
    """Save quota reset info for the /resume command."""
    marker = KOAN_ROOT / ".koan-quota-reset"
    marker.write_text(f"{reset_info}\n{int(time.time())}\n")


def git_commit_and_push(message: str):
    """Commit all instance changes and push (best-effort)."""
    try:
        subprocess.run(["git", "add", "-A"], cwd=str(INSTANCE), capture_output=True)
        # Check if there's anything to commit
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=str(INSTANCE), capture_output=True,
        )
        if result.returncode != 0:
            # There are staged changes
            subprocess.run(
                ["git", "commit", "-m", message],
                cwd=str(INSTANCE), capture_output=True,
            )
            subprocess.run(
                ["git", "push", "origin", "main"],
                cwd=str(INSTANCE), capture_output=True, timeout=30,
            )
    except Exception as e:
        print(f"[koan] Git error: {e}")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main():
    if not INSTANCE.is_dir():
        print("[koan] No instance/ directory found. Run: cp -r instance.example instance")
        sys.exit(1)

    projects = parse_projects()
    validate_projects(projects)

    print(f"[koan] Starting. Max runs: {MAX_RUNS}, interval: {INTERVAL}s")
    print(f"[koan] Projects: {', '.join(f'{n} ({p})' for n, p in projects.items())}")
    notify(f"Koan starting — {MAX_RUNS} max runs, {INTERVAL}s interval")

    count = 0

    while count < MAX_RUNS:
        # Check for stop request
        stop_file = KOAN_ROOT / ".koan-stop"
        if stop_file.exists():
            print("[koan] Stop requested.")
            stop_file.unlink()
            notify(f"Koan stopped on request after {count} runs.")
            break

        run_num = count + 1
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[koan] Run {run_num}/{MAX_RUNS} — {now}")
        notify(f"Run {run_num}/{MAX_RUNS} started")

        # Determine project for this run
        project_name, project_path = extract_mission_project(projects)
        print(f"[koan] Project: {project_name} ({project_path})")

        # Build prompt
        prompt = build_prompt(project_name, project_path, run_num)

        # Execute Claude
        try:
            result = subprocess.run(
                [
                    "claude", "-p", prompt,
                    "--allowedTools", "Bash,Read,Write,Glob,Grep,Edit",
                ],
                capture_output=False,  # Let output flow to terminal
                text=True,
                cwd=project_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            claude_output = result.stdout or ""
            # Print output to terminal
            if claude_output:
                print(claude_output)

            # Check for quota exhaustion
            reset_info = check_quota_exhausted(claude_output)
            if reset_info is not None:
                print(f"[koan] Quota reached. {reset_info}")
                write_quota_journal(project_name, count, reset_info)
                save_quota_marker(reset_info)
                git_commit_and_push(f"koan: quota exhausted {datetime.now().strftime('%Y-%m-%d-%H:%M')}")
                notify(
                    f"⚠️ Claude quota exhausted. {reset_info}\n\n"
                    f"Koan paused after {count} runs. "
                    f"Send /resume via Telegram when quota resets."
                )
                break

            # Report result
            if result.returncode == 0:
                notify(f"Run {run_num}/{MAX_RUNS} completed")
            else:
                notify(f"Run {run_num}/{MAX_RUNS} failed")

        except Exception as e:
            print(f"[koan] Claude execution error: {e}")
            notify(f"Run {run_num}/{MAX_RUNS} failed: {e}")

        # Commit instance results
        git_commit_and_push(f"koan: {datetime.now().strftime('%Y-%m-%d-%H:%M')}")

        count += 1

        if count < MAX_RUNS:
            print(f"[koan] Sleeping {INTERVAL}s...")
            time.sleep(INTERVAL)

    print(f"[koan] Session complete. {count} runs executed.")
    notify(f"Session complete — {count} runs executed")


if __name__ == "__main__":
    main()
