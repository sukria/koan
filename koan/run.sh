#!/bin/bash
# Kōan — Main run loop
# Pulls missions, executes them via Claude Code CLI, commits results.
# Sends Telegram notifications at each mission lifecycle step.

set -euo pipefail

KOAN_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INSTANCE="$KOAN_ROOT/instance"
NOTIFY="$(dirname "$0")/notify.py"

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

count=0

echo "[koan] Starting. Max runs: $MAX_RUNS, interval: ${INTERVAL}s"
notify "Koan starting — $MAX_RUNS max runs, ${INTERVAL}s interval"

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
  notify "Run $RUN_NUM/$MAX_RUNS started"

  # Extract project from next pending mission
  MISSION_LINE=$(grep -m1 "^- " "$INSTANCE/missions.md" 2>/dev/null || echo "")
  if [[ "$MISSION_LINE" =~ \[project:([a-zA-Z0-9_-]+)\] ]]; then
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
    # No project tag: default to first project
    PROJECT_NAME="${PROJECT_NAMES[0]}"
    PROJECT_PATH="${PROJECT_PATHS[0]}"
  fi

  echo "[koan] Project: $PROJECT_NAME ($PROJECT_PATH)"

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
  CLAUDE_OUT=$(mktemp)
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
    break
  fi
  rm -f "$CLAUDE_OUT"

  # Report result
  if [ $CLAUDE_EXIT -eq 0 ]; then
    notify "Run $RUN_NUM/$MAX_RUNS completed"
  else
    notify "Run $RUN_NUM/$MAX_RUNS failed"
  fi

  # Commit instance results
  cd "$INSTANCE"
  git add -A
  git diff --cached --quiet || \
    git commit -m "koan: $(date +%Y-%m-%d-%H:%M)" && \
    git push origin main 2>/dev/null || true

  count=$((count + 1))

  if [ $count -lt $MAX_RUNS ]; then
    echo "[koan] Sleeping ${INTERVAL}s..."
    sleep $INTERVAL
  fi
done

echo "[koan] Session complete. $count runs executed."
notify "Session complete — $count runs executed"
