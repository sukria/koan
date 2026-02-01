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
│  (Human)     │     │              │     │   missions.md    │
└─────────────┘     └──────────────┘     │   outbox.md      │
                                         └────────┬─────────┘
                                                  │
                                         ┌────────▼─────────┐
                                         │   run.sh         │
                                         │  (Claude CLI     │
                                         │    loop)         │
                                         └────────┬─────────┘
                                                  │
                                         ┌────────▼─────────┐
                                         │  Your Code       │
                                         │  (read-only)     │
                                         └──────────────────┘
```

## Repo Structure

```
koan/
  README.md
  INSTALL.md
  LICENSE
  Makefile                  # Build & run targets
  koan/                     # Application code
    run.sh                  #   Main loop launcher
    awake.py                #   Telegram bridge
    notify.py               #   Telegram notification helper
    system-prompt.md        #   Claude prompt template
    requirements.txt        #   Python dependencies
  instance.example/         # Template — copy to instance/ to start
    soul.md                 #   Agent personality
    config.yaml             #   Budget, paths, Telegram config
    missions.md             #   Task queue
    usage.md                #   Pasted /usage data
    outbox.md               #   Bot → Telegram queue
    mission-report.md       #   Report template
    memory/                 #   Persistent context
    journal/                #   Daily logs
  instance/                 # Your data (gitignored)
```

**Design principle:** App code (`koan/`) is generic and open source. Instance data (`instance/`) is private to each user. Fork the repo, write your own soul.

See [INSTALL.md](INSTALL.md) for setup instructions.

## License

AGPL-3.0 — See [LICENSE](LICENSE).
