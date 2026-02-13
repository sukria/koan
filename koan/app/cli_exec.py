"""CLI execution helpers — secure prompt passing via temp files.

Prevents prompts from leaking into ``ps`` process listings by writing
them to a temporary file (``0o600``) and redirecting that file as the
subprocess stdin.  The ``-p`` argument visible in ``ps`` becomes the
short placeholder ``@stdin`` instead of the full prompt text.
"""

import os
import subprocess
import tempfile
from typing import Callable, List, Optional, Tuple

STDIN_PLACEHOLDER = "@stdin"


def prepare_prompt_file(cmd: List[str]) -> Tuple[List[str], Optional[str]]:
    """Extract the ``-p`` prompt from *cmd* and write it to a secure temp file.

    Returns ``(modified_cmd, temp_file_path)``.  If no ``-p`` argument is
    found or it already equals :data:`STDIN_PLACEHOLDER`, returns
    ``(cmd, None)`` unchanged.
    """
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


def run_cli(cmd, **kwargs) -> subprocess.CompletedProcess:
    """Run a CLI command with the prompt passed via temp-file stdin.

    Drop-in replacement for ``subprocess.run(cmd, stdin=DEVNULL, ...)``.
    """
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
    """
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
