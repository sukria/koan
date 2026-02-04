#!/bin/bash
# Kōan — Main run loop
# Pulls missions, executes them via Claude Code CLI, commits results.
# Sends Telegram notifications at each mission lifecycle step.

set -euo pipefail

# Ensure KOAN_ROOT is set - mandatory from config
if [ -z "${KOAN_ROOT:-}" ]; then
  echo "[koan] Error: KOAN_ROOT environment variable not set."
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
  echo "[koan] No instance/ directory found. Run: cp -r instance.example instance"
  exit 1
fi

# Config via env vars (or defaults)
MAX_RUNS=${KOAN_MAX_RUNS:-25}
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

# Resolve CLI binary from provider config (claude or copilot)
CLI_BIN=$("$PYTHON" -c "from app.utils import get_cli_binary_for_shell; print(get_cli_binary_for_shell())" 2>/dev/null || echo "claude")
CLI_PROVIDER=$("$PYTHON" -c "from app.utils import get_cli_provider_name; print(get_cli_provider_name())" 2>/dev/null || echo "claude")

# Set git identity for koan commits (overrides local git config)
if [ -n "${KOAN_EMAIL:-}" ]; then
  export GIT_AUTHOR_NAME="Koan"
  export GIT_AUTHOR_EMAIL="$KOAN_EMAIL"
  export GIT_COMMITTER_NAME="Koan"
  export GIT_COMMITTER_EMAIL="$KOAN_EMAIL"
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

