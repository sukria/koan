# Kōan

![License](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)
![Python](https://img.shields.io/badge/python-3.8+-blue.svg)
![Status](https://img.shields.io/badge/status-alpha-orange.svg)
![Claude Code](https://img.shields.io/badge/Claude-Code%20CLI-blueviolet.svg)

An autonomous background agent that uses idle Claude Max quota to work on your projects.

Kōan runs as a loop on your local machine: it pulls missions from a shared repo, executes them via Claude Code CLI, writes reports, and communicates with you via Telegram.

**The agent proposes. The human decides.** No unsupervised code modifications.

## How It Works

```
┌─────────────┐     ┌──────────────┐     ┌──────────────────┐
│  Telegram    │◄───►│  awake.py    │◄───►│ instance/        │
│  (Human)     │     │  (bridge)    │     │   missions.md    │
└─────────────┘     └──────────────┘     │   outbox.md      │
                                         └────────┬─────────┘
                           ┌─────────────────────►│
                           │                      │
                    ┌──────┴───────┐     ┌────────▼─────────┐
                    │ dashboard.py │     │   run.sh          │
                    │ (local web)  │     │  (Claude CLI      │
                    └──────────────┘     │    loop)          │
                                         └────────┬─────────┘
                                                  │
                                         ┌────────▼─────────┐
                                         │  Your Projects   │
                                         │  (koan/* only)   │
                                         └──────────────────┘
```

Two parallel processes:
- **`make awake`** — Telegram bridge. Polls every 3s, routes messages (chat → instant Claude reply, missions → queued), flushes outbox.
- **`make run`** — Agent loop. Picks missions, executes via Claude Code CLI, writes journal & reports. Supports multi-project rotation.

Optional:
- **`make dashboard`** — Local web UI (Flask, port 5001). Status, missions CRUD, chat, journal viewer.

## Features

- **Multi-project support** — Work on up to 5 projects in parallel with per-project memory isolation
- **Crash recovery** — Stale "In Progress" missions are automatically moved back to "Pending" on restart
- **Daily reports** — Telegram digest at session boundaries (morning recap or evening summary)
- **Mission notifications** — Real-time Telegram updates at each mission lifecycle stage
- **Memory management** — Scoped summaries, automatic compaction, learnings dedup
- **Clean shutdown** — Signal traps for graceful exit with notification
- **82 tests** — `make test` runs the full suite

## Repo Structure

```
koan/
  README.md
  INSTALL.md
  LICENSE
  BACKLOG.md                  # Roadmap and user stories
  Makefile                    # Build & run targets
  koan/                       # Application code
    run.sh                    #   Main loop launcher
    awake.py                  #   Telegram bridge (poll, classify, route)
    notify.py                 #   Telegram notification helper
    dashboard.py              #   Flask web dashboard
    daily_report.py           #   Daily digest generator
    recover.py                #   Crash recovery (stale mission reset)
    extract_mission.py        #   Mission extraction from missions.md
    mission_summary.py        #   Post-mission journal summary
    memory_manager.py         #   Memory scope isolation & compaction
    migrate_memory.py         #   Memory structure migration
    system-prompt.md          #   Claude prompt template
    requirements.txt          #   Python dependencies
    templates/                #   Dashboard Jinja2 templates
    conftest.py               #   Shared test fixtures
    test_*.py                 #   Test files (pytest)
  instance.example/           # Template — copy to instance/ to start
    soul.md                   #   Agent personality
    missions.md               #   Task queue
    outbox.md                 #   Bot → Telegram queue
    memory/                   #   Persistent context
      summary.md              #     Rolling session summaries
      global/                 #     Cross-project (preferences, strategy)
      projects/               #     Per-project learnings
    journal/                  #   Daily logs (YYYY-MM-DD/project.md)
  instance/                   # Your data (gitignored)
```

**Design principle:** App code (`koan/`) is generic and open source. Instance data (`instance/`) is private to each user. Fork the repo, write your own soul.

## Quick Start

```bash
git clone https://github.com/sukria/koan.git
cd koan
cp -r instance.example instance
cp env.example .env
# Edit .env with your Telegram bot token and project path
make setup
make awake  # Terminal 1
make run    # Terminal 2
```

See [INSTALL.md](INSTALL.md) for detailed setup instructions.

## Make Targets

| Target | Description |
|--------|-------------|
| `make setup` | Create venv and install dependencies |
| `make awake` | Start Telegram bridge |
| `make run` | Start agent loop |
| `make dashboard` | Start local web dashboard |
| `make test` | Run test suite |
| `make say m="..."` | Send a message as if from Telegram |

## License

AGPL-3.0 — See [LICENSE](LICENSE).
