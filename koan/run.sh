#!/bin/bash
# Kōan — Main run loop
# Pulls missions, executes them via Claude Code CLI, commits results.
# Sends Telegram notifications at each mission lifecycle step.

set -euo pipefail

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

# Ensure KOAN_ROOT is set - mandatory from config
if [ -z "${KOAN_ROOT:-}" ]; then
  log error "KOAN_ROOT environment variable not set."
  exit 1
fi

INSTANCE="$KOAN_ROOT/instance"
APP_DIR="$KOAN_ROOT/koan/app"
NOTIFY="$APP_DIR/notify.py"
DAILY_REPORT="$APP_DIR/daily_report.py"
MISSION_SUMMARY="$APP_DIR/mission_summary.py"
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
if [ ${#PROJECT_NAMES[@]} -gt 5 ]; then
  log error "Max 5 projects allowed. You have ${#PROJECT_NAMES[@]}."
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

# Load config from config.yaml (source of truth for behavioral settings)
MAX_RUNS=$("$PYTHON" -c "from app.utils import get_max_runs; print(get_max_runs())" 2>/dev/null || echo "20")
INTERVAL=$("$PYTHON" -c "from app.utils import get_interval_seconds; print(get_interval_seconds())" 2>/dev/null || echo "300")

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
trap cleanup TERM

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

  # Check for pause — contemplative mode
  if [ -f "$KOAN_ROOT/.koan-pause" ]; then
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

    # ~50% chance of a contemplative session
    STEP_IN_PROBABILITY=50
    ROLL=$((RANDOM % 100))
    if [ $ROLL -lt $STEP_IN_PROBABILITY ]; then
      log pause "A thought stirs..."
      PROJECT_NAME="${PROJECT_NAMES[0]}"
      PROJECT_PATH="${PROJECT_PATHS[0]}"
      echo "$PROJECT_NAME" > "$KOAN_ROOT/.koan-project"
      export KOAN_CURRENT_PROJECT="$PROJECT_NAME"
      export KOAN_CURRENT_PROJECT_PATH="$PROJECT_PATH"

      CONTEMPLATE_PROMPT=$("$PYTHON" -m app.prompt_builder contemplative \
        --instance "$INSTANCE" \
        --project-name "$PROJECT_NAME" \
        --session-info "Pause mode. Run loop paused.")

      cd "$INSTANCE"
      CONTEMPLATE_FLAGS=$("$PYTHON" -c "from app.utils import get_claude_flags_for_role; print(get_claude_flags_for_role('contemplative'))" 2>/dev/null || echo "")
      # shellcheck disable=SC2086
      log pause "Running contemplative session..."
      (trap '' INT; exec claude -p "$CONTEMPLATE_PROMPT" --allowedTools Read,Write,Glob,Grep --max-turns 5 $CONTEMPLATE_FLAGS) 2>/dev/null &
      CLAUDE_PID=$!
      wait_for_claude_task
      log pause "Contemplative session ended."
    fi

    # Sleep in 5s increments — allows /resume or auto-resume to take effect quickly
    for ((s=0; s<60; s++)); do
      [ ! -f "$KOAN_ROOT/.koan-pause" ] && break
      sleep 5
    done
    continue
  fi

  RUN_NUM=$((count + 1))
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
    if [ "$AUTONOMOUS_MODE" = "deep" ] || [ "$AUTONOMOUS_MODE" = "implement" ]; then
      CONTEMPLATIVE_CHANCE=$("$PYTHON" -c "from app.utils import get_contemplative_chance; print(get_contemplative_chance())" 2>/dev/null || echo "10")
      CONTEMPLATE_ROLL=$((RANDOM % 100))
      if [ "$CONTEMPLATE_ROLL" -lt "$CONTEMPLATIVE_CHANCE" ]; then
        log pause "Decision: CONTEMPLATIVE mode (random reflection)"
        echo "  Roll: $CONTEMPLATE_ROLL < $CONTEMPLATIVE_CHANCE (threshold)"
        echo "  Action: Running contemplative session instead of autonomous work"
        echo ""
        notify "🪷 Run $RUN_NUM/$MAX_RUNS — Contemplative mode (rolled $CONTEMPLATE_ROLL < $CONTEMPLATIVE_CHANCE%)"

        # Run contemplative session (same as pause mode contemplation, but doesn't enter pause)
        CONTEMPLATE_PROMPT=$("$PYTHON" -m app.prompt_builder contemplative \
          --instance "$INSTANCE" \
          --project-name "$PROJECT_NAME" \
          --session-info "Run $RUN_NUM/$MAX_RUNS on $PROJECT_NAME. Mode: $AUTONOMOUS_MODE. Triggered by $CONTEMPLATIVE_CHANCE% contemplative chance.")

        cd "$INSTANCE"
        CONTEMPLATE_FLAGS=$("$PYTHON" -c "from app.utils import get_claude_flags_for_role; print(get_claude_flags_for_role('contemplative'))" 2>/dev/null || echo "")
        # shellcheck disable=SC2086
        log pause "Running contemplative session..."
        (trap '' INT; exec claude -p "$CONTEMPLATE_PROMPT" --allowedTools Read,Write,Glob,Grep --max-turns 5 $CONTEMPLATE_FLAGS) 2>/dev/null &
        CLAUDE_PID=$!
        wait_for_claude_task
        log pause "Contemplative session ended."

        # Contemplative session done — increment counter and loop
        count=$((count + 1))
        log pause "Contemplative session complete. Sleeping ${INTERVAL}s..."
        sleep "$INTERVAL"
        continue
      fi
    fi

    case "$AUTONOMOUS_MODE" in
      wait)
        log quota "Decision: WAIT mode (budget exhausted)"
        echo "  Reason: $DECISION_REASON"
        echo "  Action: Entering pause mode (will auto-resume after 5h)"
        echo ""
        # Send retrospective and enter pause mode
        "$PYTHON" "$APP_DIR/send_retrospective.py" "$INSTANCE" "$PROJECT_NAME" 2>/dev/null || true
        # Create pause via pause_manager
        "$PYTHON" -m app.pause_manager create "$KOAN_ROOT" "quota"
        notify "⏸️ Koan paused: budget exhausted after $count runs on [$PROJECT_NAME]. Auto-resume in 5h or use /resume."
        continue  # Go back to start of loop (will enter pause mode)
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
  PENDING_FILE="$INSTANCE/journal/pending.md"
  JOURNAL_DIR="$INSTANCE/journal/$(date +%Y-%m-%d)"
  mkdir -p "$JOURNAL_DIR"
  if [ -n "$MISSION_TITLE" ]; then
    cat > "$PENDING_FILE" <<EOF
# Mission: $MISSION_TITLE
Project: $PROJECT_NAME
Started: $(date '+%Y-%m-%d %H:%M:%S')
Run: $RUN_NUM/$MAX_RUNS
Mode: ${AUTONOMOUS_MODE:-mission}

---
EOF
  else
    cat > "$PENDING_FILE" <<EOF
# Autonomous run
Project: $PROJECT_NAME
Started: $(date '+%Y-%m-%d %H:%M:%S')
Run: $RUN_NUM/$MAX_RUNS
Mode: $AUTONOMOUS_MODE

---
EOF
  fi

  # Execute next mission, capture JSON output for token tracking
  cd "$PROJECT_PATH"
  MISSION_START_TIME=$(date +%s)
  CLAUDE_OUT="$(mktemp)"
  CLAUDE_ERR="$(mktemp)"
  MISSION_FLAGS=$("$PYTHON" -c "from app.utils import get_claude_flags_for_role; print(get_claude_flags_for_role('mission', '$AUTONOMOUS_MODE'))" 2>/dev/null || echo "")
  # Run claude with graceful CTRL-C protection (background + wait pattern)
  # Child ignores SIGINT so first CTRL-C only warns; double CTRL-C sends SIGTERM via on_sigint
  # shellcheck disable=SC2086
  (trap '' INT; exec claude -p "$PROMPT" --allowedTools Bash,Read,Write,Glob,Grep,Edit --output-format json $MISSION_FLAGS) > "$CLAUDE_OUT" 2>"$CLAUDE_ERR" &
  CLAUDE_PID=$!
  wait_for_claude_task

  # Extract text from JSON for display and quota detection
  CLAUDE_TEXT=""
  if command -v jq &>/dev/null && [ -s "$CLAUDE_OUT" ]; then
    CLAUDE_TEXT=$(jq -r '.result // .content // .text // empty' "$CLAUDE_OUT" 2>/dev/null || cat "$CLAUDE_OUT")
  else
    CLAUDE_TEXT=$(cat "$CLAUDE_OUT")
  fi
  echo "$CLAUDE_TEXT"

  # Update token usage state from JSON output
  "$PYTHON" "$USAGE_ESTIMATOR" update "$CLAUDE_OUT" "$USAGE_STATE" "$INSTANCE/usage.md" 2>/dev/null || true

  # Check for quota exhaustion (in both text output and stderr)
  CLAUDE_COMBINED="$(cat "$CLAUDE_ERR" 2>/dev/null; echo "$CLAUDE_TEXT")"
  if echo "$CLAUDE_COMBINED" | grep -q "out of extra usage\|quota.*reached\|rate limit"; then
    RESET_INFO=$(echo "$CLAUDE_COMBINED" | grep -o "resets.*" | head -1 || echo "")
    log quota "Quota reached. $RESET_INFO"

    # Parse reset time to get actual timestamp
    RESET_PARSER="$APP_DIR/reset_parser.py"
    RESET_PARSED=$("$PYTHON" "$RESET_PARSER" parse "$RESET_INFO" 2>/dev/null || echo "|$RESET_INFO")
    RESET_TIMESTAMP=$(echo "$RESET_PARSED" | cut -d'|' -f1)
    RESET_DISPLAY=$(echo "$RESET_PARSED" | cut -d'|' -f2)

    # Calculate time until reset for display
    if [ -n "$RESET_TIMESTAMP" ]; then
      RESET_UNTIL=$("$PYTHON" "$RESET_PARSER" until "$RESET_TIMESTAMP" 2>/dev/null || echo "unknown")
      RESUME_MSG="Auto-resume at reset time (~$RESET_UNTIL)"
    else
      RESET_TIMESTAMP=$(date +%s)  # Fallback: current time + 5h (old behavior)
      RESET_TIMESTAMP=$((RESET_TIMESTAMP + 5 * 3600))
      RESUME_MSG="Auto-resume in ~5h (reset time unknown)"
    fi

    # Write to journal (per-project)
    JOURNAL_DIR="$INSTANCE/journal/$(date +%Y-%m-%d)"
    JOURNAL_FILE="$JOURNAL_DIR/$PROJECT_NAME.md"
    mkdir -p "$JOURNAL_DIR"
    cat >> "$JOURNAL_FILE" <<EOF

## Quota Exhausted — $(date '+%H:%M:%S')

Claude quota reached after $count runs (project: $PROJECT_NAME). $RESET_DISPLAY

$RESUME_MSG or use \`/resume\` to restart manually.
EOF

    # Create pause via pause_manager with parsed reset timestamp
    "$PYTHON" -m app.pause_manager create "$KOAN_ROOT" "quota" "$RESET_TIMESTAMP" "$RESET_DISPLAY"

    # Commit journal update
    cd "$INSTANCE"
    git add -A
    git diff --cached --quiet || \
      git commit -m "koan: quota exhausted $(date +%Y-%m-%d-%H:%M)" && \
      git push origin main 2>/dev/null || true

    notify "⚠️ Claude quota exhausted. $RESET_DISPLAY

Koan paused after $count runs. $RESUME_MSG or use /resume to restart manually."
    rm -f "$CLAUDE_OUT" "$CLAUDE_ERR"
    CLAUDE_OUT=""
    continue  # Go back to start of loop (will enter pause mode)
  fi
  rm -f "$CLAUDE_OUT" "$CLAUDE_ERR"
  CLAUDE_OUT=""

  # If Claude didn't clean up pending.md, archive it to daily journal
  PENDING_FILE="$INSTANCE/journal/pending.md"
  if [ -f "$PENDING_FILE" ]; then
    JOURNAL_DIR="$INSTANCE/journal/$(date +%Y-%m-%d)"
    mkdir -p "$JOURNAL_DIR"
    JOURNAL_FILE="$JOURNAL_DIR/$PROJECT_NAME.md"
    echo "" >> "$JOURNAL_FILE"
    echo "## Run $RUN_NUM — $(date '+%H:%M') (auto-archived from pending)" >> "$JOURNAL_FILE"
    echo "" >> "$JOURNAL_FILE"
    cat "$PENDING_FILE" >> "$JOURNAL_FILE"
    rm -f "$PENDING_FILE"
    log health "pending.md archived to journal (Claude didn't clean up)"
  fi

  # Report result
  # NOTE: The Claude agent writes its own conclusion to outbox.md
  # (summary + koan). No need for notify() or mission_summary.py here —
  # those caused triple-repeated conclusions on Telegram.
  if [ $CLAUDE_EXIT -eq 0 ]; then
    log mission "Run $RUN_NUM/$MAX_RUNS — [$PROJECT_NAME] completed successfully"

    # Post-mission reflection for significant missions (writes to shared-journal.md)
    MISSION_END_TIME=$(date +%s)
    MISSION_DURATION_MINUTES=$(( (MISSION_END_TIME - MISSION_START_TIME) / 60 ))
    POST_MISSION_REFLECTION="$APP_DIR/post_mission_reflection.py"
    if [ -n "$MISSION_TITLE" ]; then
      "$PYTHON" "$POST_MISSION_REFLECTION" "$INSTANCE" "$MISSION_TITLE" "$MISSION_DURATION_MINUTES" 2>/dev/null || true
    else
      # Autonomous mode — use mode name as mission text
      "$PYTHON" "$POST_MISSION_REFLECTION" "$INSTANCE" "Autonomous $AUTONOMOUS_MODE on $PROJECT_NAME" "$MISSION_DURATION_MINUTES" 2>/dev/null || true
    fi

    # Auto-merge logic (if on koan/* branch)
    cd "$PROJECT_PATH"
    CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
    if [[ "$CURRENT_BRANCH" == koan/* ]]; then
      log git "Checking auto-merge for $CURRENT_BRANCH..."
      GIT_AUTO_MERGE="$APP_DIR/git_auto_merge.py"
      if "$PYTHON" "$GIT_AUTO_MERGE" "$INSTANCE" "$PROJECT_NAME" "$PROJECT_PATH" "$CURRENT_BRANCH" 2>&1; then
        log git "Auto-merge completed for $CURRENT_BRANCH"
      else
        log git "Auto-merge skipped or failed for $CURRENT_BRANCH (see journal)"
      fi
    fi
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
    git commit -m "koan: $(date +%Y-%m-%d-%H:%M)" && \
    git push origin main 2>/dev/null || true

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

  log koan "Sleeping ${INTERVAL}s..."
  sleep $INTERVAL
done

# This point is only reached via /stop command
log koan "Session ended. $count runs executed."

# End-of-session daily report check
"$PYTHON" "$DAILY_REPORT" 2>/dev/null || true