cleanup() {
  [ -n "$CLAUDE_OUT" ] && rm -f "$CLAUDE_OUT"
  [ -n "${CLAUDE_ERR:-}" ] && rm -f "$CLAUDE_ERR"
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

# Self-reflection: every 10 sessions, trigger introspection
echo "[koan] Checking self-reflection trigger..."
"$PYTHON" "$SELF_REFLECTION" "$INSTANCE" --notify || true

# Check start_on_pause config: create .koan-pause if true (boot into pause mode)
START_ON_PAUSE=$("$PYTHON" -c "from app.utils import get_start_on_pause; print('true' if get_start_on_pause() else 'false')" 2>/dev/null || echo "false")
if [ "$START_ON_PAUSE" = "true" ] && [ ! -f "$KOAN_ROOT/.koan-pause" ]; then
  echo "[koan] start_on_pause=true in config. Entering pause mode."
  touch "$KOAN_ROOT/.koan-pause"
fi

echo "[koan] Starting. Max runs: $MAX_RUNS, interval: ${INTERVAL}s, CLI: $CLI_PROVIDER ($CLI_BIN)"
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

# Morning ritual: run at first iteration (before main loop starts)
echo "[koan] Running morning ritual..."
"$PYTHON" "$RITUALS" morning "$INSTANCE" || true

##
# Kōan main loop - infinite, never exits unless /stop requested
##
while true; do

  # Check for stop request - graceful shutdown (ONLY way to exit the loop)
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

    # Check auto-resume: if paused due to quota and 5h have passed, resume
    if [ -f "$KOAN_ROOT/.koan-pause-reason" ]; then
      PAUSE_REASON=$(head -1 "$KOAN_ROOT/.koan-pause-reason")
      PAUSE_TIMESTAMP=$(tail -1 "$KOAN_ROOT/.koan-pause-reason")
      CURRENT_TIMESTAMP=$(date +%s)
      TIME_ELAPSED=$((CURRENT_TIMESTAMP - PAUSE_TIMESTAMP))
      FIVE_HOURS=$((5 * 60 * 60))

      if [ $TIME_ELAPSED -ge $FIVE_HOURS ]; then
        echo "[koan] Auto-resume: 5h have passed since pause ($PAUSE_REASON)"
        rm -f "$KOAN_ROOT/.koan-pause" "$KOAN_ROOT/.koan-pause-reason"
        count=0  # Reset run counter on auto-resume
        notify "🔄 Koan auto-resumed after 5h cooldown (reason: $PAUSE_REASON)"
        continue
      fi
    fi

    # Check for manual /resume (pause file removed but we're still in pause block from previous iteration)
    # This shouldn't normally happen since the continue at end of sleep loop would catch it,
    # but if we reach here with no pause file, we've been manually resumed
    if [ ! -f "$KOAN_ROOT/.koan-pause" ]; then
      echo "[koan] Manual resume detected"
      count=0  # Reset run counter on manual resume too
      continue
    fi

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
      CONTEMPLATE_FLAGS=$("$PYTHON" -c "from app.utils import get_claude_flags_for_role; print(get_claude_flags_for_role('contemplative'))" 2>/dev/null || echo "")
      CONTEMPLATE_TOOLS=$("$PYTHON" -c "from app.utils import get_tool_flags_for_shell; print(get_tool_flags_for_shell('Read,Write,Glob,Grep'))" 2>/dev/null || echo "--allowedTools Read,Write,Glob,Grep")
      set +e
      # shellcheck disable=SC2086
      $CLI_BIN -p "$CONTEMPLATE_PROMPT" $CONTEMPLATE_TOOLS --max-turns 3 $CONTEMPLATE_FLAGS 2>/dev/null
      set -e
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
  echo "=== Run $RUN_NUM/$MAX_RUNS — $(date '+%Y-%m-%d %H:%M:%S') ==="

  # Refresh usage.md from accumulated token state (handles session/weekly resets)
  # On first run, trust existing usage.md as source of truth (don't reset counters)
  if [ $count -gt 0 ]; then
    "$PYTHON" "$USAGE_ESTIMATOR" refresh "$USAGE_STATE" "$INSTANCE/usage.md" 2>/dev/null || true
  fi

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

  # Inject due recurring missions into pending queue
  RECURRING_SCHEDULER="$APP_DIR/recurring_scheduler.py"
  "$PYTHON" "$RECURRING_SCHEDULER" "$INSTANCE" 2>/dev/null || true

  # Pick next mission using Claude-based intelligent picker
  LAST_PROJECT=$(cat "$KOAN_ROOT/.koan-project" 2>/dev/null || echo "")
  PICK_MISSION="$APP_DIR/pick_mission.py"
  PICK_STDERR=$(mktemp)
  PICK_RESULT=$("$PYTHON" "$PICK_MISSION" "$INSTANCE" "$KOAN_PROJECTS" "$RUN_NUM" "$AUTONOMOUS_MODE" "$LAST_PROJECT" 2>"$PICK_STDERR" || echo "")
  if [ -s "$PICK_STDERR" ]; then
    echo "[koan] Mission picker stderr:"
    cat "$PICK_STDERR"
  fi
  rm -f "$PICK_STDERR"
  echo "[koan] Picker result: '${PICK_RESULT:-<empty>}'"

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
      echo "[koan] Error: Mission references unknown project: $PROJECT_NAME"
      echo "[koan] Known projects: ${PROJECT_NAMES[*]}"
      notify "Mission error: Unknown project '$PROJECT_NAME'. Known projects: ${PROJECT_NAMES[*]}"
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
        echo "Decision: CONTEMPLATIVE mode (random reflection)"
        echo "  Roll: $CONTEMPLATE_ROLL < $CONTEMPLATIVE_CHANCE (threshold)"
        echo "  Action: Running contemplative session instead of autonomous work"
        echo ""
        notify "🪷 Run $RUN_NUM/$MAX_RUNS — Contemplative mode (rolled $CONTEMPLATE_ROLL < $CONTEMPLATIVE_CHANCE%)"

        # Run contemplative session (same as pause mode contemplation, but doesn't enter pause)
        CONTEMPLATE_PROMPT=$(sed \
          -e "s|{INSTANCE}|$INSTANCE|g" \
          -e "s|{PROJECT_NAME}|$PROJECT_NAME|g" \
          "$KOAN_ROOT/koan/system-prompts/contemplative.md")

        cd "$INSTANCE"
        CONTEMPLATE_FLAGS=$("$PYTHON" -c "from app.utils import get_claude_flags_for_role; print(get_claude_flags_for_role('contemplative'))" 2>/dev/null || echo "")
        CONTEMPLATE_TOOLS=$("$PYTHON" -c "from app.utils import get_tool_flags_for_shell; print(get_tool_flags_for_shell('Read,Write,Glob,Grep'))" 2>/dev/null || echo "--allowedTools Read,Write,Glob,Grep")
        set +e
        # shellcheck disable=SC2086
        $CLI_BIN -p "$CONTEMPLATE_PROMPT" $CONTEMPLATE_TOOLS --max-turns 3 $CONTEMPLATE_FLAGS 2>/dev/null
        set -e

        # Contemplative session done — increment counter and loop
        count=$((count + 1))
        echo "[koan] Contemplative session complete. Sleeping ${INTERVAL}s..."
        sleep "$INTERVAL"
        continue
      fi
    fi

    case "$AUTONOMOUS_MODE" in
      wait)
        echo "Decision: WAIT mode (budget exhausted)"
        echo "  Reason: $DECISION_REASON"
        echo "  Action: Entering pause mode (will auto-resume after 5h)"
        echo ""
        # Send retrospective and enter pause mode
        "$PYTHON" "$APP_DIR/send_retrospective.py" "$INSTANCE" "$PROJECT_NAME" 2>/dev/null || true
        # Create pause file + reason file for auto-resume
        touch "$KOAN_ROOT/.koan-pause"
        echo "quota" > "$KOAN_ROOT/.koan-pause-reason"
        echo "$(date +%s)" >> "$KOAN_ROOT/.koan-pause-reason"
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

  # Build mission instruction for agent prompt
  if [ -n "$MISSION_TITLE" ]; then
    MISSION_INSTRUCTION="Your assigned mission is: **${MISSION_TITLE}** Mark it In Progress in missions.md. Execute it thoroughly. Take your time — go deep, don't rush."
  else
    MISSION_INSTRUCTION="No specific mission assigned. Look for pending missions for ${PROJECT_NAME} in missions.md (check [project:${PROJECT_NAME}] tags and ### project:${PROJECT_NAME} sub-headers). If none found, proceed to autonomous mode."
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
  # Replace mission instruction separately (may contain special chars)
  PROMPT="${PROMPT//\{MISSION_INSTRUCTION\}/$MISSION_INSTRUCTION}"

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

  # Verbose mode: if .koan-verbose exists, instruct agent to mirror pending.md writes to outbox
  if [ -f "$KOAN_ROOT/.koan-verbose" ]; then
    VERBOSE_SECTION="

