#!/bin/bash
set -euo pipefail

# =========================================================================
# Kōan Docker Entrypoint — Mounted Binaries Approach
# =========================================================================
# Expects host CLI binaries and auth state mounted as volumes.
# The container is a thin runtime — no auth setup, no binary installs.
#
# Commands:
#   start    — Run both agent loop and Telegram bridge (default)
#   agent    — Run agent loop only
#   bridge   — Run Telegram bridge only
#   test     — Run the test suite
#   shell    — Drop into bash shell
# =========================================================================

KOAN_ROOT="${KOAN_ROOT:-/app}"
PYTHON="${KOAN_ROOT}/.venv/bin/python3"
INSTANCE="${KOAN_ROOT}/instance"
STOPPING=false

log() {
    echo "[koan-docker] $(date +%H:%M:%S) $*"
}

# -------------------------------------------------------------------------
# 1. Verify Mounted Binaries
# -------------------------------------------------------------------------
verify_binaries() {
    local missing=()

    if ! command -v claude >/dev/null 2>&1; then
        missing+=("claude")
    fi
    if ! command -v gh >/dev/null 2>&1; then
        missing+=("gh")
    fi
    if ! command -v git >/dev/null 2>&1; then
        missing+=("git")
    fi

    if [ ${#missing[@]} -gt 0 ]; then
        log "ERROR: Missing binaries: ${missing[*]}"
        log ""
        log "These must be mounted from the host. Example docker-compose.yml volumes:"
        log "  - \$(which claude):/usr/local/bin/claude:ro"
        log "  - \$(which gh):/usr/local/bin/gh:ro"
        log ""
        log "Or run: ./setup-docker.sh to auto-detect and configure mounts."
        return 1
    fi

    log "Binaries OK: claude ✓  gh ✓  git ✓"
}

# -------------------------------------------------------------------------
# 2. Verify Claude Auth
# -------------------------------------------------------------------------
verify_claude_auth() {
    if [ -d "$HOME/.claude" ]; then
        log "Claude config: ~/.claude mounted ✓"
    else
        log "WARNING: ~/.claude not mounted — Claude CLI may not authenticate"
        log "  Add to docker-compose.yml: - \${HOME}/.claude:/home/koan/.claude"
    fi

    # Quick version check (doesn't require auth)
    local version
    version=$(claude --version 2>/dev/null | head -1) || true
    if [ -n "$version" ]; then
        log "Claude CLI: $version"
    else
        log "WARNING: claude binary found but version check failed"
    fi
}

# -------------------------------------------------------------------------
# 3. Verify GitHub CLI Auth
# -------------------------------------------------------------------------
verify_gh_auth() {
    if gh auth status >/dev/null 2>&1; then
        local user
        user=$(gh api user --jq .login 2>/dev/null) || user="unknown"
        log "GitHub CLI: authenticated as $user ✓"
    else
        log "WARNING: gh not authenticated — PR/issue operations will fail"
        log "  Mount ~/.config/gh:/home/koan/.config/gh:ro in docker-compose.yml"
    fi
}

# -------------------------------------------------------------------------
# 4. Instance Directory
# -------------------------------------------------------------------------
setup_instance() {
    if [ ! -d "$INSTANCE" ]; then
        log "Initializing instance/ from template"
        cp -r "$KOAN_ROOT/instance.example" "$INSTANCE"
    fi

    mkdir -p "$INSTANCE/journal" \
             "$INSTANCE/memory/global" \
             "$INSTANCE/memory/projects"

    log "Instance: $INSTANCE ✓"
}

# -------------------------------------------------------------------------
# 5. Verify Project Repos
# -------------------------------------------------------------------------
verify_projects() {
    if [ ! -f "$KOAN_ROOT/projects.yaml" ]; then
        log "WARNING: projects.yaml not found — no project repos configured"
        log "  Create projects.yaml or mount it from the host"
        return 0
    fi

    local count
    count=$(grep -c "path:" "$KOAN_ROOT/projects.yaml" 2>/dev/null) || count=0
    log "Projects: $count configured in projects.yaml"

    # Check each project path is accessible
    while IFS= read -r line; do
        local path
        path=$(echo "$line" | sed 's/.*path: *//' | tr -d '"' | tr -d "'")
        if [ -d "$path" ]; then
            log "  ✓ $path"
        else
            log "  ✗ $path (not mounted — mount it in docker-compose.yml)"
        fi
    done < <(grep "path:" "$KOAN_ROOT/projects.yaml" 2>/dev/null)
}

# -------------------------------------------------------------------------
# 6. Setup Python venv (if not already done)
# -------------------------------------------------------------------------
setup_python() {
    if [ -f "$PYTHON" ]; then
        return 0
    fi

    log "Setting up Python virtualenv..."
    python3 -m venv "$KOAN_ROOT/.venv"
    "$KOAN_ROOT/.venv/bin/pip" install --no-cache-dir \
        -r "$KOAN_ROOT/koan/requirements.txt" pytest >/dev/null 2>&1
    log "Python venv ready ✓"
}

# -------------------------------------------------------------------------
# 7. Process Supervision
# -------------------------------------------------------------------------
AGENT_PID=""
BRIDGE_PID=""

cleanup() {
    if [ "$STOPPING" = true ]; then
        return
    fi
    STOPPING=true
    log "Shutting down..."

    # Send SIGTERM to children
    [ -n "$AGENT_PID" ] && kill "$AGENT_PID" 2>/dev/null || true
    [ -n "$BRIDGE_PID" ] && kill "$BRIDGE_PID" 2>/dev/null || true

    # Wait up to 10s for graceful exit
    local waited=0
    while [ $waited -lt 10 ]; do
        local alive=false
        [ -n "$AGENT_PID" ] && kill -0 "$AGENT_PID" 2>/dev/null && alive=true
        [ -n "$BRIDGE_PID" ] && kill -0 "$BRIDGE_PID" 2>/dev/null && alive=true
        [ "$alive" = false ] && break
        sleep 1
        waited=$((waited + 1))
    done

    # Force kill if still alive
    [ -n "$AGENT_PID" ] && kill -9 "$AGENT_PID" 2>/dev/null || true
    [ -n "$BRIDGE_PID" ] && kill -9 "$BRIDGE_PID" 2>/dev/null || true

    log "Stopped."
    exit 0
}

trap cleanup SIGTERM SIGINT

start_agent() {
    log "Starting agent loop..."
    cd "$KOAN_ROOT" && bash koan/run.sh &
    AGENT_PID=$!
    log "Agent loop PID: $AGENT_PID"
}

start_bridge() {
    log "Starting Telegram bridge..."
    cd "$KOAN_ROOT/koan" && \
        KOAN_ROOT="$KOAN_ROOT" PYTHONPATH="$KOAN_ROOT/koan" \
        "$PYTHON" app/awake.py &
    BRIDGE_PID=$!
    log "Telegram bridge PID: $BRIDGE_PID"
}

# Monitor child processes — if one dies, restart it
monitor_children() {
    while true; do
        sleep 5

        if [ "$STOPPING" = true ]; then
            return
        fi

        # Write heartbeat
        date +%s > "$KOAN_ROOT/.koan-heartbeat"

        # Check agent
        if [ -n "$AGENT_PID" ] && ! kill -0 "$AGENT_PID" 2>/dev/null; then
            wait "$AGENT_PID" 2>/dev/null || true
            log "Agent loop exited — restarting..."
            start_agent
        fi

        # Check bridge
        if [ -n "$BRIDGE_PID" ] && ! kill -0 "$BRIDGE_PID" 2>/dev/null; then
            wait "$BRIDGE_PID" 2>/dev/null || true
            log "Telegram bridge exited — restarting..."
            start_bridge
        fi
    done
}

# -------------------------------------------------------------------------
# Main
# -------------------------------------------------------------------------
main() {
    local cmd="${1:-start}"

    log "==============================="
    log "  Kōan Docker — Mounted Mode"
    log "==============================="

    # Always run checks
    verify_binaries
    setup_instance
    setup_python

    case "$cmd" in
        start)
            verify_claude_auth
            verify_gh_auth
            verify_projects
            log ""
            log "Starting Kōan (agent + bridge)..."
            start_agent
            start_bridge
            monitor_children
            ;;
        agent)
            verify_claude_auth
            verify_projects
            log ""
            log "Starting agent loop only..."
            start_agent
            wait "$AGENT_PID"
            ;;
        bridge)
            verify_claude_auth
            verify_gh_auth
            log ""
            log "Starting Telegram bridge only..."
            start_bridge
            wait "$BRIDGE_PID"
            ;;
        test)
            log "Running test suite..."
            cd "$KOAN_ROOT/koan" && \
                KOAN_ROOT="$KOAN_ROOT" PYTHONPATH="$KOAN_ROOT/koan" \
                "$PYTHON" -m pytest tests/ -v
            ;;
        shell)
            log "Dropping into shell..."
            exec /bin/bash
            ;;
        *)
            log "Unknown command: $cmd"
            log "Usage: docker run koan [start|agent|bridge|test|shell]"
            exit 1
            ;;
    esac
}

main "$@"
