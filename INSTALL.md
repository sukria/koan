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
- A Telegram account or a Slack workspace (for messaging)

## Recommended

- GitHub cli `gh` setup and has one or more identities to access to your repositories

## LLM Providers

Koan supports multiple LLM providers. Claude Code CLI is the default and
most capable option. You can also use GitHub Copilot or a local LLM
server.

| Provider | Setup Guide | Best For |
|----------|------------|----------|
| **Claude Code** (default) | [docs/provider-claude.md](docs/provider-claude.md) | Full-featured agent with best reasoning |
| **GitHub Copilot** | [docs/provider-copilot.md](docs/provider-copilot.md) | Teams with existing Copilot subscriptions |
| **Local LLM** | [docs/provider-local.md](docs/provider-local.md) | Offline use, privacy, zero API cost |

You can mix providers per project — see the individual guides for
per-project configuration via `projects.yaml`.

## Manual Setup

### 1. Clone and create your instance

```bash
git clone https://github.com/sukria/koan.git
cd koan
cp -r instance.example instance
```

The `instance/` directory is your private data — it's gitignored and never pushed to the Kōan repo. You can version it in a separate private repo if you want persistence.

### 2. Set up a messaging platform

Kōan supports **Telegram** (default) and **Slack** for communication. Follow the setup guide for your preferred platform:

| Platform | Setup Guide | Best For |
|----------|-------------|----------|
| **Telegram** (default) | [docs/messaging-telegram.md](docs/messaging-telegram.md) | Quick setup, works from any network |
| **Slack** | [docs/messaging-slack.md](docs/messaging-slack.md) | Team collaboration, workspace integration |

Both platforms are fully supported with the same feature set. Telegram is recommended for personal use (simpler setup), while Slack is ideal for team environments.

### 3. Set environment variables

```bash
cp env.example .env
```

Edit `.env` and fill in the credentials for your chosen messaging provider.

**For Telegram:**
```bash
KOAN_TELEGRAM_TOKEN=123456789:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
KOAN_TELEGRAM_CHAT_ID=987654321
```

**For Slack:**
```bash
KOAN_MESSAGING_PROVIDER=slack
KOAN_SLACK_BOT_TOKEN=xoxb-your-bot-token
KOAN_SLACK_APP_TOKEN=xapp-your-app-token
KOAN_SLACK_CHANNEL_ID=C01234ABCD
```

The `.env` file is gitignored — your secrets stay local. See the provider-specific setup guides above for detailed instructions on obtaining these credentials.

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

### Messaging provider not responding

**For Telegram:**
1. Verify your token: `curl "https://api.telegram.org/botYOUR_TOKEN/getMe"` should return your bot info
2. Verify your chat ID: Make sure `KOAN_TELEGRAM_CHAT_ID` matches the ID from the `getUpdates` call
3. Check `make awake` is running without errors

**For Slack:**
1. Verify Socket Mode is enabled and the app token starts with `xapp-`
2. Check that the bot is invited to the channel (`/invite @koan`)
3. Review the logs for connection errors (`make logs`)

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

### Required (Provider-Specific)

**For Telegram** (default):

| Variable | Description |
|----------|-------------|
| `KOAN_TELEGRAM_TOKEN` | Telegram bot token from @BotFather |
| `KOAN_TELEGRAM_CHAT_ID` | Your Telegram chat ID |

**For Slack**:

| Variable | Description |
|----------|-------------|
| `KOAN_MESSAGING_PROVIDER` | Must be set to `slack` |
| `KOAN_SLACK_BOT_TOKEN` | Bot User OAuth Token (starts with `xoxb-`) |
| `KOAN_SLACK_APP_TOKEN` | App-Level Token (starts with `xapp-`) |
| `KOAN_SLACK_CHANNEL_ID` | Channel ID where Kōan operates |

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
