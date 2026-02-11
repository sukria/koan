# Contributing to Kōan

Thanks for your interest in contributing to Kōan! This document covers the basics you need to get started.

## Getting Started

1. **Fork & clone** the repository
2. Run `make setup` to create the virtual environment and install dependencies
3. Copy `instance.example/` to `instance/` and configure your `.env` file (see [INSTALL.md](INSTALL.md))
4. Run `make test` to verify everything works

## Commit Messages

All commits **must** follow the format defined in [`docs/commit-conventions.md`](docs/commit-conventions.md).

Quick summary:
```
<type>(<scope>): <subject>

Case <PROJ-123 or #123 or N/A>:

<description>

Changelog: <customer-facing change or empty>
```

- Use semantic type prefixes: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`, `ci`, `build`, `revert`
- Include a case ID (JIRA or GitHub issue) — use `Case: N/A:` if none exists
- **Changelog is mandatory** — even if empty

A commit message template is available:
```bash
git config commit.template .github/commit-msg-template.md
```

## Branching

- Create branches with the `koan/` prefix (or your configured `branch_prefix`)
- Never commit directly to `main`
- One branch per feature or fix

## Pull Requests

- Always create **draft PRs** until the work is ready for review
- Link the relevant issue or case ID in the PR description
- Ensure `make test` passes before requesting review

## Code Style

- Python 3.12+
- Follow existing patterns in the codebase
- Read [CLAUDE.md](CLAUDE.md) for architecture and conventions
- **No inline prompts in Python** — extract LLM prompts to `.md` files

## Testing

- Run the full suite: `make test`
- Run a single file: `KOAN_ROOT=/tmp/test-koan .venv/bin/pytest koan/tests/test_file.py -v`
- Add tests for new features — see `koan/tests/` for patterns
- Never call external APIs in tests — mock subprocess calls and network requests

## AI Contributors

Both human contributors and AI agents (like Kōan itself) follow these conventions. The commit format, branching strategy, and code style apply equally to all contributors.

## Questions?

Open an issue on GitHub or check the existing documentation:
- [README.md](README.md) — Project overview
- [INSTALL.md](INSTALL.md) — Setup instructions
- [CLAUDE.md](CLAUDE.md) — Architecture and coding guidelines
- [docs/commit-conventions.md](docs/commit-conventions.md) — Commit message format
