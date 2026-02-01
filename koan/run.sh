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
PROJECT_PATH=${KOAN_PROJECT_PATH:-"/path/to/your-project"}

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

  # Build prompt from template, replacing placeholders
  PROMPT=$(sed \
    -e "s|{INSTANCE}|$INSTANCE|g" \
    -e "s|{PROJECT_PATH}|$PROJECT_PATH|g" \
    -e "s|{RUN_NUM}|$RUN_NUM|g" \
    -e "s|{MAX_RUNS}|$MAX_RUNS|g" \
    "$(dirname "$0")/system-prompt.md")

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

    # Write to journal
    JOURNAL_FILE="$INSTANCE/journal/$(date +%Y-%m-%d).md"
    mkdir -p "$(dirname "$JOURNAL_FILE")"
    cat >> "$JOURNAL_FILE" <<EOF

## Quota Exhausted — $(date '+%H:%M:%S')

Claude quota reached after $count runs. $RESET_INFO

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
