"""Koan -- Skills system.

Loads skills from SKILL.md files, parses YAML frontmatter, and dispatches
commands to the appropriate handler (Python function or Claude prompt).

Directory layout:
    skills/<scope>/<skill-name>/SKILL.md     — skill definition
    skills/<scope>/<skill-name>/handler.py   — optional Python handler

SKILL.md format:
    ---
    name: status
    description: Show Koan status
    version: 1.0.0
    commands:
      - name: status
        description: Quick status overview
        aliases: [st]
      - name: ping
        description: Check run loop liveness
    handler: handler.py   # optional, defaults to prompt-based
    ---

    # Prompt body (used when no handler.py)
    ...
"""

import importlib.util
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


@dataclass
class SkillCommand:
    """A single command exposed by a skill."""

    name: str
    description: str = ""
    aliases: List[str] = field(default_factory=list)


@dataclass
class Skill:
    """A loaded skill definition."""

    name: str
    scope: str
    description: str = ""
    version: str = "0.0.0"
    commands: List[SkillCommand] = field(default_factory=list)
    handler_path: Optional[Path] = None
    prompt_body: str = ""
    skill_dir: Optional[Path] = None
    worker: bool = False

    @property
    def qualified_name(self) -> str:
        return f"{self.scope}.{self.name}"

    def has_handler(self) -> bool:
        return self.handler_path is not None and self.handler_path.exists()


# ---------------------------------------------------------------------------
# SKILL.md parser
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)", re.DOTALL)


def _parse_yaml_lite(text: str) -> Dict[str, Any]:
    """Minimal YAML-subset parser for SKILL.md frontmatter.

    Handles:
      - key: value (strings, numbers)
      - key: [item1, item2] (inline lists)
      - commands: (block list of dicts with - name:/description:/aliases:)

    This avoids requiring PyYAML as a dependency for the core skills system.
    """
    result: Dict[str, Any] = {}
    lines = text.split("\n")
    i = 0

    while i < len(lines):
        line = lines[i].rstrip()

        # Skip empty lines and comments
        if not line or line.startswith("#"):
            i += 1
            continue

        # Top-level key: value
        match = re.match(r"^(\w[\w_-]*)\s*:\s*(.*)", line)
        if not match:
            i += 1
            continue

        key = match.group(1)
        value = match.group(2).strip()

        if key == "commands" and not value:
            # Block list of command dicts
            commands = []
            i += 1
            current_cmd: Dict[str, Any] = {}
            while i < len(lines):
                cline = lines[i].rstrip()
                if not cline.startswith(" ") and not cline.startswith("\t"):
                    break
                cline = cline.strip()
                if cline.startswith("- name:"):
                    if current_cmd:
                        commands.append(current_cmd)
                    current_cmd = {"name": cline[7:].strip()}
                elif cline.startswith("description:"):
                    current_cmd["description"] = cline[12:].strip()
                elif cline.startswith("aliases:"):
                    aliases_str = cline[8:].strip()
                    current_cmd["aliases"] = _parse_inline_list(aliases_str)
                i += 1
            if current_cmd:
                commands.append(current_cmd)
            result["commands"] = commands
            continue

        # Inline list: [item1, item2]
        if value.startswith("[") and value.endswith("]"):
            result[key] = _parse_inline_list(value)
        else:
            result[key] = value

        i += 1

    return result


def _parse_inline_list(s: str) -> List[str]:
    """Parse [item1, item2] into a list of strings."""
    s = s.strip()
    if s.startswith("["):
        s = s[1:]
    if s.endswith("]"):
        s = s[:-1]
    if not s.strip():
        return []
    return [item.strip().strip("'\"") for item in s.split(",") if item.strip()]


def parse_skill_md(path: Path) -> Optional[Skill]:
    """Parse a SKILL.md file into a Skill object.

    Returns None if the file can't be parsed.
    """
    try:
        content = path.read_text()
    except OSError:
        return None

    match = _FRONTMATTER_RE.match(content)
    if not match:
        return None

    frontmatter_text = match.group(1)
    prompt_body = match.group(2).strip()

    meta = _parse_yaml_lite(frontmatter_text)

    if "name" not in meta:
        return None

    # Parse commands
    commands = []
    for cmd_data in meta.get("commands", []):
        if isinstance(cmd_data, dict) and "name" in cmd_data:
            commands.append(
                SkillCommand(
                    name=cmd_data["name"],
                    description=cmd_data.get("description", ""),
                    aliases=cmd_data.get("aliases", []),
                )
            )

    # Resolve handler path (always record declared path; has_handler() checks existence)
    handler_path = None
    handler_name = meta.get("handler", "")
    if handler_name:
        handler_path = path.parent / handler_name

    skill_dir = path.parent

    # Parse worker flag
    worker = meta.get("worker", "").lower() in ("true", "yes", "1")

    return Skill(
        name=meta["name"],
        scope=meta.get("scope", skill_dir.parent.name),
        description=meta.get("description", ""),
        version=meta.get("version", "0.0.0"),
        commands=commands,
        handler_path=handler_path,
        prompt_body=prompt_body,
        skill_dir=skill_dir,
        worker=worker,
    )


