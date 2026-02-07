# Koan

![License](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)
![Python](https://img.shields.io/badge/python-3.10+-blue.svg)
![Status](https://img.shields.io/badge/status-alpha-orange.svg)
![Claude Code](https://img.shields.io/badge/Claude-Code%20CLI-blueviolet.svg)
![Tests](https://img.shields.io/badge/tests-615-green.svg)

<p align="center">
  <img src="instance.example/avatar.png" alt="Kōan" width="200" />
</p>

An autonomous background agent that uses idle Claude Max quota to work on your projects.

Koan runs as a continuous loop on your local machine: it pulls missions from a shared file, executes them via Claude Code CLI, writes reports, and communicates with you via Telegram.

**The agent proposes. The human decides.** No unsupervised code modifications.

## How It Works

```
┌─────────────┐     ┌──────────────┐     ┌──────────────────┐
│  Telegram    │◄───►│  awake.py    │◄───►│ instance/        │
│  (Human)     │     │  (bridge)    │     │   missions.md    │
└─────────────┘     └──────────────┘     │   outbox.md      │
                                         │   config.yaml    │
                                         └────────┬─────────┘
                          ┌─────────────────────►│
                          │                      │
                   ┌──────┴───────┐     ┌────────▼─────────┐
                   │ dashboard.py │     │   run.sh          │
                   │ (local web)  │     │  (agent loop)     │
                   └──────────────┘     └────────┬─────────┘
                                                 │
                                        ┌────────▼─────────┐
                                        │  Your Projects   │
                                        │  (koan/* only)   │
                                        └──────────────────┘
```

Two parallel processes:
- **`make awake`** — Telegram bridge. Polls every 3s, classifies messages (chat → instant Claude reply, mission → queued to `missions.md`), formats all outbox messages through Claude with personality context, flushes to Telegram.
- **`make run`** — Agent loop. Picks missions (smart picker with rotation awareness), executes via Claude Code CLI, writes journal & reports. Supports multi-project rotation and configurable model selection.

Optional:
- **`make dashboard`** — Local web UI (Flask, port 5001). Status, missions CRUD, chat, journal viewer.

## Features

- **Multi-project support** — Up to 5 projects with per-project memory isolation, round-robin rotation
- **Smart mission picker** — Claude-based prioritization when 3+ missions across 2+ projects; direct extraction otherwise
- **Crash recovery** — Stale "In Progress" missions auto-reset on restart
- **Auto-merge** — Configurable per-project merge of koan/* branches (squash/merge/rebase strategies)
- **Git sync awareness** — Branch tracking, merge detection, sync status reported to agent between runs
- **Budget-aware modes** — DEEP (>40%), IMPLEMENT (15-40%), REVIEW (<15%), WAIT (<5%) based on API quota
- **Model configuration** — Per-role model selection (haiku for lightweight, sonnet for missions) with fallback
- **Outbox formatting** — All messages to Telegram pass through Claude with soul + personality + memory context
- **Memory management** — Scoped summaries, automatic compaction, journal archival (3-tier lifecycle), learnings cap
- **Health monitoring** — Heartbeat tracking for the Telegram bridge, alerts on stale state
- **Daily reports** — Telegram digest at session boundaries
- **Personality evolution** — Acquired traits tracked across sessions
- **Clean shutdown** — Signal traps for graceful exit with notification
- **615 tests** — `make test` runs the full suite (~95% coverage)

## Repo Structure

```
koan/
  README.md
  CLAUDE.md                     # Agent coding guidelines
  INSTALL.md                    # Setup instructions
  LICENSE
  Makefile                      # Build & run targets
  env.example                   # Template for .env
  koan/                         # Application package
    run.sh                      #   Main loop orchestrator (bash)
    system-prompts/             #   Claude prompt templates
      agent.md                  #     Mission execution prompt
      chat.md                   #     Telegram chat prompt
      contemplative.md          #     Pause mode prompt
      dashboard-chat.md         #     Dashboard chat prompt
      format-telegram.md        #     Outbox formatting prompt
      pick-mission.md           #     Mission selection prompt
    app/                        #   Python modules
      awake.py                  #     Telegram bridge (poll, classify, route)
      dashboard.py              #     Flask web dashboard
      missions.py               #     Mission parsing (single source of truth)
      pick_mission.py           #     Smart mission picker with rotation
      notify.py                 #     Telegram notification helper
      format_outbox.py          #     Claude-based message formatting
      daily_report.py           #     Daily digest generator
      recover.py                #     Crash recovery (stale mission reset)
      extract_mission.py        #     Mission extraction wrapper
      mission_summary.py        #     Post-mission journal summary
      memory_manager.py         #     Memory scope isolation & compaction
      migrate_memory.py         #     Memory structure migration
      git_sync.py               #     Branch tracking & sync awareness
      git_auto_merge.py         #     Configurable auto-merge for koan/* branches
      health_check.py           #     Heartbeat monitoring
      usage_tracker.py          #     Budget tracking & mode selection
      usage_estimator.py        #     Cost estimation
      prompts.py                #     Prompt building helpers
      send_retrospective.py     #     End-of-session retrospective
      utils.py                  #     Shared utilities (locks, config, atomic writes)
    templates/                  #   Dashboard Jinja2 templates
    tests/                      #   Test suite (pytest)
    requirements.txt            #   Python dependencies
  instance.example/             # Template — copy to instance/ to start
    soul.md                     #   Agent personality definition
    missions.md                 #   Task queue (Pending / In Progress / Done)
    outbox.md                   #   Bot → Telegram message queue
    config.yaml                 #   Per-instance config (models, auto-merge, tools)
    memory/                     #   Persistent context
      summary.md                #     Rolling session summaries
      global/                   #     Cross-project (preferences, strategy)
      projects/                 #     Per-project learnings
    journal/                    #   Daily logs (YYYY-MM-DD/project.md)
  instance/                     # Your data (gitignored)
```

**Design principle:** App code (`koan/`) is generic and open source. Instance data (`instance/`) is private to each user. Fork the repo, write your own soul.

## Quick Start

```bash
git clone https://github.com/sukria/koan.git
cd koan
cp -r instance.example instance
cp env.example .env
# Edit .env with your Telegram bot token and project paths
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
| `make dashboard` | Start local web dashboard (port 5001) |
| `make test` | Run test suite |
| `make say m="..."` | Send a message as if from Telegram |
| `make migrate` | Run memory structure migration |
| `make errand-run` | Start agent loop with `caffeinate` (prevents macOS sleep) |
| `make errand-awake` | Start Telegram bridge with `caffeinate` |
| `make clean` | Remove virtualenv |

## Configuration

Instance configuration lives in `instance/config.yaml`:

```yaml
# Model selection per role
models:
  mission: null          # Default (sonnet)
  chat: null             # Default (sonnet)
  lightweight: haiku     # For quick tasks (picking, formatting)
  contemplative: haiku   # Pause mode reflections
  review_mode: null      # Read-only audit mode

# Auto-merge rules for koan/* branches
git_auto_merge:
  default:
    enabled: true
    strategy: squash
    delete_after_merge: true
  rules:
    - pattern: "koan/*"
      base_branch: main
```

## Security

**This project is alpha software.** It is not designed for public-facing deployment.

Koan exposes local services (dashboard, Telegram bridge) that have **no authentication or access control**. It should only be run on a trusted local network.

Known security considerations:
- Telegram messages are passed to Claude with tool access — prompt injection risk exists
- Dashboard has no auth (safe on localhost, do not expose)
- Bot authentication relies on Telegram chat_id only

Do not expose any Koan service to the public internet. Use a VPN or SSH tunnel for remote access.

## License

AGPL-3.0 — See [LICENSE](LICENSE).
