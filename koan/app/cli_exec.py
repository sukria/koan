"""CLI execution helpers — secure prompt passing via temp files.

Prevents prompts from leaking into ``ps`` process listings by writing
them to a temporary file (``0o600``) and redirecting that file as the
subprocess stdin.  The ``-p`` argument visible in ``ps`` becomes the
short placeholder ``@stdin`` instead of the full prompt text.

Providers that consume stdin for the prompt (making it unavailable for
the agent's own tool calls) skip this mechanism and pass the prompt
directly as a ``-p`` argument.
"""

import os
import subprocess
import tempfile
from typing import Callable, List, Optional, Tuple

STDIN_PLACEHOLDER = "@stdin"


def _uses_stdin_passing() -> bool:
    """Check if the current provider supports stdin-based prompt passing.

    Copilot CLI consumes stdin when reading the ``@stdin`` prompt,
    leaving nothing for its internal agent's tool calls (e.g.
    ``cat /dev/stdin``).  For these providers we pass the prompt
    directly as a ``-p`` argument instead.
    """
    try:
        from app.provider import get_provider_name
        return get_provider_name() not in ("copilot",)
    except Exception:
        return True


def prepare_prompt_file(cmd: List[str]) -> Tuple[List[str], Optional[str]]:
    """Extract the ``-p`` prompt from *cmd* and write it to a secure temp file.

    Returns ``(modified_cmd, temp_file_path)``.  If no ``-p`` argument is
    found, it already equals :data:`STDIN_PLACEHOLDER`, or the current
    provider does not support stdin-based prompt passing, returns
    ``(cmd, None)`` unchanged.
    """
    if not _uses_stdin_passing():
        return cmd, None

    try:
        idx = cmd.index("-p")
    except ValueError:
        return cmd, None

    if idx + 1 >= len(cmd):
        return cmd, None

    prompt = cmd[idx + 1]
    if prompt == STDIN_PLACEHOLDER:
        return cmd, None

    fd, path = tempfile.mkstemp(suffix=".md", prefix="koan-prompt-")
    try:
        os.write(fd, prompt.encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(path, 0o600)

    new_cmd = cmd.copy()
    new_cmd[idx + 1] = STDIN_PLACEHOLDER
    return new_cmd, path


def _cleanup_prompt_file(path: Optional[str]) -> None:
    """Silently remove a temp prompt file if it exists."""
    if path:
        try:
            os.unlink(path)
        except OSError:
            pass


def _get_provider_env() -> dict:
    """Get environment overrides from the current provider.

    Returns an empty dict if no overrides are needed or if the provider
    can't be resolved (safe fallback — preserves existing behavior).
    """
    try:
        from app.provider import get_provider
        return get_provider().get_env()
    except Exception:
        return {}


def _inject_provider_env(kwargs: dict) -> None:
    """Merge provider environment overrides into subprocess kwargs.

    Only injects when the caller hasn't already set ``env=`` and the
    provider has non-empty overrides. When no overrides exist, kwargs
    is left untouched (preserving exact current behavior).
    """
    if "env" not in kwargs:
        provider_env = _get_provider_env()
        if provider_env:
            kwargs["env"] = {**os.environ, **provider_env}


def run_cli(cmd, **kwargs) -> subprocess.CompletedProcess:
    """Run a CLI command with the prompt passed via temp-file stdin.

    Drop-in replacement for ``subprocess.run(cmd, stdin=DEVNULL, ...)``.
    Injects provider environment overrides (e.g., ANTHROPIC_BASE_URL)
    when the caller hasn't set ``env=`` explicitly.
    """
    _inject_provider_env(kwargs)
    cmd, prompt_path = prepare_prompt_file(cmd)
    if prompt_path:
        try:
            with open(prompt_path) as f:
                kwargs.pop("stdin", None)
                kwargs["stdin"] = f
                return subprocess.run(cmd, **kwargs)
        finally:
            _cleanup_prompt_file(prompt_path)
    else:
        kwargs.setdefault("stdin", subprocess.DEVNULL)
        return subprocess.run(cmd, **kwargs)


def popen_cli(
    cmd, **kwargs
) -> Tuple[subprocess.Popen, Callable[[], None]]:
    """Start a :class:`~subprocess.Popen` process with the prompt via temp-file stdin.

    Returns ``(proc, cleanup)`` where *cleanup()* **must** be called after
    the process exits to close the file handle and delete the temp file.

    Injects provider environment overrides (e.g., ANTHROPIC_BASE_URL)
    when the caller hasn't set ``env=`` explicitly.
    """
    _inject_provider_env(kwargs)
    cmd, prompt_path = prepare_prompt_file(cmd)
    if prompt_path:
        stdin_file = open(prompt_path)  # noqa: SIM115
        kwargs.pop("stdin", None)
        kwargs["stdin"] = stdin_file
        try:
            proc = subprocess.Popen(cmd, **kwargs)
        except Exception:
            stdin_file.close()
            _cleanup_prompt_file(prompt_path)
            raise

        def cleanup():
            stdin_file.close()
            _cleanup_prompt_file(prompt_path)

        return proc, cleanup
    else:
        kwargs.setdefault("stdin", subprocess.DEVNULL)
        return subprocess.Popen(cmd, **kwargs), lambda: None
