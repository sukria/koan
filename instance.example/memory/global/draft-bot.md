# Anantys Pilot - Draft

Autonomous background agent that consumes unused Claude Max quota to work on Anantys improvements.

## Concept

- Runs on local machine via Claude Code CLI in a loop
- Pulls missions from a shared GitHub repo
- Communicates with the human via Telegram
- Maintains memory and personality across sessions
- Self-regulates based on pasted `/usage` data and a daily run cap

## Repo Structure

```
anantys-pilot/
  soul.md                # Personality, values, communication style
  config.yaml            # Budget, intervals, target project path
  missions.md            # Shared task queue (human pushes, bot pulls)
  usage.md               # Human pastes /usage here, bot parses it
  run.sh                 # Main launcher (while loop)
  bridge.py              # Telegram ↔ missions.md bridge
  journal/               # Daily reports (YYYY-MM-DD.md)
  memory/                # Cumulative session summaries for continuity
  templates/             # Structured prompts per mission type
```

## Mission Queue (missions.md)

Shared via git. Bot pulls every 5 min, human pushes from anywhere.

```markdown
## Pending
- [ ] Security audit of nextapi routes

## In Progress
- [~] Review of the StockAccountHolding model

## Done
- [x] 2026-01-30 - Bridge webhooks documentation
```

Flow: bot pulls → takes first Pending → marks In Progress → executes against anantys-back → writes report in journal/ → marks Done → commit+push.

## Usage Budget

No API to read Claude Max quota. Workaround:

1. Human pastes `/usage` output into `usage.md` (or sends it via Telegram)
2. Bot parses it at the start of each run
3. If estimated remaining budget is too low, bot stops
4. Safety net: hard cap of `MAX_RUNS` per day in `config.yaml`

## Telegram Integration

### Setup

1. Create bot via @BotFather → get token
2. Get chat_id from first message
3. Run `bridge.py` alongside `run.sh`

### Communication Flow

```
Human (Telegram) → bridge.py → missions.md → git push
Pilot (claude)   → outbox.md → bridge.py   → Human (Telegram)
```

### bridge.py

- Polls Telegram API for new messages
- Appends messages to missions.md as new Pending tasks
- Polls outbox.md for bot responses, sends them to Telegram
- ~50 lines Python, no framework needed

### Message Types

| From Human | Action |
|------------|--------|
| Free text | Added as new mission in missions.md |
| `/usage` paste | Written to usage.md |
| `/status` | Bot replies with current mission + remaining budget |
| `/stop` | Bot finishes current mission then stops |

| From Bot | Trigger |
|----------|---------|
| Mission started | When picking up a Pending task |
| Mission completed | With summary + link to journal entry |
| Budget warning | When approaching daily limit |
| Bot stopped | When budget exceeded or /stop received |

## Run Loop (run.sh)

```bash
#!/bin/bash
MAX_RUNS=20
INTERVAL=300  # 5 min
PROJECT_PATH="/path/to/anantys-back"
PILOT_REPO="/path/to/anantys-pilot"
count=0

while [ $count -lt $MAX_RUNS ]; do
  cd "$PILOT_REPO" && git pull --rebase origin main

  # Check usage budget
  claude -p "Read usage.md and config.yaml.
    If budget exceeded, write 'BUDGET_EXCEEDED' to /tmp/pilot-status and stop." \
    --allowedTools Read,Glob

  [ -f /tmp/pilot-status ] && grep -q "BUDGET_EXCEEDED" /tmp/pilot-status && break

  # Execute next mission
  claude -p "You are Pilot. Load soul.md for personality.
    Load memory/ for context continuity.
    Check missions.md for next Pending task.
    If no pending task, do autonomous exploration of $PROJECT_PATH.
    Execute the mission. Write report in journal/.
    Update missions.md status." \
    --allowedTools Bash,Read,Write,Glob,Grep,Edit

  # Commit results
  cd "$PILOT_REPO" && git add -A && \
    git commit -m "pilot: $(date +%Y-%m-%d-%H:%M)" && \
    git push origin main

  count=$((count + 1))
  sleep $INTERVAL
done
```

## Memory System

### journal/ (Daily Logs)

One file per day. Contains:
- Missions executed with summaries
- Discoveries and observations
- Decisions taken and rationale

### memory/ (Persistent Context)

- `summary.md` - Rolling summary of all past sessions (bot updates this)
- `learnings.md` - Accumulated knowledge about the codebase
- `human-preferences.md` - Noted preferences from conversations

Bot loads memory/ at each run start to maintain continuity.

## soul.md

Defines personality and operating rules. Written by human. Contains:
- Communication style and tone
- Decision-making principles
- What to prioritize (growth, retention, code quality)
- What to avoid
- Relationship to the human (collaborator, not servant)

## Autonomous Mode

When no missions are pending, bot can:
- Explore the codebase and note improvement ideas
- Run security audits
- Identify missing tests
- Review recent git changes
- Write findings to journal/ as "autonomous exploration" entries

These never modify anantys-back code directly. Bot proposes, human decides.

## What This Is NOT

- Not a CI/CD tool (doesn't deploy, doesn't run in prod)
- Not an always-on server (runs on local machine when launched)
- Not unsupervised code modification (bot writes reports, human acts)
- Not a replacement for direct Claude Code usage (complements it)

## Next Steps

1. Create `anantys-pilot` GitHub repo
2. Write `soul.md`
3. Write `config.yaml`
4. Implement `run.sh`
5. Create Telegram bot via @BotFather
6. Implement `bridge.py`
7. First test run with a simple mission
