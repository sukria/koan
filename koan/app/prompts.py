"""Kōan — System prompt loader.

Loads prompt templates from koan/system-prompts/ and substitutes placeholders.
"""

from pathlib import Path

PROMPT_DIR = Path(__file__).parent.parent / "system-prompts"


def get_prompt_path(name: str) -> Path:
    """Return the full path to a system prompt file.

    Args:
        name: Prompt file name without .md extension (e.g. "chat", "pick-mission")

    Returns:
        Path to the prompt file (e.g. koan/system-prompts/chat.md)
    """
    return PROMPT_DIR / f"{name}.md"


def _substitute(template: str, kwargs: dict) -> str:
    """Replace {KEY} placeholders in a template string."""
    for key, value in kwargs.items():
        template = template.replace(f"{{{key}}}", str(value))
    return template


def load_prompt(name: str, **kwargs: str) -> str:
    """Load a system prompt template and substitute placeholders.

    Args:
        name: Prompt file name without .md extension (e.g. "chat", "format-telegram")
        **kwargs: Placeholder values to substitute. Keys map to {KEY} in the template.

    Returns:
        The prompt string with placeholders replaced.
    """
    template = get_prompt_path(name).read_text()
    return _substitute(template, kwargs)


def load_skill_prompt(skill_dir: Path, name: str, **kwargs: str) -> str:
    """Load a prompt from a skill's prompts/ directory.

    Looks for ``skill_dir/prompts/<name>.md`` first, then falls back to
    the global ``system-prompts/`` directory for safe incremental migration.

    Args:
        skill_dir: Path to the skill directory (e.g. ``skills/core/plan``).
        name: Prompt file name without .md extension.
        **kwargs: Placeholder values to substitute. Keys map to {KEY} in the template.

    Returns:
        The prompt string with placeholders replaced.
    """
    skill_prompt = skill_dir / "prompts" / f"{name}.md"
    if skill_prompt.exists():
        template = skill_prompt.read_text()
    else:
        template = get_prompt_path(name).read_text()
    return _substitute(template, kwargs)
