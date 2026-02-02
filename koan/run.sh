#!/bin/bash
# Kōan — Main run loop
# Pulls missions, executes them via Claude Code CLI, commits results.
# Sends Telegram notifications at each mission lifecycle step.

set -euo pipefail

KOAN_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export KOAN_ROOT
INSTANCE="$KOAN_ROOT/instance"
APP_DIR="$(dirname "$0")/app"
NOTIFY="$APP_DIR/notify.py"
DAILY_REPORT="$APP_DIR/daily_report.py"
MISSION_SUMMARY="$APP_DIR/mission_summary.py"
GIT_SYNC="$APP_DIR/git_sync.py"
GIT_SYNC_INTERVAL=${KOAN_GIT_SYNC_INTERVAL:-5}
HEALTH_CHECK="$APP_DIR/health_check.py"
USAGE_TRACKER="$APP_DIR/usage_tracker.py"

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

# Set PYTHONPATH so Python scripts can import from app/
export PYTHONPATH="$KOAN_ROOT/koan"

# Initialize .koan-project with first project
echo "${PROJECT_NAMES[0]}" > "$KOAN_ROOT/.koan-project"
export KOAN_CURRENT_PROJECT="${PROJECT_NAMES[0]}"
export KOAN_CURRENT_PROJECT_PATH="${PROJECT_PATHS[0]}"

notify() {
  "$PYTHON" "$NOTIFY" --format "$@" 2>/dev/null || true
}

# Temp file for Claude output (set early so trap can clean it)
CLAUDE_OUT=""

