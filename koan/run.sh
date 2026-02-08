#!/bin/bash
# Kōan — Main run loop
# Pulls missions, executes them via Claude Code CLI, commits results.
# Sends Telegram notifications at each mission lifecycle step.
#
# Restart support: if the inner loop exits with code 42 (restart signal),
# the script re-executes itself. Any other exit code is a real stop.

set -euo pipefail

# --- Restart wrapper ---
# If invoked with _KOAN_INNER=1, skip the wrapper (we're already inside it).
# Otherwise, run the script in a restart loop.
if [ -z "${_KOAN_INNER:-}" ]; then
  while true; do
    exec_exit=0
    _KOAN_INNER=1 "$0" "$@" || exec_exit=$?
    if [ "$exec_exit" -eq 42 ]; then
      echo "[koan] Restarting run loop..."
      sleep 1  # Brief pause to let filesystem settle
      continue
    fi
    exit "$exec_exit"
  done
fi

# --- Colored log prefixes ---
# Each category gets its own ANSI color for easy visual scanning.
if [ -t 1 ]; then
  # Terminal supports colors
  _C_RESET='\033[0m'
  _C_BOLD='\033[1m'
  _C_DIM='\033[2m'
  _C_RED='\033[31m'
  _C_GREEN='\033[32m'
  _C_YELLOW='\033[33m'
  _C_BLUE='\033[34m'
  _C_MAGENTA='\033[35m'
  _C_CYAN='\033[36m'
  _C_WHITE='\033[37m'
else
  # No color (piped output, CI, etc.)
  _C_RESET='' _C_BOLD='' _C_DIM=''
  _C_RED='' _C_GREEN='' _C_YELLOW=''
  _C_BLUE='' _C_MAGENTA='' _C_CYAN='' _C_WHITE=''
fi

# log <category> <message>
# Categories: koan (cyan), error (red+bold), init (blue), health (yellow),
#             git (magenta), mission (green), quota (yellow+bold), pause (blue+dim)
log() {
  local cat="$1"; shift
  local color
  case "$cat" in
    koan)    color="${_C_CYAN}" ;;
    error)   color="${_C_BOLD}${_C_RED}" ;;
    init)    color="${_C_BLUE}" ;;
    health)  color="${_C_YELLOW}" ;;
    git)     color="${_C_MAGENTA}" ;;
    mission) color="${_C_GREEN}" ;;
    quota)   color="${_C_BOLD}${_C_YELLOW}" ;;
    pause)   color="${_C_DIM}${_C_BLUE}" ;;
    *)       color="${_C_WHITE}" ;;
  esac
  echo -e "${color}[${cat}]${_C_RESET} $*"
}

# set_status <message>
# Writes to .koan-status so /status and dashboard can display loop state.
# This file is the primary way the human knows what the loop is doing.
set_status() {
  echo "$*" > "$KOAN_ROOT/.koan-status"
}

# has_pending_missions
# Quick check for pending missions in missions.md (no Claude call).
# Returns 0 if pending missions exist, 1 otherwise.
has_pending_missions() {
  "$PYTHON" -c "
from app.missions import count_pending
from pathlib import Path
p = Path('$INSTANCE/missions.md')
print(count_pending(p.read_text()) if p.exists() else 0)
" 2>/dev/null | grep -qv '^0$'
}

# Ensure KOAN_ROOT is set - mandatory from config
if [ -z "${KOAN_ROOT:-}" ]; then
  log error "KOAN_ROOT environment variable not set."
  exit 1
fi

# Record startup time — used to ignore stale .koan-restart files left
# from a previous process incarnation (same dedup logic as awake.py).
KOAN_START_TIME=$(date +%s)

INSTANCE="$KOAN_ROOT/instance"
APP_DIR="$KOAN_ROOT/koan/app"
NOTIFY="$APP_DIR/notify.py"
DAILY_REPORT="$APP_DIR/daily_report.py"
GIT_SYNC="$APP_DIR/git_sync.py"
GIT_SYNC_INTERVAL=${KOAN_GIT_SYNC_INTERVAL:-5}
HEALTH_CHECK="$APP_DIR/health_check.py"
SELF_REFLECTION="$APP_DIR/self_reflection.py"
RITUALS="$APP_DIR/rituals.py"
USAGE_TRACKER="$APP_DIR/usage_tracker.py"
USAGE_ESTIMATOR="$APP_DIR/usage_estimator.py"
USAGE_STATE="$INSTANCE/usage_state.json"

if [ ! -d "$INSTANCE" ]; then
  log error "No instance/ directory found. Run: cp -r instance.example instance"
  exit 1
