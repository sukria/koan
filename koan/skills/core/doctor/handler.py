"""Koan /doctor skill — system health diagnostics and self-repair.

Checks:
1. Required binaries (python, git, gh, claude/copilot CLI)
2. Instance directory structure (config.yaml, missions.md, soul.md, etc.)
3. Process health (PID files vs actual processes, stale PIDs)
4. Signal file state (stale .koan-pause, .koan-stop, etc.)
5. Projects configuration (projects.yaml validity, paths exist)
6. Bridge heartbeat freshness
7. Journal and memory directory health

With --fix: removes stale PID files, clears orphaned signal files.
"""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


# Status indicators
OK = "\u2705"      # green check
WARN = "\u26a0\ufe0f"    # warning
FAIL = "\u274c"    # red X
INFO = "\u2139\ufe0f"    # info


class CheckResult:
    """Result of a single health check."""

    __slots__ = ("icon", "label", "detail", "fixable", "fix_action")

    def __init__(self, icon, label, detail="", fixable=False, fix_action=None):
        self.icon = icon
        self.label = label
        self.detail = detail
        self.fixable = fixable
        self.fix_action = fix_action

    def format(self):
        line = f"{self.icon} {self.label}"
        if self.detail:
            line += f" — {self.detail}"
        return line


def handle(ctx):
    """Run health diagnostics. Pass --fix to auto-repair common issues."""
    fix_mode = "--fix" in (ctx.args or "")
    koan_root = ctx.koan_root
    instance_dir = ctx.instance_dir

    results = []
    results.extend(_check_binaries(koan_root))
    results.extend(_check_instance_structure(instance_dir))
    results.extend(_check_processes(koan_root))
    results.extend(_check_signal_files(koan_root))
    results.extend(_check_projects(koan_root))
    results.extend(_check_heartbeat(koan_root))
    results.extend(_check_journal_memory(instance_dir))

    # Count by severity
    counts = {OK: 0, WARN: 0, FAIL: 0, INFO: 0}
    for r in results:
        counts[r.icon] = counts.get(r.icon, 0) + 1

    lines = ["Koan Doctor"]

    # Apply fixes if requested
    fixes_applied = []
    if fix_mode:
        for r in results:
            if r.fixable and r.fix_action:
                try:
                    msg = r.fix_action()
                    if msg:
                        fixes_applied.append(msg)
                except Exception as e:
                    fixes_applied.append(f"Fix failed ({r.label}): {e}")

    # Section: results
    current_section = None
    for r in results:
        section = _section_for(r.label)
        if section != current_section:
            lines.append(f"\n{section}")
            current_section = section
        lines.append(f"  {r.format()}")

    # Summary
    lines.append(f"\n{counts[OK]} ok, {counts[WARN]} warn, {counts[FAIL]} fail")

    if fix_mode and fixes_applied:
        lines.append(f"\nFixes applied ({len(fixes_applied)}):")
        for f in fixes_applied:
            lines.append(f"  {f}")
    elif not fix_mode:
        fixable_count = sum(1 for r in results if r.fixable)
        if fixable_count:
            lines.append(f"\n{fixable_count} issue(s) auto-fixable — run /doctor --fix")

    return "\n".join(lines)


def _section_for(label):
    """Map check label to section name."""
    if label.startswith(("python", "git ", "gh ", "claude", "copilot")):
        return "Binaries"
    if label.startswith(("config.yaml", "missions.md", "soul.md", "outbox.md",
                         "memory/", "journal/", "instance")):
        return "Instance"
    if label.startswith(("run ", "awake ", "ollama ")):
        return "Processes"
    if label.startswith((".koan-")):
        return "Signal Files"
    if label.startswith(("projects.yaml", "project:")):
        return "Projects"
    if label.startswith("heartbeat"):
        return "Bridge Health"
    if label.startswith(("journal ", "memory ")):
        return "Storage"
    return "Other"


# ---------------------------------------------------------------------------
# Check categories
# ---------------------------------------------------------------------------