cleanup() {
  [ -n "$CLAUDE_OUT" ] && rm -f "$CLAUDE_OUT"
  echo "[koan] Shutdown."
  CURRENT_PROJ=$(cat "$KOAN_ROOT/.koan-project" 2>/dev/null || echo "unknown")
  notify "Koan interrupted after $count runs. Last project: $CURRENT_PROJ."
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
STARTUP_PROJECTS=$(IFS=', '; echo "${PROJECT_NAMES[*]}")
STARTUP_PAUSE=""
if [ -f "$KOAN_ROOT/.koan-pause" ]; then
  STARTUP_PAUSE=" Currently PAUSED."
fi
notify "Koan starting — $MAX_RUNS max runs, ${INTERVAL}s interval. Projects: $STARTUP_PROJECTS. Current: ${PROJECT_NAMES[0]}.$STARTUP_PAUSE"

# Git sync: check what changed since last run (branches merged, new commits)
echo "[koan] Running git sync..."
for i in "${!PROJECT_NAMES[@]}"; do
  "$PYTHON" "$GIT_SYNC" "$INSTANCE" "${PROJECT_NAMES[$i]}" "${PROJECT_PATHS[$i]}" 2>/dev/null || true
done

# Daily report check (morning recap or evening summary)
"$PYTHON" "$DAILY_REPORT" 2>/dev/null || true

##
# Kōan main loop - alive and running
##
while [ $count -lt $MAX_RUNS ]; do
  
  # Check for stop request - graceful shutdown
  if [ -f "$KOAN_ROOT/.koan-stop" ]; then
    echo "[koan] Stop requested."
    rm -f "$KOAN_ROOT/.koan-stop"
    CURRENT_PROJ=$(cat "$KOAN_ROOT/.koan-project" 2>/dev/null || echo "unknown")
    notify "Koan stopped on request after $count runs. Last project: $CURRENT_PROJ."
    break
  fi

  # Check for pause — contemplative mode
  if [ -f "$KOAN_ROOT/.koan-pause" ]; then
    echo "[koan] Paused. Contemplative mode. ($(date '+%H:%M'))"

    # ~50% chance of a contemplative session
    STEP_IN_PROBABILITY=50
    ROLL=$((RANDOM % 100))
    if [ $ROLL -lt $STEP_IN_PROBABILITY ]; then
      echo "[koan] A thought stirs..."
      PROJECT_NAME="${PROJECT_NAMES[0]}"
      PROJECT_PATH="${PROJECT_PATHS[0]}"
      echo "$PROJECT_NAME" > "$KOAN_ROOT/.koan-project"
      export KOAN_CURRENT_PROJECT="$PROJECT_NAME"
      export KOAN_CURRENT_PROJECT_PATH="$PROJECT_PATH"

      CONTEMPLATE_PROMPT=$(sed \
        -e "s|{INSTANCE}|$INSTANCE|g" \
        -e "s|{PROJECT_NAME}|$PROJECT_NAME|g" \
        "$KOAN_ROOT/koan/system-prompts/contemplative.md")

      cd "$INSTANCE"
      set +e
      claude -p "$CONTEMPLATE_PROMPT" --allowedTools Read,Write,Glob,Grep --max-turns 3 2>/dev/null
      set -e
    fi

    # Sleep in 5s increments — allows /resume to take effect quickly
    for ((s=0; s<60; s++)); do
      [ ! -f "$KOAN_ROOT/.koan-pause" ] && break
      sleep 5
    done
    continue
  fi

  RUN_NUM=$((count + 1))
  echo ""
  echo "=== Run $RUN_NUM/$MAX_RUNS — $(date '+%Y-%m-%d %H:%M:%S') ==="

  # Parse usage.md and decide autonomous mode
  USAGE_DECISION=$("$PYTHON" "$USAGE_TRACKER" "$INSTANCE/usage.md" "$count" "$KOAN_PROJECTS" 2>/dev/null || echo "implement:50:Tracker error:0")
  IFS=':' read -r AUTONOMOUS_MODE AVAILABLE_PCT DECISION_REASON RECOMMENDED_PROJECT_IDX <<< "$USAGE_DECISION"

  # Display usage status (verbose logging)
  echo "Usage Status:"
  if [ -f "$INSTANCE/usage.md" ]; then
    # Extract and display session/weekly lines
    SESSION_LINE=$(grep -i "Session" "$INSTANCE/usage.md" | head -1 || echo "Session: unknown")
    WEEKLY_LINE=$(grep -i "Weekly" "$INSTANCE/usage.md" | head -1 || echo "Weekly: unknown")
    echo "  $SESSION_LINE"
    echo "  $WEEKLY_LINE"
  else
    echo "  [No usage.md file - using fallback mode]"
  fi
  echo "  Safety margin: 10% → Available: ${AVAILABLE_PCT}%"
  echo ""

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
    # No project tag: use smart selection or default
    if [ -z "$MISSION_LINE" ]; then
      # Autonomous mode: use usage tracker recommendation
      PROJECT_IDX=$RECOMMENDED_PROJECT_IDX
    else
      # Untagged mission: default to first project
      PROJECT_IDX=0
    fi
    PROJECT_NAME="${PROJECT_NAMES[$PROJECT_IDX]}"
    PROJECT_PATH="${PROJECT_PATHS[$PROJECT_IDX]}"
  fi

  # Define focus area based on autonomous mode
  if [ -z "$MISSION_LINE" ]; then
    case "$AUTONOMOUS_MODE" in
      wait)
        echo "Decision: WAIT mode (budget exhausted)"
        echo "  Reason: $DECISION_REASON"
        echo "  Action: Sending retrospective and exiting"
        echo ""
        # Send retrospective and exit gracefully
        "$PYTHON" "$APP_DIR/send_retrospective.py" "$INSTANCE" "$PROJECT_NAME" 2>/dev/null || true
        notify "⏸️ Koan paused: budget exhausted after $count runs on [$PROJECT_NAME]. Use /resume when quota resets."
        break
        ;;
      review)
        FOCUS_AREA="Low-cost review: audit code, find issues, suggest improvements (READ-ONLY)"
        ;;
      implement)
        FOCUS_AREA="Medium-cost implementation: prototype fixes, small improvements"
        ;;
      deep)
        FOCUS_AREA="High-cost deep work: refactoring, architectural changes"
        ;;
      *)
        FOCUS_AREA="General autonomous work"
        ;;
    esac
  else
    FOCUS_AREA="Execute assigned mission"
  fi

  # Enforce current project state
  echo "$PROJECT_NAME" > "$KOAN_ROOT/.koan-project"
  export KOAN_CURRENT_PROJECT="$PROJECT_NAME"
  export KOAN_CURRENT_PROJECT_PATH="$PROJECT_PATH"

  echo ">>> Current project: $PROJECT_NAME ($PROJECT_PATH)"
  echo ""

  # Mission lifecycle notification: taken or autonomous
  if [ -n "$MISSION_TITLE" ]; then
    echo "Decision: MISSION mode (assigned)"
    echo "  Mission: $MISSION_TITLE"
    echo "  Project: $PROJECT_NAME"
    echo ""
    notify "Run $RUN_NUM/$MAX_RUNS — [$PROJECT_NAME] Mission taken: $MISSION_TITLE"
  else
    ESTIMATED_COST="5.0"
    # Uppercase mode for display (bash 3.2 compatible)
    MODE_UPPER=$(echo "$AUTONOMOUS_MODE" | tr '[:lower:]' '[:upper:]')
    echo "Decision: $MODE_UPPER mode (estimated cost: ${ESTIMATED_COST}% session)"
    echo "  Reason: $DECISION_REASON"
    echo "  Project: $PROJECT_NAME"
    echo "  Focus: $FOCUS_AREA"
    echo ""
    notify "Run $RUN_NUM/$MAX_RUNS — Autonomous: ${AUTONOMOUS_MODE} mode on $PROJECT_NAME"
  fi

  # Build prompt from template, replacing placeholders
  PROMPT=$(sed \
    -e "s|{INSTANCE}|$INSTANCE|g" \
    -e "s|{PROJECT_PATH}|$PROJECT_PATH|g" \
    -e "s|{PROJECT_NAME}|$PROJECT_NAME|g" \
    -e "s|{RUN_NUM}|$RUN_NUM|g" \
    -e "s|{MAX_RUNS}|$MAX_RUNS|g" \
    -e "s|{AUTONOMOUS_MODE}|${AUTONOMOUS_MODE:-implement}|g" \
    -e "s|{FOCUS_AREA}|${FOCUS_AREA:-General autonomous work}|g" \
    -e "s|{AVAILABLE_PCT}|${AVAILABLE_PCT:-50}|g" \
    "$KOAN_ROOT/koan/system-prompts/agent.md")

  # Append merge policy based on config
  MERGE_POLICY=""
  if "$PYTHON" -c "