# Verbose Mode (ACTIVE)

The human has activated verbose mode (/verbose). Every time you write a progress line
to pending.md, you MUST ALSO write the same line to {INSTANCE}/outbox.md so the human
gets real-time updates on Telegram. Use this pattern:

\`\`\`bash
MSG=\"\$(date +%H:%M) — description\"
echo \"\$MSG\" >> {INSTANCE}/journal/pending.md
echo \"\$MSG\" >> {INSTANCE}/outbox.md
\`\`\`

This replaces the single echo to pending.md. Do this for EVERY progress update.
The conclusion message at the end of the mission is still a single write as usual.
"
    VERBOSE_SECTION="${VERBOSE_SECTION//\{INSTANCE\}/$INSTANCE}"
    PROMPT="$PROMPT$VERBOSE_SECTION"
  fi

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
  CLAUDE_OUT="$(mktemp)"
  CLAUDE_ERR="$(mktemp)"
  MISSION_FLAGS=$("$PYTHON" -c "from app.utils import get_claude_flags_for_role; print(get_claude_flags_for_role('mission', '$AUTONOMOUS_MODE'))" 2>/dev/null || echo "")
  MCP_FLAGS=$("$PYTHON" -c "from app.utils import get_mcp_flags_for_shell; print(get_mcp_flags_for_shell())" 2>/dev/null || echo "")
  MISSION_TOOLS=$("$PYTHON" -c "from app.utils import get_tool_flags_for_shell; print(get_tool_flags_for_shell('Bash,Read,Write,Glob,Grep,Edit'))" 2>/dev/null || echo "--allowedTools Bash,Read,Write,Glob,Grep,Edit")
  OUTPUT_FLAGS=$("$PYTHON" -c "from app.utils import get_output_flags_for_shell; print(get_output_flags_for_shell('json'))" 2>/dev/null || echo "--output-format json")
  set +e  # Don't exit on error, we need to check the output
  # shellcheck disable=SC2086
  $CLI_BIN -p "$PROMPT" $MISSION_TOOLS $OUTPUT_FLAGS $MISSION_FLAGS $MCP_FLAGS > "$CLAUDE_OUT" 2>"$CLAUDE_ERR"
  CLAUDE_EXIT=$?
  set -e

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
    echo "[koan] Quota reached. $RESET_INFO"

    # Write to journal (per-project)
    JOURNAL_DIR="$INSTANCE/journal/$(date +%Y-%m-%d)"
    JOURNAL_FILE="$JOURNAL_DIR/$PROJECT_NAME.md"
    mkdir -p "$JOURNAL_DIR"
    cat >> "$JOURNAL_FILE" <<EOF

## Quota Exhausted — $(date '+%H:%M:%S')

Claude quota reached after $count runs (project: $PROJECT_NAME). $RESET_INFO

Koan entering pause mode. Auto-resume in 5h or use \`/resume\` to restart manually.
EOF

    # Create pause file + reason file for auto-resume
    touch "$KOAN_ROOT/.koan-pause"
    echo "quota" > "$KOAN_ROOT/.koan-pause-reason"
    echo "$(date +%s)" >> "$KOAN_ROOT/.koan-pause-reason"

    # Commit journal update
    cd "$INSTANCE"
    git add -A
    git diff --cached --quiet || \
      git commit -m "koan: quota exhausted $(date +%Y-%m-%d-%H:%M)" && \
      git push origin main 2>/dev/null || true

    notify "⚠️ Claude quota exhausted. $RESET_INFO

Koan paused after $count runs. Auto-resume in 5h or use /resume to restart manually."
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
    echo "[koan] pending.md archived to journal (Claude didn't clean up)"
  fi

  # Report result
  # NOTE: The Claude agent writes its own conclusion to outbox.md
  # (summary + koan). No need for notify() or mission_summary.py here —
  # those caused triple-repeated conclusions on Telegram.
  if [ $CLAUDE_EXIT -eq 0 ]; then
    echo "[koan] Run $RUN_NUM/$MAX_RUNS — [$PROJECT_NAME] completed successfully"

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
  if [ $((count % GIT_SYNC_INTERVAL)) -eq 0 ]; then
    echo "[koan] Periodic git sync (run $count)..."
    for i in "${!PROJECT_NAMES[@]}"; do
      "$PYTHON" "$GIT_SYNC" "$INSTANCE" "${PROJECT_NAMES[$i]}" "${PROJECT_PATHS[$i]}" 2>/dev/null || true
    done
  fi

  # Check if max runs reached — enter pause mode instead of exiting
  if [ $count -ge $MAX_RUNS ]; then
    echo "[koan] Max runs ($MAX_RUNS) reached. Running evening ritual before pause."
    "$PYTHON" "$RITUALS" evening "$INSTANCE" || true
    echo "[koan] Entering pause mode (auto-resume in 5h)."
    touch "$KOAN_ROOT/.koan-pause"
    echo "max_runs" > "$KOAN_ROOT/.koan-pause-reason"
    echo "$(date +%s)" >> "$KOAN_ROOT/.koan-pause-reason"
    notify "⏸️ Koan paused: $MAX_RUNS runs completed. Auto-resume in 5h or use /resume to restart."
    # Don't reset count here — it gets reset on auto-resume or manual /resume
    continue  # Go back to start of loop (will enter pause mode)
  fi

  echo "[koan] Sleeping ${INTERVAL}s..."
  sleep $INTERVAL
done

# This point is only reached via /stop command
echo "[koan] Session ended. $count runs executed."

# End-of-session daily report check
"$PYTHON" "$DAILY_REPORT" 2>/dev/null || true