def _check_binaries(koan_root):
    """Check required external binaries are available."""
    results = []

    # Python version
    v = sys.version_info
    if v >= (3, 9):
        results.append(CheckResult(OK, f"python {v.major}.{v.minor}.{v.micro}"))
    else:
        results.append(CheckResult(FAIL, f"python {v.major}.{v.minor}.{v.micro}",
                                   "requires >= 3.9"))

    # git
    git_path = shutil.which("git")
    if git_path:
        try:
            ver = subprocess.run(
                ["git", "--version"], capture_output=True, text=True, timeout=5
            ).stdout.strip().replace("git version ", "")
            results.append(CheckResult(OK, f"git {ver}"))
        except Exception:
            results.append(CheckResult(OK, "git (version unknown)"))
    else:
        results.append(CheckResult(FAIL, "git", "not found in PATH"))

    # gh CLI
    gh_path = shutil.which("gh")
    if gh_path:
        try:
            ver = subprocess.run(
                ["gh", "--version"], capture_output=True, text=True, timeout=5
            ).stdout.strip().split("\n")[0]
            # Check auth status
            auth = subprocess.run(
                ["gh", "auth", "status"], capture_output=True, text=True, timeout=10
            )
            if auth.returncode == 0:
                results.append(CheckResult(OK, f"gh ({ver})", "authenticated"))
            else:
                results.append(CheckResult(WARN, f"gh ({ver})", "not authenticated"))
        except Exception:
            results.append(CheckResult(OK, "gh (version unknown)"))
    else:
        results.append(CheckResult(WARN, "gh", "not found in PATH — GitHub features disabled"))

    # CLI provider binary
    provider = _get_provider()
    if provider in ("claude", ""):
        _check_binary(results, "claude", "Claude Code CLI")
    elif provider == "copilot":
        _check_binary(results, "copilot", "GitHub Copilot CLI")

    return results


def _check_binary(results, name, description):
    """Check if a binary is in PATH."""
    path = shutil.which(name)
    if path:
        results.append(CheckResult(OK, f"{name}", f"{description} found"))
    else:
        results.append(CheckResult(FAIL, f"{name}", f"{description} not found in PATH"))


def _check_instance_structure(instance_dir):
    """Check instance/ directory has required files."""
    results = []

    required_files = [
        ("config.yaml", True),
        ("missions.md", True),
        ("soul.md", False),
        ("outbox.md", False),
    ]

    required_dirs = [
        "memory/",
        "journal/",
    ]

    for filename, critical in required_files:
        path = instance_dir / filename
        if path.exists():
            if path.stat().st_size == 0 and filename == "config.yaml":
                results.append(CheckResult(WARN, filename, "exists but empty"))
            else:
                results.append(CheckResult(OK, filename))
        else:
            icon = FAIL if critical else WARN
            results.append(CheckResult(icon, filename, "missing"))

    for dirname in required_dirs:
        path = instance_dir / dirname.rstrip("/")
        if path.exists() and path.is_dir():
            results.append(CheckResult(OK, dirname))
        else:
            results.append(CheckResult(WARN, dirname, "missing"))

    return results


def _check_processes(koan_root):
    """Check PID files and process liveness."""
    from app.pid_manager import check_pidfile, _pidfile_path, _read_pid, _is_process_alive

    results = []
    process_names = ["run", "awake"]

    # Include ollama only if provider requires it
    provider = _get_provider()
    if provider in ("local", "ollama"):
        process_names.append("ollama")

    for name in process_names:
        pidfile = _pidfile_path(koan_root, name)
        if not pidfile.exists():
            results.append(CheckResult(INFO, f"{name} process", "no PID file (not running)"))
            continue

        live_pid = check_pidfile(koan_root, name)
        file_pid = _read_pid(pidfile)

        if live_pid:
            results.append(CheckResult(OK, f"{name} process", f"running (PID {live_pid})"))
        elif file_pid:
            # PID file exists but process is dead = stale
            def make_fix(pf=pidfile, n=name):
                def fix():
                    pf.unlink(missing_ok=True)
                    return f"Removed stale PID file for {n} (was PID {_read_pid(pf) if pf.exists() else '?'})"
                return fix

            results.append(CheckResult(
                WARN, f"{name} process",
                f"stale PID file (PID {file_pid} is dead)",
                fixable=True,
                fix_action=make_fix()
            ))
        else:
            results.append(CheckResult(INFO, f"{name} process", "not running"))

    return results


def _check_signal_files(koan_root):
    """Check for potentially stale signal files."""
    from app.pid_manager import check_pidfile

    results = []
    run_alive = check_pidfile(koan_root, "run") is not None

    # .koan-stop — only problematic if run loop isn't running
    stop_file = koan_root / ".koan-stop"
    if stop_file.exists():
        if run_alive:
            results.append(CheckResult(INFO, ".koan-stop", "stop requested (runner still active)"))
        else:
            def fix_stop(f=stop_file):
                def fix():
                    f.unlink(missing_ok=True)
                    return "Removed orphaned .koan-stop"
                return fix
            results.append(CheckResult(
                WARN, ".koan-stop",
                "present but runner is not running (orphaned)",
                fixable=True, fix_action=fix_stop()
            ))

    # .koan-pause — check if runner is alive
    pause_file = koan_root / ".koan-pause"
    pause_reason_file = koan_root / ".koan-pause-reason"
    if pause_file.exists():
        if run_alive:
            reason = ""
            if pause_reason_file.exists():
                try:
                    reason = pause_reason_file.read_text().strip().split("\n")[0]
                except OSError:
                    pass
            detail = f"paused ({reason})" if reason else "paused"
            results.append(CheckResult(INFO, ".koan-pause", detail))
        else:
            def fix_pause(pf=pause_file, prf=pause_reason_file):
                def fix():
                    pf.unlink(missing_ok=True)
                    prf.unlink(missing_ok=True)
                    return "Removed orphaned .koan-pause"
                return fix
            results.append(CheckResult(
                WARN, ".koan-pause",
                "present but runner is not running (orphaned)",
                fixable=True, fix_action=fix_pause()
            ))

    # .koan-restart
    restart_file = koan_root / ".koan-restart"
    if restart_file.exists():
        if not run_alive:
            def fix_restart(f=restart_file):
                def fix():
                    f.unlink(missing_ok=True)
                    return "Removed orphaned .koan-restart"
                return fix
            results.append(CheckResult(
                WARN, ".koan-restart",
                "present but runner is not running (orphaned)",
                fixable=True, fix_action=fix_restart()
            ))

    return results


