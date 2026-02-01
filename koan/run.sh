#!/bin/bash
# Kōan — Main run loop
# Pulls missions, executes them via Claude Code CLI, commits results.
# Sends Telegram notifications at each mission lifecycle step.

set -euo pipefail

KOAN_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INSTANCE="$KOAN_ROOT/instance"
APP_DIR="$(dirname "$0")/app"
NOTIFY="$APP_DIR/notify.py"
DAILY_REPORT="$APP_DIR/daily_report.py"
MISSION_SUMMARY="$APP_DIR/mission_summary.py"
GIT_SYNC="$APP_DIR/git_sync.py"
GIT_SYNC_INTERVAL=${KOAN_GIT_SYNC_INTERVAL:-5}
HEALTH_CHECK="$APP_DIR/health_check.py"

if [ ! -d "$INSTANCE" ]; then
  echo "[koan] No instance/ directory found. Run: cp -r instance.example instance"
  exit 1
fi

# Config via env vars (or defaults)
MAX_RUNS=${KOAN_MAX_RUNS:-20}
INTERVAL=${KOAN_INTERVAL:-5}

# Parse projects configuration (bash 3.2 compatible - no associative arrays)
PROJECT_NAMES=()
PROJECT_PATHS=()

if [ -n "$KOAN_PROJECTS" ]; then
  # Multi-project mode: parse name:path;name:path;...
  IFS=';' read -ra PROJECT_PAIRS <<< "$KOAN_PROJECTS"
  for pair in "${PROJECT_PAIRS[@]}"; do
    IFS=':' read -r name path <<< "$pair"
    PROJECT_NAMES+=("$name")
    PROJECT_PATHS+=("$path")
  done
elif [ -n "$KOAN_PROJECT_PATH" ]; then
  # Single-project mode (backward compatible)
  PROJECT_NAMES=("default")
  PROJECT_PATHS=("$KOAN_PROJECT_PATH")
else
  echo "[koan] Error: Set KOAN_PROJECT_PATH or KOAN_PROJECTS env var."
  exit 1
fi

