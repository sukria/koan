# Installation

## Quick Start (Wizard)

The easiest way to set up Kōan is with the interactive wizard:

```bash
git clone https://github.com/sukria/koan.git
cd koan
make install
```

This launches a web-based wizard that guides you through Telegram setup, project configuration, and validation. If you prefer manual setup, continue below.

## Prerequisites

- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) installed and authenticated
- Python 3.8+
- A Telegram account (for the Telegram bridge)

## Recommended

- GitHub cli `gh` setup and has one or more identidies to access to your repositories

## Manual Setup

### 1. Clone and create your instance

```bash
git clone https://github.com/sukria/koan.git
cd koan
cp -r instance.example instance
```

The `instance/` directory is your private data — it's gitignored and never pushed to the Kōan repo. You can version it in a separate private repo if you want persistence.

### 2. Create a Telegram bot

1. Open Telegram, message [@BotFather](https://t.me/BotFather)
2. Send `/newbot`, follow the prompts (choose a display name, then a username ending in `Bot`)
3. BotFather gives you an HTTP API token — copy it and store it safely
4. Open a chat with your new bot in Telegram and send any message (e.g. "hello")
5. Get your chat ID:

```bash
# Replace YOUR_TOKEN with your actual bot token
curl -s "https://api.telegram.org/botYOUR_TOKEN/getUpdates" | python3 -m json.tool
```

Look for `"chat": {"id": 123456789, ...}` in the response — that number is your chat ID.

> **Security note:** Your bot token grants full control of the bot. Never commit it to a public repo. If you accidentally leak it, revoke it immediately with `/revoke` in BotFather.

### 3. Set environment variables

```bash
cp env.example .env
```

Edit `.env` and fill in the **required** Telegram credentials:

```bash
KOAN_TELEGRAM_TOKEN=123456789:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
KOAN_TELEGRAM_CHAT_ID=987654321
```

The `.env` file is gitignored — your secrets stay local.

### 4. Configure projects

**Recommended:** Use `projects.yaml` at your koan root:

```bash
cp projects.example.yaml projects.yaml
```

Edit `projects.yaml`:

```yaml
defaults:
  git_auto_merge:
    enabled: false
    base_branch: "main"
    strategy: "squash"

projects:
  myapp:
    path: "/Users/yourname/myapp"
  backend:
    path: "/Users/yourname/backend"
```

Each project only needs a `path`. All other fields are optional and inherit from `defaults`.

**Fallback:** You can also use the `KOAN_PROJECTS` env var in `.env`:

```bash
KOAN_PROJECTS=myapp:/Users/yourname/myapp;backend:/Users/yourname/backend
```

If `projects.yaml` exists, the env var is ignored. On first startup, Kōan will auto-migrate env vars to `projects.yaml`.

### 5. Set up project memory (optional but recommended)

If you're using multi-project mode, create a memory folder for each project:

```bash
# Copy the template for each project
cp -r instance/memory/projects/_template instance/memory/projects/myapp
```

Edit the files in `instance/memory/projects/myapp/` to describe your project's architecture. This helps Kōan understand your codebase faster.

### 6. Customize your agent (optional)

```bash
$EDITOR instance/soul.md    # Write your agent's personality
```

### 7. Install dependencies

```bash
make setup
```

This creates a `.venv/` and installs Python dependencies.

### 8. Run

```bash
# Terminal 1: Telegram bridge
make awake

# Terminal 2: Agent loop
make run
```

## Troubleshooting

### "KOAN_ROOT environment variable is not set"

This happens if you run Python scripts directly instead of using `make` commands.

**Fix:** Always use the Makefile targets (`make run`, `make awake`, `make test`). They set `KOAN_ROOT` automatically.

If you need to run scripts directly:
```bash
KOAN_ROOT=/path/to/koan python3 koan/app/awake.py
```

### "Project 'example' not found" or similar

Your `missions.md` file references a project name that doesn't match your configuration.

**Fix:** Either:
1. Remove project tags from missions: `- My task` instead of `- [project:example] My task`
2. Or ensure the project name matches your `projects.yaml` config

### Telegram bot not responding

1. Verify your token: `curl "https://api.telegram.org/botYOUR_TOKEN/getMe"` should return your bot info
2. Verify your chat ID: Make sure `KOAN_TELEGRAM_CHAT_ID` matches the ID from the `getUpdates` call
3. Check `make awake` is running without errors

### Claude CLI errors

Make sure Claude Code CLI is installed and authenticated:
```bash
claude --version   # Should show version
claude             # Should start interactive mode (exit with /exit)
```

## Preventing macOS sleep

Kōan runs in the background — if your Mac goes to sleep, everything stops. You need to prevent sleep while keeping the screen off.

### Using `caffeinate` (recommended)

The simplest approach — run this before launching Kōan:

```bash
# Prevent system sleep indefinitely (Ctrl+C to stop)
caffeinate -s &
```

Or wrap your entire session:

```bash
caffeinate -s make run
```

The `-s` flag prevents system sleep even when the display is off. The display will still turn off normally (saving power), but the CPU keeps running.

### Using `pmset` (persistent)

To make the setting survive reboots:

```bash
# Disable sleep on AC power
sudo pmset -c sleep 0
sudo pmset -c disablesleep 1
```

To revert:

```bash
sudo pmset -c sleep 10
sudo pmset -c disablesleep 0
```

### macOS System Settings

Alternatively: **System Settings → Energy → Prevent automatic sleeping when the display is off** — toggle ON.

> **Tip:** `caffeinate -s` is the safest option — it only affects the current session and stops automatically when you kill it.

## Environment Variables Reference

### Required

| Variable | Description |
|----------|-------------|
| `KOAN_TELEGRAM_TOKEN` | Telegram bot token from @BotFather |
| `KOAN_TELEGRAM_CHAT_ID` | Your Telegram chat ID |

> **Note:** Project paths are configured in `projects.yaml` (see step 4 above). The `KOAN_PROJECTS` env var is supported as a fallback.

### Optional

| Variable | Default | Description |
|----------|---------|-------------|
| `KOAN_ROOT` | (auto-detected) | Path to koan repo (set by Makefile) |
| `KOAN_EMAIL` | — | Git author email for koan's commits |
| `KOAN_BRIDGE_INTERVAL` | 3 | Telegram poll interval (seconds) |
| `KOAN_CHAT_TIMEOUT` | 180 | Claude CLI timeout for chat responses (seconds) |
| `KOAN_GIT_SYNC_INTERVAL` | 5 | Runs between git sync checks |

> **Note:** `max_runs_per_day` and `interval_seconds` are now configured in `config.yaml`, not `.env`.
> The env vars `KOAN_MAX_RUNS` and `KOAN_INTERVAL` are deprecated and ignored.

## Multi-Project Setup

Kōan can work on up to 50 projects, rotating between them.

Configure in `projects.yaml`:

```yaml
projects:
  myapp:
    path: "/Users/yourname/myapp"
  backend:
    path: "/Users/yourname/backend"
    git_auto_merge:
      base_branch: "staging"
```

Per-project `git_auto_merge` overrides are defined directly in `projects.yaml`. See `projects.example.yaml` for the full schema.

For each project, create a memory folder:
```bash
cp -r instance/memory/projects/_template instance/memory/projects/myapp
cp -r instance/memory/projects/_template instance/memory/projects/backend
```

Tag missions with the target project:
```
- [project:myapp] Add user authentication
- [project:backend] Fix CSS bug on homepage
```