def _check_projects(koan_root):
    """Check projects.yaml validity and project paths."""
    results = []

    projects_path = koan_root / "projects.yaml"
    if not projects_path.exists():
        results.append(CheckResult(INFO, "projects.yaml", "not found (using env vars or defaults)"))
        return results

    try:
        from app.projects_config import load_projects_config, get_projects_from_config
        config = load_projects_config(str(koan_root))
        if config is None:
            results.append(CheckResult(WARN, "projects.yaml", "empty or invalid"))
            return results

        results.append(CheckResult(OK, "projects.yaml", "valid"))

        projects = get_projects_from_config(config)
        for name, path in projects:
            if Path(path).exists():
                results.append(CheckResult(OK, f"project:{name}", path))
            else:
                results.append(CheckResult(WARN, f"project:{name}", f"path not found: {path}"))

    except ValueError as e:
        results.append(CheckResult(FAIL, "projects.yaml", f"parse error: {e}"))
    except Exception as e:
        results.append(CheckResult(WARN, "projects.yaml", f"load error: {e}"))

    return results


def _check_heartbeat(koan_root):
    """Check Telegram bridge heartbeat freshness."""
    results = []

    heartbeat_file = koan_root / ".koan-heartbeat"
    if not heartbeat_file.exists():
        results.append(CheckResult(INFO, "heartbeat", "no heartbeat file (bridge not started)"))
        return results

    try:
        ts = float(heartbeat_file.read_text().strip())
        age = time.time() - ts
        age_str = _format_duration(age)

        if age <= 60:
            results.append(CheckResult(OK, "heartbeat", f"fresh ({age_str} ago)"))
        elif age <= 300:
            results.append(CheckResult(WARN, "heartbeat", f"stale ({age_str} ago)"))
        else:
            results.append(CheckResult(FAIL, "heartbeat", f"very stale ({age_str} ago)"))
    except (ValueError, OSError):
        results.append(CheckResult(WARN, "heartbeat", "file unreadable"))

    return results


def _check_journal_memory(instance_dir):
    """Check journal and memory directories for health."""
    results = []

    # Journal directory size
    journal_dir = instance_dir / "journal"
    if journal_dir.exists():
        entry_count = sum(1 for _ in journal_dir.rglob("*.md"))
        dir_size = _dir_size_mb(journal_dir)
        if dir_size > 100:
            results.append(CheckResult(
                WARN, f"journal size", f"{dir_size:.0f} MB ({entry_count} entries) — consider cleanup"))
        else:
            results.append(CheckResult(OK, f"journal size", f"{dir_size:.1f} MB ({entry_count} entries)"))

    # Memory directory
    memory_dir = instance_dir / "memory"
    if memory_dir.exists():
        file_count = sum(1 for _ in memory_dir.rglob("*") if _.is_file())
        dir_size = _dir_size_mb(memory_dir)
        results.append(CheckResult(OK, f"memory size", f"{dir_size:.1f} MB ({file_count} files)"))

    # Missions.md Done section size
    missions_file = instance_dir / "missions.md"
    if missions_file.exists():
        try:
            content = missions_file.read_text()
            total_lines = content.count("\n")
            # Rough heuristic: Done section is usually the biggest
            if total_lines > 500:
                results.append(CheckResult(
                    WARN, f"missions.md", f"{total_lines} lines — Done section may need pruning"))
            else:
                results.append(CheckResult(OK, f"missions.md", f"{total_lines} lines"))
        except OSError:
            pass

    return results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_provider():
    """Get the configured CLI provider name."""
    try:
        from app.provider import get_provider_name
        return get_provider_name()
    except Exception:
        return "claude"


def _dir_size_mb(path):
    """Calculate directory size in MB."""
    total = 0
    try:
        for f in path.rglob("*"):
            if f.is_file():
                try:
                    total += f.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total / (1024 * 1024)


def _format_duration(seconds):
    """Format seconds into human-readable duration."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.0f}m"
    if seconds < 86400:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"