# Validate project configuration
if [ ${#PROJECT_NAMES[@]} -gt 5 ]; then
  echo "[koan] Error: Max 5 projects allowed. You have ${#PROJECT_NAMES[@]}."
  exit 1
fi

for i in "${!PROJECT_NAMES[@]}"; do
  name="${PROJECT_NAMES[$i]}"
  path="${PROJECT_PATHS[$i]}"
  if [ ! -d "$path" ]; then
    echo "[koan] Error: Project '$name' path does not exist: $path"
    exit 1
  fi
done

# Use venv python if available, else system python
PYTHON="python3"
[ -f "$KOAN_ROOT/.venv/bin/python3" ] && PYTHON="$KOAN_ROOT/.venv/bin/python3"

notify() {
  "$PYTHON" "$NOTIFY" "$@" 2>/dev/null || true
}

# Temp file for Claude output (set early so trap can clean it)
CLAUDE_OUT=""

cleanup() {
  [ -n "$CLAUDE_OUT" ] && rm -f "$CLAUDE_OUT"
  echo "[koan] Shutdown."
  notify "Koan interrupted after $count runs."
  exit 0
}

trap cleanup INT TERM

count=0

# Crash recovery: move stale in-progress missions back to pending
RECOVER="$APP_DIR/recover.py"
echo "[koan] Checking for interrupted missions..."
"$PYTHON" "$RECOVER" "$INSTANCE" || true

# Memory cleanup: compact summary, dedup learnings
MEMORY_MGR="$APP_DIR/memory_manager.py"
echo "[koan] Running memory cleanup..."
"$PYTHON" "$MEMORY_MGR" "$INSTANCE" cleanup 15 2>/dev/null || true

# Health check: warn if Telegram bridge is not running
echo "[koan] Checking Telegram bridge health..."
"$PYTHON" "$HEALTH_CHECK" "$KOAN_ROOT" --max-age 120 || true

echo "[koan] Starting. Max runs: $MAX_RUNS, interval: ${INTERVAL}s"
notify "Koan starting — $MAX_RUNS max runs, ${INTERVAL}s interval"

# Git sync: check what changed since last run (branches merged, new commits)
echo "[koan] Running git sync..."
for i in "${!PROJECT_NAMES[@]}"; do
  "$PYTHON" "$GIT_SYNC" "$INSTANCE" "${PROJECT_NAMES[$i]}" "${PROJECT_PATHS[$i]}" 2>/dev/null || true
done

# Daily report check (morning recap or evening summary)
"$PYTHON" "$DAILY_REPORT" 2>/dev/null || true

while [ $count -lt $MAX_RUNS ]; do
  # Check for stop request
  if [ -f "$KOAN_ROOT/.koan-stop" ]; then
    echo "[koan] Stop requested."
    rm -f "$KOAN_ROOT/.koan-stop"
    notify "Koan stopped on request after $count runs."
    break
  fi

  RUN_NUM=$((count + 1))
  echo "[koan] Run $RUN_NUM/$MAX_RUNS — $(date '+%Y-%m-%d %H:%M:%S')"

  # Extract next pending mission line (section-aware, scoped to "En attente")
  EXTRACT_MISSION="$APP_DIR/extract_mission.py"
  MISSION_LINE=$("$PYTHON" "$EXTRACT_MISSION" "$INSTANCE/missions.md" 2>/dev/null || echo "")

  # Extract mission title (strip "- ", project tag, and leading/trailing whitespace)
  MISSION_TITLE=""
  if [ -n "$MISSION_LINE" ]; then
    MISSION_TITLE=$(echo "$MISSION_LINE" | sed 's/^- //' | sed 's/\[projec\{0,1\}t:[a-zA-Z0-9_-]*\] *//' | sed 's/^ *//;s/ *$//')
  fi
  if [[ "$MISSION_LINE" =~ \[projec?t:([a-zA-Z0-9_-]+)\] ]]; then
    PROJECT_NAME="${BASH_REMATCH[1]}"

    # Find project index
    PROJECT_PATH=""
    for i in "${!PROJECT_NAMES[@]}"; do
      if [ "${PROJECT_NAMES[$i]}" = "$PROJECT_NAME" ]; then
        PROJECT_PATH="${PROJECT_PATHS[$i]}"
        break
      fi
    done

    # Validate mission project exists
    if [ -z "$PROJECT_PATH" ]; then
      echo "[koan] Error: Mission references unknown project: $PROJECT_NAME"
      echo "[koan] Known projects: ${PROJECT_NAMES[*]}"
      notify "Mission error: Unknown project '$PROJECT_NAME'. Known projects: ${PROJECT_NAMES[*]}"
      exit 1
    fi
  else
    # No project tag: rotate through projects in autonomous mode
    if [ -z "$MISSION_LINE" ]; then
      # Autonomous mode: round-robin across projects
      PROJECT_IDX=$(( (RUN_NUM - 1) % ${#PROJECT_NAMES[@]} ))
    else
      # Untagged mission: default to first project
      PROJECT_IDX=0
    fi
    PROJECT_NAME="${PROJECT_NAMES[$PROJECT_IDX]}"
    PROJECT_PATH="${PROJECT_PATHS[$PROJECT_IDX]}"
  fi

  echo "[koan] Project: $PROJECT_NAME ($PROJECT_PATH)"

  # Mission lifecycle notification: taken or autonomous
  if [ -n "$MISSION_TITLE" ]; then
    echo "[koan] Mission: $MISSION_TITLE"
    notify "Run $RUN_NUM/$MAX_RUNS — Mission taken: $MISSION_TITLE"
  else
    echo "[koan] No pending mission — autonomous mode ($PROJECT_NAME)"
    notify "Run $RUN_NUM/$MAX_RUNS — No pending mission, autonomous mode on $PROJECT_NAME"
  fi

  # Build prompt from template, replacing placeholders
  PROMPT=$(sed \
    -e "s|{INSTANCE}|$INSTANCE|g" \
    -e "s|{PROJECT_PATH}|$PROJECT_PATH|g" \
    -e "s|{PROJECT_NAME}|$PROJECT_NAME|g" \
    -e "s|{RUN_NUM}|$RUN_NUM|g" \
    -e "s|{MAX_RUNS}|$MAX_RUNS|g" \
    "$KOAN_ROOT/koan/system-prompt.md")

  # Execute next mission, capture output to detect quota errors
  cd "$PROJECT_PATH"
  CLAUDE_OUT="$(mktemp)"
  set +e  # Don't exit on error, we need to check the output
  claude -p "$PROMPT" --allowedTools Bash,Read,Write,Glob,Grep,Edit 2>&1 | tee "$CLAUDE_OUT"
  CLAUDE_EXIT=$?
  set -e

  # Check for quota exhaustion
  if grep -q "out of extra usage\|quota.*reached\|rate limit" "$CLAUDE_OUT"; then
    RESET_INFO=$(grep -o "resets.*" "$CLAUDE_OUT" | head -1 || echo "")
    echo "[koan] Quota reached. $RESET_INFO"

    # Write to journal (per-project)
    JOURNAL_DIR="$INSTANCE/journal/$(date +%Y-%m-%d)"
    JOURNAL_FILE="$JOURNAL_DIR/$PROJECT_NAME.md"
    mkdir -p "$JOURNAL_DIR"
    cat >> "$JOURNAL_FILE" <<EOF

## Quota Exhausted — $(date '+%H:%M:%S')

Claude quota reached after $count runs (project: $PROJECT_NAME). $RESET_INFO

Koan paused. Use \`/resume\` command via Telegram when ready to restart.
EOF

    # Save reset time for /resume command
    echo "$RESET_INFO" > "$KOAN_ROOT/.koan-quota-reset"
    echo "$(date +%s)" >> "$KOAN_ROOT/.koan-quota-reset"  # Current timestamp

    # Commit journal update
    cd "$INSTANCE"
    git add -A
    git diff --cached --quiet || \
      git commit -m "koan: quota exhausted $(date +%Y-%m-%d-%H:%M)" && \
      git push origin main 2>/dev/null || true

    notify "⚠️ Claude quota exhausted. $RESET_INFO

Koan paused after $count runs. Send /resume via Telegram when quota resets to check if you want to restart."
    rm -f "$CLAUDE_OUT"
    CLAUDE_OUT=""
    break
  fi
  rm -f "$CLAUDE_OUT"
  CLAUDE_OUT=""

  # Report result with mission title
  if [ $CLAUDE_EXIT -eq 0 ]; then
    if [ -n "$MISSION_TITLE" ]; then
      notify "Run $RUN_NUM/$MAX_RUNS — Mission completed: $MISSION_TITLE"
    else
      notify "Run $RUN_NUM/$MAX_RUNS — Autonomous run completed"
    fi

    # Extract journal summary and send via outbox (locked append to avoid race with awake.py)
    SUMMARY_TEXT=$("$PYTHON" "$MISSION_SUMMARY" "$INSTANCE" "$PROJECT_NAME" 2>/dev/null || echo "")
    if [ -n "$SUMMARY_TEXT" ]; then
      "$PYTHON" -c "
import fcntl, sys
with open('$INSTANCE/outbox.md', 'a') as f:
    fcntl.flock(f, fcntl.LOCK_EX)
    f.write(sys.stdin.read())
    fcntl.flock(f, fcntl.LOCK_UN)
" <<< "$SUMMARY_TEXT"
    fi
  else
    if [ -n "$MISSION_TITLE" ]; then
      notify "Run $RUN_NUM/$MAX_RUNS — Mission failed: $MISSION_TITLE"
    else
      notify "Run $RUN_NUM/$MAX_RUNS — Run failed"
    fi
  fi

  # Commit instance results
  cd "$INSTANCE"
  git add -A
  git diff --cached --quiet || \
    git commit -m "koan: $(date +%Y-%m-%d-%H:%M)" && \
    git push origin main 2>/dev/null || true

  count=$((count + 1))

  # Periodic git sync (every GIT_SYNC_INTERVAL runs)
  if [ $((count % GIT_SYNC_INTERVAL)) -eq 0 ] && [ $count -lt $MAX_RUNS ]; then
    echo "[koan] Periodic git sync (run $count)..."
    for i in "${!PROJECT_NAMES[@]}"; do
      "$PYTHON" "$GIT_SYNC" "$INSTANCE" "${PROJECT_NAMES[$i]}" "${PROJECT_PATHS[$i]}" 2>/dev/null || true
    done
  fi

  if [ $count -lt $MAX_RUNS ]; then
    echo "[koan] Sleeping ${INTERVAL}s..."
    sleep $INTERVAL
  fi
done

echo "[koan] Session complete. $count runs executed."
notify "Session complete — $count runs executed"

# End-of-session daily report check
"$PYTHON" "$DAILY_REPORT" 2>/dev/null || true
