"""Shared GitHub CLI (gh) wrapper for all Koan modules.

Centralizes all `gh` CLI interactions so that consumers don't reinvent
subprocess plumbing.  Auth is handled externally by ``github_auth.py``
which sets ``GH_TOKEN`` — this module has no auth logic.
"""

import subprocess


def run_gh(*args, cwd=None, timeout=30, stdin_data=None):
    """Run a ``gh`` CLI command and return stripped stdout.

    Args:
        *args: Arguments passed after ``gh`` (e.g. ``"pr", "view", "1"``).
        cwd: Working directory for the subprocess.
        timeout: Seconds before the command is killed.
        stdin_data: Optional string passed to the process via stdin.

    Returns:
        Stripped stdout string.

    Raises:
        RuntimeError: If the ``gh`` command exits with a non-zero code.
    """
    cmd = ["gh", *args]
    result = subprocess.run(
        cmd, input=stdin_data,
        capture_output=True, text=True, timeout=timeout, cwd=cwd,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"gh failed: {' '.join(cmd[:4])}... — {result.stderr[:300]}"
        )
    return result.stdout.strip()


def pr_create(title, body, draft=True, base=None, repo=None, head=None, cwd=None):
    """Create a pull request via ``gh pr create``.

    Args:
        title: PR title.
        body: PR body (markdown).
        draft: If True (default), create a draft PR.
        base: Target branch (omit to let ``gh`` pick the default).
        repo: Repository in ``owner/repo`` format (omit to use local repo).
        head: Branch containing the changes (omit to use current branch).
        cwd: Working directory (must be inside a git repo).

    Returns:
        The URL of the newly created PR.
    """
    args = ["pr", "create", "--title", title, "--body", body]
    if draft:
        args.append("--draft")
    if base:
        args.extend(["--base", base])
    if repo:
        args.extend(["--repo", repo])
    if head:
        args.extend(["--head", head])
    return run_gh(*args, cwd=cwd)


def issue_create(title, body, labels=None, cwd=None):
    """Create a GitHub issue via ``gh issue create``.

    Args:
        title: Issue title.
        body: Issue body (markdown).
        labels: Optional list of label names.
        cwd: Working directory (must be inside a git repo).

    Returns:
        The URL of the newly created issue.
    """
    args = ["issue", "create", "--title", title, "--body", body]
    if labels:
        args.extend(["--label", ",".join(labels)])
    return run_gh(*args, cwd=cwd)


def api(endpoint, method="GET", jq=None, input_data=None, cwd=None,
        extra_args=None):
    """Call ``gh api`` for lower-level GitHub API access.

    Args:
        endpoint: API path (e.g. ``repos/owner/repo/pulls/1/comments``).
        method: HTTP method (default GET).
        jq: Optional jq filter applied server-side.
        input_data: If provided, passed via stdin (``-F body=@-``).
        cwd: Working directory.
        extra_args: Additional arguments for ``gh api``.

    Returns:
        Stripped stdout string.
    """
    args = ["api", endpoint]
    if method and method.upper() != "GET":
        args.extend(["-X", method.upper()])
    if jq:
        args.extend(["--jq", jq])
    if extra_args:
        args.extend(extra_args)
    if input_data is not None:
        args.extend(["-F", "body=@-"])

    return run_gh(*args, cwd=cwd, stdin_data=input_data)
