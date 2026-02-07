# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is Kōan

Kōan is an autonomous background agent that uses idle Claude API quota to work on local projects. It runs as a continuous loop, pulling missions from a shared file, executing them via Claude Code CLI, and communicating progress via Telegram. Philosophy: "The agent proposes. The human decides." — no unsupervised code modifications.

## Commands

```bash
make setup          # Create venv, install dependencies
make run            # Start main agent loop
make awake          # Start Telegram bridge (fast-response polling)
make dashboard      # Start Flask web dashboard (port 5001)
make test           # Run full test suite (pytest)
make say m="..."    # Send test message as if from Telegram
make clean          # Remove venv
```

Run a single test file:
```bash
KOAN_ROOT=/tmp/test-koan .venv/bin/pytest koan/tests/test_missions.py -v
```

## Test suite

- **`KOAN_ROOT` must be set** when running tests. Many modules (`utils.py`, `awake.py`) check for `KOAN_ROOT` at import time and raise `SystemExit` if it's missing. Use `KOAN_ROOT=/tmp/test-koan` (or any path) as a prefix: `KOAN_ROOT=/tmp/test-koan .venv/bin/pytest koan/tests/ -v`
- Never call Claude (subprocess) in tests. Mock `format_and_send` which invokes Claude CLI for message formatting.
- With `runpy.run_module()` (CLI tests), patch both `app.<module>.format_and_send` **and** `app.notify.format_and_send` — `runpy` re-executes the module so the import-level binding escapes the first patch.
- When `load_dotenv()` would reload env vars from `.env` (defeating `monkeypatch.delenv`), patch `app.notify.load_dotenv` too.

## Architecture

Two parallel processes run independently:

- **`awake.py`** (Telegram bridge): Polls Telegram every 3s. Classifies messages as "chat" (instant Claude reply) or "mission" (queued to `missions.md`). Flushes `outbox.md` messages back to Telegram.
- **`run.sh`** (agent loop): Picks pending missions from `missions.md`, executes via Claude Code CLI, writes journal entries and reports. Supports multi-project rotation.

Communication between processes happens through shared files in `instance/` with atomic writes (`utils.atomic_write()` using temp file + rename + `fcntl.flock()`).

### Key modules (`koan/app/`)

- **`missions.py`** — Single source of truth for `missions.md` parsing (sections: Pending / In Progress / Done; French equivalents also accepted). Missions can be tagged `[project:name]`.
- **`utils.py`** — File locking (thread + file locks), config loading, atomic writes
- **`memory_manager.py`** — Per-project memory isolation and compaction
- **`usage_tracker.py`** — Budget tracking; decides autonomous mode (REVIEW/IMPLEMENT/DEEP/WAIT) based on quota percentage
- **`git_sync.py`** / **`git_auto_merge.py`** — Branch tracking, sync awareness, configurable auto-merge
- **`recover.py`** — Crash recovery for stale in-progress missions
- **`notify.py`** — Telegram notification helper
- **`github.py`** — Centralized `gh` CLI wrapper (`run_gh()`, `pr_create()`, `issue_create()`)
- **`prompts.py`** — System prompt loader; `load_prompt()` for `koan/system-prompts/*.md`, `load_skill_prompt()` for skill-bound prompts
- **`prompt_builder.py`** — Agent prompt assembly for `run.sh` (replaces bash sed/string concatenation)
- **`pause_manager.py`** — Pause state management (`.koan-pause` / `.koan-pause-reason` files)

### Skills system (`koan/skills/`)

Extensible command plugin system. Each skill lives in `skills/<scope>/<skill-name>/` with a `SKILL.md` (YAML frontmatter defining commands, aliases, metadata) and an optional `handler.py`.

- **`skills.py`** — Registry that discovers SKILL.md files, parses frontmatter (custom lite YAML parser, no PyYAML), maps commands/aliases to skills, and dispatches execution.
- **Core skills** live in `koan/skills/core/` (cancel, chat, idea, journal, language, list, magic, mission, plan, pr, priority, rebase, reflect, sparring, status, verbose)
- **Custom skills** loaded from `instance/skills/<scope>/` — each scope directory can be a cloned Git repo for team sharing.
- **Handler pattern**: `def handle(ctx: SkillContext) -> Optional[str]` — return string for Telegram reply, empty string for "already handled", None for no message.
- **`worker: true`** flag in SKILL.md marks blocking skills (Claude calls, API requests) that run in a background thread.
- **Prompt-only skills**: omit `handler`, put prompt text after the frontmatter — sent to Claude directly.
- See `koan/skills/README.md` for the full authoring guide.

### Instance directory

`instance/` (gitignored, copy from `instance.example/`) holds all runtime state:
- `missions.md` — Task queue
- `outbox.md` — Bot → Telegram message queue
- `config.yaml` — Per-instance configuration (tools, auto-merge rules)
- `soul.md` — Agent personality definition
- `memory/` — Global summary + per-project learnings/context
- `journal/` — Daily logs organized as `YYYY-MM-DD/project.md`

## Conventions

- Claude always creates **`koan/*` branches**, never commits to main
- Environment config via `.env` file and `KOAN_*` variables (e.g., `KOAN_PROJECTS=name:path;name2:path2`)
- Multi-project support: up to 5 projects, each with isolated memory under `memory/projects/{name}/`
- Tests use temp directories and isolated env vars — no real Telegram calls
- `system-prompt.md` defines the Claude agent's identity, priorities, and autonomous mode rules
- **No inline prompts in Python code** — LLM prompts MUST be extracted to `.md` files. Skill-bound prompts go in `skills/<scope>/<name>/prompts/` and are loaded via `load_skill_prompt()`. Infrastructure prompts used by `koan/app/` modules stay in `koan/system-prompts/` and are loaded via `load_prompt()`.
- **System prompts must be generic** — Never reference specific instance details like owner names in system prompts. Use generic terms like "your human" instead of personal names. Prompts are in English; instance-specific personality and language preferences come from `soul.md`.
