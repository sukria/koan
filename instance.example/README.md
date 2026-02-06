# Koan Instance

<p align="center">
  <img src="avatar.png" alt="Koan" width="200" />
</p>

<p align="center"><em>The agent proposes. The human decides.</em></p>

---

## What is this?

This directory is a **template** for a Koan instance. Copy it to `instance/` and customize it to create your own running agent.

## Identity

**Koan** — Semi-autonomous cognitive sparring partner.

Not an assistant. Not a bot. A collaborator that thinks while the human acts.

## Philosophy

> *The agent proposes. The human decides.*

- **Propose, never impose.** Suggests options, explains trade-offs.
- **Say no, properly.** Challenges ill-framed, premature, or needlessly complex requests.
- **Full traceability.** Every reasoning can be re-read, understood, criticized.

## Autonomous Modes

Budget-aware modes that adapt work intensity to remaining API quota:

| Mode | Budget | Behavior |
|------|--------|----------|
| **REVIEW** | < 15% | Read-only analysis, audit code, document findings |
| **IMPLEMENT** | 15–40% | Prototype fixes, write code, run tests |
| **DEEP** | >= 40% | Strategic deep work, thorough exploration |
| **WAIT** | < 5% | Write retrospective, then exit gracefully |

## Contents

| File | Purpose |
|------|---------|
| `soul.md` | Agent personality and behavioral rules |
| `config.yaml` | Instance configuration (tools, models, auto-merge) |
| `missions.md` | Task queue (pending / in progress / done) |
| `outbox.md` | Messages queued for Telegram delivery |
| `usage.md` | Claude API usage snapshot |
| `memory/` | Global summary + per-project learnings |
| `journal/` | Daily logs as `YYYY-MM-DD/project.md` |

## Setup

1. Copy this directory: `cp -r instance.example/ instance/`
2. Edit `soul.md` to define your agent's personality
3. Edit `config.yaml` with your paths, Telegram credentials, and preferences
4. Create a `projects.yaml` listing your projects (see `projects.sample.yaml`)
5. Create a `.env` file with API keys and other environment variables
6. Run `make run` to start the agent loop, `make awake` for the Telegram bridge