fi

# Config is loaded from config.yaml (see instance/config.yaml)
# These are placeholder defaults; actual values set after PYTHONPATH is configured.
MAX_RUNS=20
INTERVAL=300

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
  log error "Set KOAN_PROJECT_PATH or KOAN_PROJECTS env var."
  exit 1
fi

# Validate project configuration
if [ ${#PROJECT_NAMES[@]} -gt 50 ]; then
  log error "Max 50 projects allowed. You have ${#PROJECT_NAMES[@]}."
  exit 1
fi

for i in "${!PROJECT_NAMES[@]}"; do
  name="${PROJECT_NAMES[$i]}"
  path="${PROJECT_PATHS[$i]}"
  if [ ! -d "$path" ]; then
    log error "Project '$name' path does not exist: $path"
    exit 1
  fi
done

# Use venv python if available, else system python
PYTHON="python3"
[ -f "$KOAN_ROOT/.venv/bin/python3" ] && PYTHON="$KOAN_ROOT/.venv/bin/python3"

# Set PYTHONPATH so Python scripts can import from app/
export PYTHONPATH="$KOAN_ROOT/koan"

# Enforce single instance — abort if another run process is alive
"$PYTHON" -m app.pid_manager acquire-pid run "$KOAN_ROOT" $$

# Load config from config.yaml (source of truth for behavioral settings)
MAX_RUNS=$("$PYTHON" -c "from app.utils import get_max_runs; print(get_max_runs())" 2>/dev/null || echo "20")
INTERVAL=$("$PYTHON" -c "from app.utils import get_interval_seconds; print(get_interval_seconds())" 2>/dev/null || echo "300")
BRANCH_PREFIX=$("$PYTHON" -c "from app.utils import get_branch_prefix; print(get_branch_prefix())" 2>/dev/null || echo "koan/")

# Set git identity for koan commits (overrides local git config)
if [ -n "${KOAN_EMAIL:-}" ]; then
  export GIT_AUTHOR_NAME="Koan"
  export GIT_AUTHOR_EMAIL="$KOAN_EMAIL"
  export GIT_COMMITTER_NAME="Koan"
  export GIT_COMMITTER_EMAIL="$KOAN_EMAIL"
fi

# Set up GitHub CLI identity if GITHUB_USER is configured
if [ -n "${GITHUB_USER:-}" ]; then
  GH_AUTH_OUTPUT=$("$PYTHON" -m app.github_auth 2>/dev/null)
  GH_AUTH_EXIT=$?
  if [ $GH_AUTH_EXIT -eq 0 ] && [ -n "$GH_AUTH_OUTPUT" ]; then
    export "${GH_AUTH_OUTPUT?}"
    echo "[koan] GitHub CLI authenticated as $GITHUB_USER"
  else
    echo "[koan] Warning: GitHub auth failed for $GITHUB_USER — gh commands may fail"
  fi
fi

# Initialize .koan-project with first project
echo "${PROJECT_NAMES[0]}" > "$KOAN_ROOT/.koan-project"
export KOAN_CURRENT_PROJECT="${PROJECT_NAMES[0]}"
export KOAN_CURRENT_PROJECT_PATH="${PROJECT_PATHS[0]}"

notify() {
  "$PYTHON" "$NOTIFY" --format "$@" 2>/dev/null || true
}

# Temp file for Claude output (set early so trap can clean it)
CLAUDE_OUT=""

# --- Graceful CTRL-C handling ---
# When a task is running, first CTRL-C shows a warning.
# Second CTRL-C within 10 seconds actually aborts.
# If no second CTRL-C within 10s, the warning resets.
TASK_RUNNING=0
CTRL_C_FIRST_TIME=0
CLAUDE_PID=""
CTRL_C_TIMEOUT=10

cleanup() {
  [ -n "$CLAUDE_OUT" ] && rm -f "$CLAUDE_OUT"
  [ -n "${CLAUDE_ERR:-}" ] && rm -f "$CLAUDE_ERR"
  # Kill child process if still running, then wait for it to release pipes
  if [ -n "$CLAUDE_PID" ]; then
    kill "$CLAUDE_PID" 2>/dev/null
    wait "$CLAUDE_PID" 2>/dev/null
  fi
  rm -f "$KOAN_ROOT/.koan-status"
  "$PYTHON" -m app.pid_manager release-pid run "$KOAN_ROOT" 2>/dev/null || true
  log koan "Shutdown."
  CURRENT_PROJ=$(cat "$KOAN_ROOT/.koan-project" 2>/dev/null || echo "unknown")
  notify "Koan interrupted after $count runs. Last project: $CURRENT_PROJ."
  exit 0
}

on_sigint() {
  if [ "$TASK_RUNNING" -eq 0 ]; then
    # No task running — immediate cleanup
    cleanup
  fi

  # Task is running — check if this is first or second CTRL-C
  local now
  now=$(date +%s)

  if [ "$CTRL_C_FIRST_TIME" -gt 0 ]; then
    local elapsed=$((now - CTRL_C_FIRST_TIME))
    if [ "$elapsed" -le "$CTRL_C_TIMEOUT" ]; then
      # Second CTRL-C within timeout — abort
      echo ""
      log koan "Confirmed. Aborting task..."
      CTRL_C_FIRST_TIME=0
      TASK_RUNNING=0
      [ -n "$CLAUDE_PID" ] && kill "$CLAUDE_PID" 2>/dev/null
      cleanup
    fi
  fi

  # First CTRL-C (or timeout expired — treat as new first)
  CTRL_C_FIRST_TIME=$now
  echo ""
  log koan "⚠️  A task is running. Press CTRL-C again within ${CTRL_C_TIMEOUT}s to abort."
}

trap on_sigint INT
trap cleanup EXIT

# wait_for_claude_task
# Waits for the background process $CLAUDE_PID with graceful CTRL-C protection.
# Caller must set: CLAUDE_PID (background process ID)
# Sets: CLAUDE_EXIT (child exit code)
# Also sets TASK_RUNNING=1 on entry, resets to 0 on exit.
wait_for_claude_task() {
  TASK_RUNNING=1
  CTRL_C_FIRST_TIME=0

  # Wait for child, re-waiting if interrupted by CTRL-C
  while kill -0 "$CLAUDE_PID" 2>/dev/null; do
    wait "$CLAUDE_PID" 2>/dev/null || true
  done

  # Child is done — capture exit code (|| true prevents set -e from exiting)
  wait "$CLAUDE_PID" 2>/dev/null && CLAUDE_EXIT=0 || CLAUDE_EXIT=$?
  CLAUDE_PID=""
  TASK_RUNNING=0
  CTRL_C_FIRST_TIME=0
}

count=0

# Print startup banner
"$PYTHON" -c "from app.banners import print_agent_banner; print_agent_banner('agent loop — $CLI_PROVIDER')" 2>/dev/null || true

# Crash recovery: move stale in-progress missions back to pending
RECOVER="$APP_DIR/recover.py"
log health "Checking for interrupted missions..."
"$PYTHON" "$RECOVER" "$INSTANCE" || true

# Memory cleanup: compact summary, dedup learnings
MEMORY_MGR="$APP_DIR/memory_manager.py"
log health "Running memory cleanup..."
"$PYTHON" "$MEMORY_MGR" "$INSTANCE" cleanup 15 2>/dev/null || true

# Health check: warn if Telegram bridge is not running
log health "Checking Telegram bridge health..."
"$PYTHON" "$HEALTH_CHECK" "$KOAN_ROOT" --max-age 120 || true

# Self-reflection: every 10 sessions, trigger introspection
log health "Checking self-reflection trigger..."
"$PYTHON" "$SELF_REFLECTION" "$INSTANCE" --notify || true

# Check start_on_pause config: create .koan-pause if true (boot into pause mode)
START_ON_PAUSE=$("$PYTHON" -c "from app.utils import get_start_on_pause; print('true' if get_start_on_pause() else 'false')" 2>/dev/null || echo "false")
if [ "$START_ON_PAUSE" = "true" ] && [ ! -f "$KOAN_ROOT/.koan-pause" ]; then
  log pause "start_on_pause=true in config. Entering pause mode."
  touch "$KOAN_ROOT/.koan-pause"
fi

set_status "Starting up"
log init "Starting. Max runs: $MAX_RUNS, interval: ${INTERVAL}s"
STARTUP_PROJECTS=$(printf '%s\n' "${PROJECT_NAMES[@]}" | sort | sed 's/^/  • /')
STARTUP_PAUSE=""
if [ -f "$KOAN_ROOT/.koan-pause" ]; then
  STARTUP_PAUSE=" Currently PAUSED."
fi
notify "Koan starting — $MAX_RUNS max runs, ${INTERVAL}s interval.
Projects:
$STARTUP_PROJECTS
Current: ${PROJECT_NAMES[0]}.$STARTUP_PAUSE"

# Git sync: check what changed since last run (branches merged, new commits)
log git "Running git sync..."
for i in "${!PROJECT_NAMES[@]}"; do
  "$PYTHON" "$GIT_SYNC" "$INSTANCE" "${PROJECT_NAMES[$i]}" "${PROJECT_PATHS[$i]}" 2>/dev/null || true
done

# Daily report check (morning recap or evening summary)
"$PYTHON" "$DAILY_REPORT" 2>/dev/null || true

# Morning ritual: run at first iteration (before main loop starts)
log init "Running morning ritual..."
"$PYTHON" "$RITUALS" morning "$INSTANCE" || true

##
# Kōan main loop - infinite, never exits unless /stop requested
##
while true; do

  # Check for stop request - graceful shutdown (ONLY way to exit the loop)
  if [ -f "$KOAN_ROOT/.koan-stop" ]; then
    log koan "Stop requested."
    rm -f "$KOAN_ROOT/.koan-stop"
    CURRENT_PROJ=$(cat "$KOAN_ROOT/.koan-project" 2>/dev/null || echo "unknown")
    notify "Koan stopped on request after $count runs. Last project: $CURRENT_PROJ."
    break
  fi

  # Check for restart request — exit with code 42 so wrapper can re-launch.
  # Only react if the file was touched AFTER our start time (ignore stale
  # signals left from a previous incarnation to prevent restart loops).
  if [ -f "$KOAN_ROOT/.koan-restart" ]; then
    RESTART_MTIME=$("$PYTHON" -c "import os; print(int(os.path.getmtime('$KOAN_ROOT/.koan-restart')))" 2>/dev/null || echo 0)
    if [ "$RESTART_MTIME" -gt "$KOAN_START_TIME" ]; then
      log koan "Restart requested. Exiting for re-launch..."
      exit 42
    fi
  fi

  # Check for pause — contemplative mode
  if [ -f "$KOAN_ROOT/.koan-pause" ]; then
    set_status "Paused ($(date '+%H:%M'))"
    log pause "Paused. Contemplative mode. ($(date '+%H:%M'))"

    # Check auto-resume via pause_manager (handles quota reset + 5h cooldown)
    RESUME_MSG=$("$PYTHON" -m app.pause_manager check "$KOAN_ROOT" 2>/dev/null) && {
      log pause "Auto-resume: $RESUME_MSG"
      count=0  # Reset run counter on auto-resume — start fresh at MAX capacity
      notify "🔄 Koan auto-resumed: $RESUME_MSG. Starting fresh (0/$MAX_RUNS runs)."
      continue
    }

    # Check for manual /resume (pause file removed but we're still in pause block from previous iteration)
    # This shouldn't normally happen since the continue at end of sleep loop would catch it,
    # but if we reach here with no pause file, we've been manually resumed
    if [ ! -f "$KOAN_ROOT/.koan-pause" ]; then
      log pause "Manual resume detected"
      count=0  # Reset run counter on manual resume too
      continue
    fi

    # ~50% chance of a contemplative session (skipped in focus mode)
    STEP_IN_PROBABILITY=50
    ROLL=$((RANDOM % 100))
    if [ $ROLL -lt $STEP_IN_PROBABILITY ] && ! "$PYTHON" -m app.focus_manager check "$KOAN_ROOT" >/dev/null 2>&1; then
      log pause "A thought stirs..."
      PROJECT_NAME="${PROJECT_NAMES[0]}"
      PROJECT_PATH="${PROJECT_PATHS[0]}"
      echo "$PROJECT_NAME" > "$KOAN_ROOT/.koan-project"
      export KOAN_CURRENT_PROJECT="$PROJECT_NAME"
      export KOAN_CURRENT_PROJECT_PATH="$PROJECT_PATH"

      pushd "$INSTANCE" > /dev/null
      log pause "Running contemplative session..."
      (trap '' INT; exec "$PYTHON" -m app.contemplative_runner run \
        --instance "$INSTANCE" \
        --project-name "$PROJECT_NAME" \
        --session-info "Pause mode. Run loop paused.") 2>/dev/null &
      CLAUDE_PID=$!
      wait_for_claude_task
      log pause "Contemplative session ended."
      popd > /dev/null
    fi

    # Sleep in 5s increments — allows /resume, /restart, or auto-resume to take effect quickly
    for ((s=0; s<60; s++)); do
      [ ! -f "$KOAN_ROOT/.koan-pause" ] && break
      [ -f "$KOAN_ROOT/.koan-restart" ] && break
      sleep 5
    done
    continue
  fi

  RUN_NUM=$((count + 1))
  set_status "Run $RUN_NUM/$MAX_RUNS — preparing"
  echo ""
  echo -e "${_C_BOLD}${_C_CYAN}=== Run $RUN_NUM/$MAX_RUNS — $(date '+%Y-%m-%d %H:%M:%S') ===${_C_RESET}"

  # Refresh usage.md from accumulated token state (handles session/weekly resets)
  # On first run, trust existing usage.md as source of truth (don't reset counters)
  if [ $count -gt 0 ]; then
    "$PYTHON" "$USAGE_ESTIMATOR" refresh "$USAGE_STATE" "$INSTANCE/usage.md" 2>/dev/null || true
  fi

  # Parse usage.md and decide autonomous mode
  USAGE_DECISION=$("$PYTHON" "$USAGE_TRACKER" "$INSTANCE/usage.md" "$count" "$KOAN_PROJECTS" 2>/dev/null || echo "implement:50:Tracker error:0")
  IFS=':' read -r AUTONOMOUS_MODE AVAILABLE_PCT DECISION_REASON RECOMMENDED_PROJECT_IDX <<< "$USAGE_DECISION"

  # Display usage status (verbose logging)
  log quota "Usage Status:"
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

  # Check recurring missions — inject due ones into pending queue
  "$PYTHON" "$APP_DIR/recurring_scheduler.py" "$INSTANCE" 2>/dev/null | while IFS= read -r line; do
    log mission "$line"
  done

  # Pick next mission using Claude-based intelligent picker
  LAST_PROJECT=$(cat "$KOAN_ROOT/.koan-project" 2>/dev/null || echo "")
  PICK_MISSION="$APP_DIR/pick_mission.py"
  PICK_STDERR=$(mktemp)
  PICK_RESULT=$("$PYTHON" "$PICK_MISSION" "$INSTANCE" "$KOAN_PROJECTS" "$RUN_NUM" "$AUTONOMOUS_MODE" "$LAST_PROJECT" 2>"$PICK_STDERR" || echo "")
  if [ -s "$PICK_STDERR" ]; then
    log mission "Mission picker stderr:"
    cat "$PICK_STDERR"
  fi
  rm -f "$PICK_STDERR"
  log mission "Picker result: '${PICK_RESULT:-<empty>}'"

  # Parse picker output: "project_name:mission title" or empty
  MISSION_TITLE=""
  if [ -n "$PICK_RESULT" ]; then
    PROJECT_NAME="${PICK_RESULT%%:*}"
    MISSION_TITLE="${PICK_RESULT#*:}"

    # Find project path from name
    PROJECT_PATH=""
    for i in "${!PROJECT_NAMES[@]}"; do
      if [ "${PROJECT_NAMES[$i]}" = "$PROJECT_NAME" ]; then
        PROJECT_PATH="${PROJECT_PATHS[$i]}"
        break
      fi
    done

    # Validate mission project exists
    if [ -z "$PROJECT_PATH" ]; then
      KNOWN_PROJECTS=$(printf '%s\n' "${PROJECT_NAMES[@]}" | sort | sed 's/^/  • /')
      log error "Mission references unknown project: $PROJECT_NAME"
      log error "Known projects:"
      echo "$KNOWN_PROJECTS"
      notify "Mission error: Unknown project '$PROJECT_NAME'.
Known projects:
$KNOWN_PROJECTS"
      exit 1
    fi
  else
    # No mission picked: autonomous mode
    PROJECT_IDX=$RECOMMENDED_PROJECT_IDX
    PROJECT_NAME="${PROJECT_NAMES[$PROJECT_IDX]}"
    PROJECT_PATH="${PROJECT_PATHS[$PROJECT_IDX]}"
  fi

  # Set MISSION_LINE for downstream compatibility (empty = autonomous)
  MISSION_LINE=""
  if [ -n "$MISSION_TITLE" ]; then
    MISSION_LINE="- $MISSION_TITLE"
  fi

  # Define focus area based on autonomous mode
  if [ -z "$MISSION_LINE" ]; then
    # Contemplative mode check: random chance to reflect instead of autonomous work
    # Only triggers when there's no mission and not in WAIT/REVIEW mode (need budget)
    # Skipped entirely when focus mode is active
    if [ "$AUTONOMOUS_MODE" = "deep" ] || [ "$AUTONOMOUS_MODE" = "implement" ]; then
      CONTEMPLATIVE_CHANCE=$("$PYTHON" -c "from app.utils import get_contemplative_chance; print(get_contemplative_chance())" 2>/dev/null || echo "10")
      if ! "$PYTHON" -m app.focus_manager check "$KOAN_ROOT" >/dev/null 2>&1 && "$PYTHON" -m app.contemplative_runner should-run "$CONTEMPLATIVE_CHANCE" 2>/dev/null; then
        log pause "Decision: CONTEMPLATIVE mode (random reflection, chance: ${CONTEMPLATIVE_CHANCE}%)"
        echo "  Action: Running contemplative session instead of autonomous work"
        echo ""
        notify "🪷 Run $RUN_NUM/$MAX_RUNS — Contemplative mode (chance: $CONTEMPLATIVE_CHANCE%)"

        pushd "$INSTANCE" > /dev/null
        log pause "Running contemplative session..."
        (trap '' INT; exec "$PYTHON" -m app.contemplative_runner run \
          --instance "$INSTANCE" \
          --project-name "$PROJECT_NAME" \
          --session-info "Run $RUN_NUM/$MAX_RUNS on $PROJECT_NAME. Mode: $AUTONOMOUS_MODE. Triggered by $CONTEMPLATIVE_CHANCE% contemplative chance.") 2>/dev/null &
        CLAUDE_PID=$!
        wait_for_claude_task
        log pause "Contemplative session ended."
        popd > /dev/null

        # Contemplative session done — increment counter and loop
        count=$((count + 1))
        # Check for pending missions before sleeping
        if has_pending_missions; then
          log koan "Pending missions found after contemplation — skipping sleep"
        else
          set_status "Idle — post-contemplation sleep ($(date '+%H:%M'))"
          log pause "Contemplative session complete. Sleeping ${INTERVAL}s..."
          WAKE_REASON=$("$PYTHON" -m app.loop_manager interruptible-sleep \
            --interval "$INTERVAL" --koan-root "$KOAN_ROOT" --instance "$INSTANCE" 2>/dev/null || echo "timeout")
          [ "$WAKE_REASON" = "mission" ] && log koan "New mission detected during sleep — waking up early"
        fi
        continue
      fi
    fi

    # Focus mode: skip autonomous work entirely — wait for missions
    FOCUS_REMAINING=$("$PYTHON" -m app.focus_manager check "$KOAN_ROOT" 2>/dev/null) && {
      log koan "Focus mode active ($FOCUS_REMAINING remaining) — no missions pending, sleeping"
      set_status "Focus mode — waiting for missions ($FOCUS_REMAINING remaining)"
      WAKE_REASON=$("$PYTHON" -m app.loop_manager interruptible-sleep \
        --interval "$INTERVAL" --koan-root "$KOAN_ROOT" --instance "$INSTANCE" 2>/dev/null || echo "timeout")
      [ "$WAKE_REASON" = "mission" ] && log koan "New mission detected during focus sleep — waking up"
      continue
    }

    # Handle WAIT mode (budget exhausted) — enter pause
    if [ "$AUTONOMOUS_MODE" = "wait" ]; then
      log quota "Decision: WAIT mode (budget exhausted)"
      echo "  Reason: $DECISION_REASON"
      echo "  Action: Entering pause mode (will auto-resume after 5h)"
      echo ""
      "$PYTHON" "$APP_DIR/send_retrospective.py" "$INSTANCE" "$PROJECT_NAME" 2>/dev/null || true
      "$PYTHON" -m app.pause_manager create "$KOAN_ROOT" "quota"
      notify "⏸️ Koan paused: budget exhausted after $count runs on [$PROJECT_NAME]. Auto-resume in 5h or use /resume."
      continue
    fi

    # Resolve focus area from autonomous mode (Python handles the mapping)
    FOCUS_AREA=$("$PYTHON" -m app.loop_manager resolve-focus --mode "$AUTONOMOUS_MODE" 2>/dev/null || echo "General autonomous work")
  else
    FOCUS_AREA=$("$PYTHON" -m app.loop_manager resolve-focus --mode "$AUTONOMOUS_MODE" --has-mission 2>/dev/null || echo "Execute assigned mission")
  fi

  # Enforce current project state
  echo "$PROJECT_NAME" > "$KOAN_ROOT/.koan-project"
  export KOAN_CURRENT_PROJECT="$PROJECT_NAME"
  export KOAN_CURRENT_PROJECT_PATH="$PROJECT_PATH"

  echo -e "${_C_BOLD}${_C_GREEN}>>> Current project: $PROJECT_NAME${_C_RESET} ($PROJECT_PATH)"
  echo ""

  # Mission lifecycle notification: taken or autonomous
  if [ -n "$MISSION_TITLE" ]; then
    log mission "Decision: MISSION mode (assigned)"
    echo "  Mission: $MISSION_TITLE"
    echo "  Project: $PROJECT_NAME"
    echo ""
    notify "🚀 Run $RUN_NUM/$MAX_RUNS — [$PROJECT_NAME] Mission taken: $MISSION_TITLE"
  else
    ESTIMATED_COST="5.0"
    # Uppercase mode for display (bash 3.2 compatible)
    MODE_UPPER=$(echo "$AUTONOMOUS_MODE" | tr '[:lower:]' '[:upper:]')
    log mission "Decision: $MODE_UPPER mode (estimated cost: ${ESTIMATED_COST}% session)"
    echo "  Reason: $DECISION_REASON"
    echo "  Project: $PROJECT_NAME"
    echo "  Focus: $FOCUS_AREA"
    echo ""
    notify "🚀 Run $RUN_NUM/$MAX_RUNS — Autonomous: ${AUTONOMOUS_MODE} mode on $PROJECT_NAME"
  fi

  # Build complete agent prompt (template + merge policy + deep research + verbose mode)
  PROMPT=$("$PYTHON" -m app.prompt_builder agent \
    --instance "$INSTANCE" \
    --project-name "$PROJECT_NAME" \
    --project-path "$PROJECT_PATH" \
    --run-num "$RUN_NUM" \
    --max-runs "$MAX_RUNS" \
    --autonomous-mode "${AUTONOMOUS_MODE:-implement}" \
    --focus-area "${FOCUS_AREA:-General autonomous work}" \
    --available-pct "${AVAILABLE_PCT:-50}" \
    --mission-title "$MISSION_TITLE")

  # Create pending.md — live progress journal for this run
  "$PYTHON" -m app.loop_manager create-pending \
    --instance "$INSTANCE" \
    --project-name "$PROJECT_NAME" \
    --run-num "$RUN_NUM" \
    --max-runs "$MAX_RUNS" \
    --autonomous-mode "${AUTONOMOUS_MODE:-implement}" \
    --mission-title "$MISSION_TITLE" 2>/dev/null || true

  # Execute next mission, capture JSON output for token tracking
  if [ -n "$MISSION_TITLE" ]; then
    set_status "Run $RUN_NUM/$MAX_RUNS — executing mission on $PROJECT_NAME"
  else
    MODE_UPPER_STATUS=$(echo "$AUTONOMOUS_MODE" | tr '[:lower:]' '[:upper:]')
    set_status "Run $RUN_NUM/$MAX_RUNS — $MODE_UPPER_STATUS on $PROJECT_NAME"
  fi
  cd "$PROJECT_PATH"
  MISSION_START_TIME=$(date +%s)
  CLAUDE_OUT="$(mktemp)"
  CLAUDE_ERR="$(mktemp)"
  MISSION_FLAGS=$(KOAN_MODE="$AUTONOMOUS_MODE" "$PYTHON" -c "import os; from app.utils import get_claude_flags_for_role; print(get_claude_flags_for_role('mission', os.environ['KOAN_MODE']))" 2>/dev/null || echo "")
  # Run claude with graceful CTRL-C protection (background + wait pattern)
  # Child ignores SIGINT so first CTRL-C only warns; double CTRL-C sends SIGTERM via on_sigint
  # shellcheck disable=SC2086
  (trap '' INT; exec claude -p "$PROMPT" --allowedTools Bash,Read,Write,Glob,Grep,Edit --output-format json $MISSION_FLAGS) > "$CLAUDE_OUT" 2>"$CLAUDE_ERR" &
  CLAUDE_PID=$!
  wait_for_claude_task

  # Extract text from JSON for display (no jq dependency)
  CLAUDE_TEXT=$("$PYTHON" -m app.mission_runner parse-output "$CLAUDE_OUT" 2>/dev/null || cat "$CLAUDE_OUT")
  echo "$CLAUDE_TEXT"

  # Post-mission processing pipeline (usage, quota, pending, reflection, auto-merge)
  set_status "Run $RUN_NUM/$MAX_RUNS — post-mission processing"
  POST_MISSION_STDERR=$(mktemp)
  POST_MISSION_RESULT=$("$PYTHON" -m app.mission_runner post-mission \
    --instance "$INSTANCE" \
    --project-name "$PROJECT_NAME" \
    --project-path "$PROJECT_PATH" \
    --run-num "$RUN_NUM" \
    --exit-code "$CLAUDE_EXIT" \
    --stdout-file "$CLAUDE_OUT" \
    --stderr-file "$CLAUDE_ERR" \
    --mission-title "$MISSION_TITLE" \
    --autonomous-mode "${AUTONOMOUS_MODE:-implement}" \
    --start-time "$MISSION_START_TIME" 2>"$POST_MISSION_STDERR")
  POST_EXIT=$?

  # Log post-mission activity
  while IFS= read -r line; do
    case "$line" in
      PENDING_ARCHIVED) log health "pending.md archived to journal (Claude didn't clean up)" ;;
      AUTO_MERGE\|*) log git "Auto-merge checked for ${line#AUTO_MERGE|}" ;;
    esac
  done < "$POST_MISSION_STDERR"
  rm -f "$POST_MISSION_STDERR"

  # Handle quota exhaustion (exit code 2 from post-mission)
  if [ $POST_EXIT -eq 2 ]; then
    RESET_DISPLAY=$(echo "$POST_MISSION_RESULT" | cut -d'|' -f2)
    RESUME_MSG=$(echo "$POST_MISSION_RESULT" | cut -d'|' -f3)
    log quota "Quota reached. $RESET_DISPLAY"

    # Commit journal update
    cd "$INSTANCE"
    git add -A
    git diff --cached --quiet || \
      { git commit -m "koan: quota exhausted $(date +%Y-%m-%d-%H:%M)" && \
        git push origin main 2>/dev/null; } || true

    notify "⚠️ Claude quota exhausted. $RESET_DISPLAY