from app.utils import load_config, get_auto_merge_config
config = load_config()
merge_cfg = get_auto_merge_config(config, '$PROJECT_NAME')
import sys
sys.exit(0 if merge_cfg.get('enabled', True) and merge_cfg.get('rules') else 1)
" 2>/dev/null; then
    MERGE_POLICY="

# Git Merge Policy (Auto-Merge Enabled)

Auto-merge is ENABLED for this project. After you complete your work on a koan/* branch
and push it, the system will automatically merge it according to configured rules.

Just focus on: creating koan/* branch, implementing, committing, pushing.
The auto-merge system handles the merge to the base branch after mission completion.
"
  else
    MERGE_POLICY="

# Git Merge Policy

Auto-merge is NOT configured for this project. Follow standard workflow:
create koan/* branches, commit, and push, but DO NOT merge yourself.
"
  fi
  PROMPT="$PROMPT$MERGE_POLICY"

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
      notify "Run $RUN_NUM/$MAX_RUNS — [$PROJECT_NAME] Mission completed: $MISSION_TITLE"
    else
      notify "Run $RUN_NUM/$MAX_RUNS — [$PROJECT_NAME] Autonomous run completed"
    fi

    # Extract journal summary and append raw to outbox
    # (formatting via Claude happens at flush time in awake.py)
    SUMMARY_TEXT=$("$PYTHON" "$MISSION_SUMMARY" "$INSTANCE" "$PROJECT_NAME" 2>/dev/null || echo "")
    if [ -n "$SUMMARY_TEXT" ]; then
      # Locked append to outbox (avoid race with awake.py)
      "$PYTHON" -c "
import fcntl, sys
with open('$INSTANCE/outbox.md', 'a') as f:
    fcntl.flock(f, fcntl.LOCK_EX)
    f.write(sys.stdin.read())
    fcntl.flock(f, fcntl.LOCK_UN)
" <<< "$SUMMARY_TEXT"
    fi

    # Auto-merge logic (if on koan/* branch)
    cd "$PROJECT_PATH"
    CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
    if [[ "$CURRENT_BRANCH" == koan/* ]]; then
      echo "[koan] Checking auto-merge for $CURRENT_BRANCH..."
      GIT_AUTO_MERGE="$APP_DIR/git_auto_merge.py"
      if "$PYTHON" "$GIT_AUTO_MERGE" "$INSTANCE" "$PROJECT_NAME" "$PROJECT_PATH" "$CURRENT_BRANCH" 2>&1; then
        echo "[koan] Auto-merge completed for $CURRENT_BRANCH"
      else
        echo "[koan] Auto-merge skipped or failed for $CURRENT_BRANCH (see journal)"
      fi
    fi
  else
    if [ -n "$MISSION_TITLE" ]; then
      notify "Run $RUN_NUM/$MAX_RUNS — [$PROJECT_NAME] Mission failed: $MISSION_TITLE"
    else
      notify "Run $RUN_NUM/$MAX_RUNS — [$PROJECT_NAME] Run failed"
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
