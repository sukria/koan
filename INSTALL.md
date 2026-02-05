# Installation

## Prerequisites

- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) installed and authenticated
- Python 3.8+
- A Telegram or Slack account (Telegram is the default)

## Setup

### 1. Clone and create your instance

```bash
git clone https://github.com/sukria/koan.git
cd koan
cp -r instance.example instance
```

The `instance/` directory is your private data — it's gitignored and never pushed to the Kōan repo. You can version it in a separate private repo if you want persistence.

### 2. Edit your instance

```bash
$EDITOR instance/config.yaml    # Set your project path
$EDITOR instance/soul.md        # Write your agent's personality
```

### 3. Create a Telegram bot

1. Open Telegram, message [@BotFather](https://t.me/BotFather)
2. Send `/newbot`, follow the prompts (choose a display name, then a username ending in `Bot`)
3. BotFather gives you an HTTP API token — copy it and store it safely
4. Open a chat with your new bot in Telegram and send any message (e.g. "hello")
5. Get your chat ID:

```bash
curl -s https://api.telegram.org/bot<TOKEN>/getUpdates | python3 -m json.tool
```

Look for `"chat": {"id": 123456789, ...}` in the response — that number is your chat ID.

> **Security note:** Your bot token grants full control of the bot. Never commit it to a public repo. If you accidentally leak it, revoke it immediately with `/revoke` in BotFather.

### 3b. Alternative: Use Slack instead of Telegram

If you prefer Slack, set `messaging_provider: "slack"` in `instance/config.yaml` (or `KOAN_MESSAGING_PROVIDER=slack` in `.env`).

1. Create a Slack App at [api.slack.com/apps](https://api.slack.com/apps) → "Create New App" → "From Scratch"
2. Under **OAuth & Permissions**, add these Bot Token Scopes: `chat:write`, `channels:history`, `channels:read`
3. Install the app to your workspace — copy the **Bot User OAuth Token** (`xoxb-...`)
4. Create a channel for Koan (e.g., `#koan`) and invite the bot (`/invite @YourBot`)
5. Get the channel ID: right-click the channel → "View channel details" → copy the ID at the bottom (`C...`)

Set these env vars in your `.env`:

```bash
KOAN_MESSAGING_PROVIDER=slack
KOAN_SLACK_BOT_TOKEN=xoxb-your-token
KOAN_SLACK_CHANNEL_ID=C01234ABCDE
```

### 4. Configure your projects

```bash
cp projects.sample.yaml projects.yaml
$EDITOR projects.yaml    # Add your project names and paths
```

The `projects.yaml` file is gitignored — your paths stay local. There is no limit on the number of projects.

### 5. Set environment variables

```bash
cp env.example .env
$EDITOR .env    # Fill in your token and chat ID
```

The `.env` file is gitignored — your secrets stay local.

### 6. Install dependencies

```bash
make setup
```

This creates a `.venv/` and installs Python dependencies from `requirements.txt`.

### 7. Run

```bash
# Terminal 1: Telegram bridge
make awake

# Terminal 2: Agent loop
make run
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

## Optional environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `KOAN_MESSAGING_PROVIDER` | `telegram` | Messaging backend: `telegram` or `slack` |
| `KOAN_MAX_RUNS` | 25 | Maximum runs per session |
| `KOAN_INTERVAL` | 5 | Seconds between runs |
| `KOAN_BRIDGE_INTERVAL` | 3 | Messaging poll interval (seconds) |
| `KOAN_CHAT_TIMEOUT` | 180 | Claude CLI timeout for chat responses (seconds) |
| `KOAN_GIT_SYNC_INTERVAL` | 5 | Runs between git sync checks |