Koan paused after $count runs. $RESUME_MSG or use /resume to restart manually."
    rm -f "$CLAUDE_OUT" "$CLAUDE_ERR"
    CLAUDE_OUT=""
    continue  # Go back to start of loop (will enter pause mode)
  fi
  rm -f "$CLAUDE_OUT" "$CLAUDE_ERR"
  CLAUDE_OUT=""

  # Report result
  if [ $CLAUDE_EXIT -eq 0 ]; then
    log mission "Run $RUN_NUM/$MAX_RUNS — [$PROJECT_NAME] completed successfully"
  else
    if [ -n "$MISSION_TITLE" ]; then
      notify "❌ Run $RUN_NUM/$MAX_RUNS — [$PROJECT_NAME] Mission failed: $MISSION_TITLE"
    else
      notify "❌ Run $RUN_NUM/$MAX_RUNS — [$PROJECT_NAME] Run failed"
    fi
  fi

  # Commit instance results
  cd "$INSTANCE"
  git add -A
  git diff --cached --quiet || \
    { git commit -m "koan: $(date +%Y-%m-%d-%H:%M)" && \
      git push origin main 2>/dev/null; } || true

  count=$((count + 1))

  # Periodic git sync (every GIT_SYNC_INTERVAL runs)
  if [ $((count % GIT_SYNC_INTERVAL)) -eq 0 ]; then
    log git "Periodic git sync (run $count)..."
    for i in "${!PROJECT_NAMES[@]}"; do
      "$PYTHON" "$GIT_SYNC" "$INSTANCE" "${PROJECT_NAMES[$i]}" "${PROJECT_PATHS[$i]}" 2>/dev/null || true
    done
  fi

  # Check if max runs reached — enter pause mode instead of exiting
  if [ $count -ge $MAX_RUNS ]; then
    log koan "Max runs ($MAX_RUNS) reached. Running evening ritual before pause."
    "$PYTHON" "$RITUALS" evening "$INSTANCE" || true
    log pause "Entering pause mode (auto-resume in 5h)."
    "$PYTHON" -m app.pause_manager create "$KOAN_ROOT" "max_runs"
    notify "⏸️ Koan paused: $MAX_RUNS runs completed. Auto-resume in 5h or use /resume to restart."
    # Don't reset count here — it gets reset on auto-resume or manual /resume
    continue  # Go back to start of loop (will enter pause mode)
  fi

  # Check for pending missions before sleeping — skip sleep if work is waiting
  if has_pending_missions; then
    log koan "Pending missions found — skipping sleep, starting next run immediately"
    set_status "Run $RUN_NUM/$MAX_RUNS — done, next run starting"
  else
    set_status "Idle — sleeping ${INTERVAL}s ($(date '+%H:%M'))"
    log koan "Sleeping ${INTERVAL}s (checking for new missions every 10s)..."
    WAKE_REASON=$("$PYTHON" -m app.loop_manager interruptible-sleep \
      --interval "$INTERVAL" --koan-root "$KOAN_ROOT" --instance "$INSTANCE" 2>/dev/null || echo "timeout")
    if [ "$WAKE_REASON" = "mission" ]; then
      log koan "New mission detected during sleep — waking up early"
      set_status "Run $RUN_NUM/$MAX_RUNS — done, new mission detected"
    fi
  fi
done

# This point is only reached via /stop command
rm -f "$KOAN_ROOT/.koan-status"
"$PYTHON" -m app.pid_manager release-pid run "$KOAN_ROOT" 2>/dev/null || true
log koan "Session ended. $count runs executed."

# End-of-session daily report check
"$PYTHON" "$DAILY_REPORT" 2>/dev/null || true