# ---------------------------------------------------------------------------
# Skill Registry
# ---------------------------------------------------------------------------

class SkillRegistry:
    """Discovers and manages skills from a directory tree.

    Expected layout:
        skills_dir/<scope>/<skill-name>/SKILL.md
    """

    def __init__(self, skills_dir: Optional[Path] = None):
        self._skills: Dict[str, Skill] = {}  # key: "scope.name"
        self._command_map: Dict[str, Skill] = {}  # key: command name -> skill
        if skills_dir and skills_dir.is_dir():
            self._discover(skills_dir)

    def _discover(self, skills_dir: Path) -> None:
        """Scan directory tree for SKILL.md files."""
        for skill_md in sorted(skills_dir.rglob("SKILL.md")):
            skill = parse_skill_md(skill_md)
            if skill is None:
                continue
            self._register(skill)

    def _register(self, skill: Skill) -> None:
        """Register a skill and build command lookup."""
        key = skill.qualified_name
        self._skills[key] = skill

        # Map each command name and alias to this skill
        for cmd in skill.commands:
            self._command_map[cmd.name] = skill
            for alias in cmd.aliases:
                self._command_map[alias] = skill

    def get(self, scope: str, name: str) -> Optional[Skill]:
        return self._skills.get(f"{scope}.{name}")

    def get_by_qualified_name(self, qualified: str) -> Optional[Skill]:
        return self._skills.get(qualified)

    def find_by_command(self, command_name: str) -> Optional[Skill]:
        """Find a skill that handles the given command name."""
        return self._command_map.get(command_name)

    def list_all(self) -> List[Skill]:
        return list(self._skills.values())

    def list_by_scope(self, scope: str) -> List[Skill]:
        return [s for s in self._skills.values() if s.scope == scope]

    def scopes(self) -> List[str]:
        return sorted(set(s.scope for s in self._skills.values()))

    def __len__(self) -> int:
        return len(self._skills)

    def resolve_scoped_command(self, text: str) -> Optional[Tuple["Skill", str, str]]:
        """Resolve a scoped command like 'anantys.review' or 'core.status.ping'.

        Returns:
            (skill, command_name, args) tuple, or None if no match.
        """
        parts = text.split(None, 1)
        ref = parts[0]
        args = parts[1] if len(parts) > 1 else ""

        segments = ref.split(".")

        if len(segments) < 2:
            return None

        scope = segments[0]
        skill_name = segments[1]
        subcommand = segments[2] if len(segments) > 2 else skill_name

        skill = self.get(scope, skill_name)
        if skill is None:
            return None

        return skill, subcommand, args

    def __contains__(self, qualified_name: str) -> bool:
        return qualified_name in self._skills


# ---------------------------------------------------------------------------
# Skill execution
# ---------------------------------------------------------------------------

@dataclass
class SkillContext:
    """Context passed to skill handlers."""

    koan_root: Path
    instance_dir: Path
    command_name: str = ""
    args: str = ""
    send_message: Optional[Callable[[str], Any]] = None
    handle_chat: Optional[Callable[[str], Any]] = None


def execute_skill(skill: Skill, ctx: SkillContext) -> Optional[str]:
    """Execute a skill and return the response text.

    Handler-based skills: imports handler.py and calls handle(ctx).
    Prompt-based skills: returns the prompt body (caller sends to Claude).

    Returns:
        Response text, or None if execution failed.
    """
    if skill.has_handler():
        return _execute_handler(skill, ctx)
    if skill.prompt_body:
        return _execute_prompt(skill, ctx)
    return None


def _execute_handler(skill: Skill, ctx: SkillContext) -> Optional[str]:
    """Load and execute a Python handler."""
    handler_path = skill.handler_path
    if handler_path is None:
        return None

    try:
        spec = importlib.util.spec_from_file_location(
            f"skill_handler_{skill.qualified_name}",
            str(handler_path),
        )
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        handle_fn = getattr(module, "handle", None)
        if handle_fn is None:
            return None

        return handle_fn(ctx)
    except Exception as e:
        return f"Skill error ({skill.qualified_name}): {e}"


def _execute_prompt(skill: Skill, ctx: SkillContext) -> Optional[str]:
    """Return the prompt body for Claude-based execution.

    The caller is responsible for sending this to Claude.
    """
    return skill.prompt_body


# ---------------------------------------------------------------------------
# Default skills directory
# ---------------------------------------------------------------------------

def get_default_skills_dir() -> Path:
    """Return the default skills directory (koan/skills/)."""
    return Path(__file__).parent.parent / "skills"


def build_registry(extra_dirs: Optional[List[Path]] = None) -> SkillRegistry:
    """Build a registry from the default skills dir + optional extra dirs.

    Args:
        extra_dirs: Additional directories to scan (e.g., instance/skills/).
    """
    registry = SkillRegistry(get_default_skills_dir())

    if extra_dirs:
        for d in extra_dirs:
            if d.is_dir():
                for skill_md in sorted(d.rglob("SKILL.md")):
                    skill = parse_skill_md(skill_md)
                    if skill:
                        registry._register(skill)

    return registry
