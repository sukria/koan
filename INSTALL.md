# Installation

## Prerequisites

- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) installed and authenticated
- Python 3.8+
- A Telegram account

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

### 4. Set environment variables

```bash
cp env.example .env
$EDITOR .env    # Fill in your token, chat ID and project path
```

The `.env` file is gitignored — your secrets stay local.

### 5. Install dependencies

```bash
make setup
```

This creates a `.venv/` and installs Python dependencies from `requirements.txt`.

### 6. Run

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
| `KOAN_MAX_RUNS` | 20 | Maximum runs per session |
| `KOAN_INTERVAL` | 5 | Seconds between runs |
| `KOAN_BRIDGE_INTERVAL` | 3 | Telegram poll interval (seconds) |
| `KOAN_CHAT_TIMEOUT` | 180 | Claude CLI timeout for chat responses (seconds) |
| `KOAN_PROJECTS` | — | Multi-project config: `name:path;name2:path2` |
| `KOAN_PROJECT_PATH` | — | Single-project path (alternative to `KOAN_PROJECTS`) |
| `KOAN_GIT_SYNC_INTERVAL` | 5 | Runs between git sync checks |
