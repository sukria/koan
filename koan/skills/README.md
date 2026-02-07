# Koan Skills

Skills are self-contained plugins that add commands to Koan's Telegram interface. Each skill lives in its own directory and declares its commands through a `SKILL.md` file.

## Directory layout

```
skills/
  <scope>/
    <skill-name>/
      SKILL.md        # Required — metadata + optional prompt body
      handler.py      # Optional — Python handler
```

**Scope** is the top-level grouping directory. Built-in skills use `core`. Custom skills use any scope name — typically matching your team or project.

```
skills/
  core/               # Built-in (ships with Koan)
    status/
    idea/
    ...
  myteam/             # Custom scope (your own skills)
    deploy/
    oncall/
```

## SKILL.md format

Every skill needs a `SKILL.md` with YAML frontmatter:

```yaml
---
name: greet
scope: myteam
description: Send a greeting to the channel
version: 1.0.0
commands:
  - name: greet
    description: Say hello
    aliases: [hi, hello]
  - name: goodbye
    description: Say goodbye
    aliases: []
handler: handler.py
---
```

| Field | Required | Description |
|-------|----------|-------------|
| `name` | yes | Skill identifier (matches directory name) |
| `scope` | no | Defaults to parent directory name |
| `description` | no | One-line summary shown in `/help` |
| `version` | no | Semver, defaults to `0.0.0` |
| `commands` | no | List of commands this skill exposes |
| `handler` | no | Path to Python handler (relative to skill dir) |
| `worker` | no | Set to `true` for skills that block (call Claude, APIs, etc.) |

### Commands

A single skill can expose multiple commands. Each command has:

- **`name`** — what the user types after `/` (e.g., `/greet`)
- **`description`** — shown in help listings
- **`aliases`** — alternative names (e.g., `/hi` resolves to the `greet` command)

### Prompt-only skills (no handler)

If you omit `handler`, the markdown body after the frontmatter is sent to Claude as a prompt:

```yaml
---
name: haiku
commands:
  - name: haiku
    description: Write a haiku about the current project
    aliases: []
---

Write a haiku about the project described in the soul file.
Keep it relevant to recent work from the journal.
```

## Writing a handler

A handler is a Python module with a `handle(ctx)` function:

```python
def handle(ctx):
    """Handle the command. Return a string to send to Telegram."""
    if not ctx.args:
        return "Usage: /greet <name>"
    return f"Hello, {ctx.args}!"
```

### SkillContext

Every handler receives a `SkillContext` object:

| Attribute | Type | Description |
|-----------|------|-------------|
| `ctx.koan_root` | `Path` | Root koan directory |
| `ctx.instance_dir` | `Path` | `instance/` directory (runtime state) |
| `ctx.command_name` | `str` | The command that was invoked (e.g., `"greet"` or `"hi"`) |
| `ctx.args` | `str` | Everything after the command |
| `ctx.send_message` | `callable` | Send a message directly to Telegram |
| `ctx.handle_chat` | `callable` | Trigger a conversational Claude response |

### Return values

- Return a **string** — sent to Telegram as a reply
- Return **empty string** — signals "already handled, don't send anything"
- Return **None** — no message sent

### Conventions

- Use **lazy imports** inside `handle()` to avoid circular dependencies
- Access shared state via `ctx.instance_dir` (missions.md, soul.md, memory/, etc.)
- Use `fcntl.flock()` when reading/writing shared files concurrently
- Mark `worker: true` in SKILL.md if your handler blocks (API calls, subprocess, etc.)

## Loading custom skills

Koan loads skills from two locations:

1. **`koan/skills/`** — built-in core skills (shipped with the repo)
2. **`instance/skills/`** — custom skills (gitignored, instance-specific)

Skills from `instance/skills/` are merged into the registry at startup. If a custom command name collides with a core one, the last-loaded wins.

## Sharing skills via Git repos

A scope directory can be a **cloned Git repository**, letting a team share skills privately:

```bash
# Clone a shared skills repo into instance/skills/
cd instance/skills/
git clone git@github.com:myorg/koan-skills-ops.git ops
```

This produces:

```
instance/
  skills/
    ops/                        # scope = "ops"
      deploy/
        SKILL.md
        handler.py
      oncall/
        SKILL.md
        handler.py
```

Now everyone on the team gets `/deploy` and `/oncall` commands. Updates propagate with `git pull`.

### Organizing a shared skills repo

A skills repo is just a directory of skill subdirectories. Minimal structure:

```
koan-skills-ops/          # repo root = scope directory
  deploy/
    SKILL.md
    handler.py
  oncall/
    SKILL.md
  rollback/
    SKILL.md
    handler.py
  README.md               # optional, for humans
```

Each subdirectory follows the same `SKILL.md` + optional `handler.py` pattern. The scope name is determined by the directory name you clone into, not the repo name.

### Multiple shared repos

You can mix multiple repos under `instance/skills/`:

```
instance/
  skills/
    ops/                  # git@github.com:myorg/koan-skills-ops.git
      deploy/
      oncall/
    analytics/            # git@github.com:myorg/koan-skills-analytics.git
      report/
      dashboard/
    personal/             # your own local skills, no repo needed
      scratch/
```

Each top-level directory becomes its own scope. Skills are invocable directly (`/deploy`) or with explicit scope (`/ops.deploy`).

### Scoped commands

When command names conflict across scopes, use the fully qualified form:

```
/deploy              → first match wins
/ops.deploy          → explicitly from ops scope
/ops.deploy.rollback → subcommand form
```
