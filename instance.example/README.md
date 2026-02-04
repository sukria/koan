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

## Operating Modes

| Mode | Purpose |
|------|---------|
| **THINK** | Clarify, structure, reveal blind spots (default) |
| **BUILD** | Ship fast, ship clean, no noise |
| **REVIEW** | Challenge quality, coherence, durability |
| **DECIDE** | Explicit trade-offs, clear recommendation |
| **CHALLENGE** | Deliberate intellectual friction |
| **SILENT** | Minimal response — do no harm |

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
4. Create a `.env` file with `KOAN_PROJECTS` and other environment variables
5. Run `make run` to start the agent loop, `make awake` for the Telegram bridge
