# Kōan Roadmap

Progress tracking via user stories. Updated between sessions.

---

## Epic 1 — Fast Telegram Flow (high priority)

> Goal: respond to the human in seconds, no longer wait for a run to complete.

### US 1.1 — Separate chat and missions in awake.py
- **As a** Telegram user
- **I want** to receive a conversational response in a few seconds
- **So that** I don't wait 10-20 min for a mission to complete
- [x] Refactor `awake.py`: detect if the message is a quick question or a mission
- [x] For quick questions: call `claude -p` with short context (soul.md + memory/summary.md)
- [x] For missions: write to `missions.md` and reply immediately "Mission received"
- [x] Reduce Telegram poll interval to 2-5s for chat (default 3s)

### US 1.2 — Push notifications from workers
- **As a** Telegram user
- **I want** to be notified automatically when a mission starts, completes, or fails
- **So that** I can track progress without asking `/status`
- [x] Create `notify.py`: `send_telegram(message)` function reusable by all workers
- [x] Integrate calls in `run.sh`: notification at start and end of each mission
- [x] Handle failure cases: notification with error message
- [ ] Send a short summary of the mission report on completion (via outbox)

### US 1.3 — Continuous outbox flush
- **As** Kōan
- **I want** my outbox messages sent immediately
- **So that** I don't wait for the next poll cycle
- [x] Modify `awake.py` to flush `outbox.md` at each poll cycle (3s)
- [x] Clear `outbox.md` after sending (truncate, not delete)
- [x] Handle concurrency: file lock (`fcntl.flock`) if a worker writes at the same time

---

## Epic 2 — Multi-project

> Goal: work on N projects in parallel (e.g. koan + anantys-back + anantys-front).

### US 2.1 — Missions per project
- **As a** Human
- **I want** to assign a mission to a specific project
- **So that** Kōan works on the right codebase
- [ ] Define the list of projects and their paths in `.env` (`KOAN_PROJECT_PATH=`)
- [ ] Don't be tight to a single project (`KOAN_PROJECT_PATH=`) but up to 5: `KOAN_PROJECTS=`
      - format: `KOAN_PROJECTS=name:PATH;name2:PATH2;...`
- [ ] Add a `project:<name>` field in the missions format (missions.md)
- [ ] Parse the project in `run.sh` and pass the right `PROJECT_PATH` to Claude for the current mission
- [ ] Feed the journal with the current project scope to avoid mixup
- [ ] Always scope memory/* entries by project

### US 2.2 — Context per project
- **As** Kōan
- **I want** to load the right memory context according to the project
- **So that** I don't mix learnings between projects
- [ ] Structure `memory/` by project (e.g. `memory/anantys/learnings.md`)
- [ ] Adapt the bootstrap prompt to load the right subfolder
- [ ] Keep `memory/summary.md` global (cross-project)

---

## Epic 3 — Systematic Telegram notifications

> Goal: Telegram = single tracking channel, always up to date.

### US 3.1 — Complete mission lifecycle
- **As a** Telegram user
- **I want** to receive a notification at each stage of a mission
- **So that** I can track in real-time without opening the repo (message written via Claude to stay in personality)
- [ ] Notification "Mission taken: <title>"
- [ ] Notification "Mission completed: <title>" + 2-3 line summary
- [ ] Notification "Mission failed: <title>" + reason
- [ ] Notification "No pending mission — autonomous mode" (optional)

### US 3.2 — Daily report
- **As a** human
- **I want** to receive a daily summary on Telegram (time-based: if >= 7am and <= 9am report of yesterday's work / if > 8pm and quota reached for the day -> daily report)
- **So that** I can see Kōan's activity without reading journals
- [ ] At the end of the last run of the day: send a digest (missions done, discoveries, questions)
- [ ] Concise, Telegram-friendly format (no complex markdown)
- [ ] Ideal moment for daily log rotation, memory/*.md compaction/review

---

### US 3.3 — Recovery after crash
- **As a** system
- **I want** Kōan to resume cleanly after a crash or reboot
- **So that** I don't lose in-progress missions
- [ ] Detect "In Progress" missions without active worker on startup
- [ ] Automatically move them back to "Pending"
- [ ] Notify on Telegram "Restart — X missions resumed"

---

## Epic 5 — Robustness

> Goal: Improve reliability, maintainability, and cross-platform compatibility.

### US 5.1 — Rewrite run.sh as run.py
- **As a** developer
- **I want** the main run loop to be written in Python
- **So that** I avoid bash version compatibility issues and improve maintainability
- **Context**: macOS uses bash 3.2 (no associative arrays), Linux typically uses bash 4.0+. Current run.sh uses workarounds (parallel arrays) that are harder to maintain. Python would provide:
  - Consistent behavior across platforms
  - Better error handling and logging
  - Type safety with type hints
  - Dict-based project lookups (cleaner than parallel arrays)
  - Consistency with awake.py and notify.py
- [ ] Rewrite run.sh logic in Python (run.py)
- [ ] Keep same env var interface (KOAN_PROJECTS, KOAN_PROJECT_PATH, etc.)
- [ ] Preserve all existing features (project parsing, validation, mission extraction, quota detection)
- [ ] Update Makefile to use `$(PYTHON) koan/run.py` instead of `./koan/run.sh`
- [ ] Test backward compatibility with single-project setups

---

## Global Status

| Epic | Status |
|------|--------|
| 1 — Fast Telegram | ✅ Done |
| 2 — Multi-project | To do |
| 3 — Parallel workers | To do |
| 4 — Systematic notifications | To do |
| 5 — Robustness | In progress (INSTALL.md done) |
