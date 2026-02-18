#!/bin/bash
set -euo pipefail

# =========================================================================
# Kōan Docker Entrypoint
# =========================================================================
# Claude CLI is installed in the image via npm.
# Auth: ANTHROPIC_API_KEY (API billing) or interactive login (subscription).
# GitHub CLI auth (~/.config/gh) is mounted from the host.
#
# Commands:
#   start    — Run both agent loop and Telegram bridge (default)
#   agent    — Run agent loop only
#   bridge   — Run Telegram bridge only
#   auth     — Authenticate Claude CLI interactively (browser login)
#   test     — Run the test suite
#   shell    — Drop into bash shell
# =========================================================================

KOAN_ROOT="${KOAN_ROOT:-/app}"
PYTHON="${KOAN_ROOT}/.venv/bin/python3"
INSTANCE="${KOAN_ROOT}/instance"
STOPPING=false

# Fall back to system Python if venv doesn't exist (Docker image uses system pip)
if [ ! -x "$PYTHON" ]; then
    PYTHON="python3"
fi

# --- ANSI Colors (disabled when stdout is not a TTY) ---
if [ -n "${KOAN_FORCE_COLOR:-}" ] || [ -t 1 ]; then
    BOLD='\033[1m' DIM='\033[2m'
    RED='\033[31m' GREEN='\033[32m' YELLOW='\033[33m' CYAN='\033[36m'
    RESET='\033[0m'
else
    BOLD='' DIM='' RED='' GREEN='' YELLOW='' CYAN='' RESET=''
fi

log()     { printf "${DIM}[koan-docker] $(date +%H:%M:%S)${RESET} %s\n" "$*"; }
warn()    { printf "${YELLOW}[koan-docker] $(date +%H:%M:%S) ⚠ %s${RESET}\n" "$*" >&2; }
error()   { printf "${RED}[koan-docker] $(date +%H:%M:%S) ✗ %s${RESET}\n" "$*" >&2; }
success() { printf "${GREEN}[koan-docker] $(date +%H:%M:%S) ✓ %s${RESET}\n" "$*"; }
section() { printf "\n${BOLD}${CYAN}--- %s ---${RESET}\n" "$*"; }

# -------------------------------------------------------------------------
# 1. Verify Mounted Binaries
# -------------------------------------------------------------------------
verify_binaries() {
    local missing=()
    local provider="${KOAN_CLI_PROVIDER:-claude}"

    # gh and git are installed in the image — just log versions
    success "gh $(gh --version 2>/dev/null | head -1 || echo '(unknown version)')"
    success "git $(git --version 2>/dev/null || echo '(unknown version)')"
    success "node $(node --version 2>/dev/null || echo '(unknown version)')"

    # Provider-specific CLI
    case "$provider" in
        claude)
            if ! command -v claude &>/dev/null; then
                missing+=("claude (Claude Code CLI) — npm install may have failed")
            else
                success "claude $(claude --version 2>/dev/null | head -1 || echo '(unknown version)')"
            fi
            ;;
        copilot)
            if ! command -v github-copilot-cli &>/dev/null && ! command -v copilot &>/dev/null; then
                missing+=("github-copilot-cli or copilot (GitHub Copilot CLI)")
            else
                success "copilot CLI"
            fi
            ;;
        local|ollama)
            if ! command -v ollama &>/dev/null; then
                missing+=("ollama")
            else
                success "ollama $(ollama --version 2>/dev/null | head -1 || echo '(unknown version)')"
            fi
            ;;
    esac

    if [ ${#missing[@]} -gt 0 ]; then
        error "Missing binaries:"
        for bin in "${missing[@]}"; do
            printf "  ${RED}✗${RESET} %s\n" "$bin"
        done
        printf "\n"
        log "Run ./setup-docker.sh on the host to generate volume mounts."
        return 1
    fi

    success "All required binaries available (provider: $provider)"
    return 0
}

# -------------------------------------------------------------------------
# 2. Verify Auth State
# -------------------------------------------------------------------------

# Check Claude authentication (API key or interactive login).
# Returns 0 if authenticated, 1 if not.
check_claude_auth() {
    # Option 1: API key (works with API billing accounts)
    if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
        success "Claude auth: API key"
        return 0
    fi

    # Option 2: Interactive login (works with Claude subscriptions)
    # The auth state lives in ~/.claude/ (mounted from host's claude-auth/)
    if timeout 10 claude -p "ok" --max-turns 1 >/dev/null 2>&1; then
        success "Claude auth: interactive login"
        return 0
    fi

    # Neither method works
    error "Claude CLI is not authenticated"
    log "  Option 1: Set ANTHROPIC_API_KEY in .env (API billing)"
    log "  Option 2: Run 'docker compose run --rm -it koan auth' (subscription login)"
    return 1
}

verify_auth() {
    local provider="${KOAN_CLI_PROVIDER:-claude}"
    local warnings=()

    # Check gh auth
    if [ ! -d "${HOME}/.config/gh" ]; then
        warnings+=("No ~/.config/gh/ mounted — gh CLI may not be authenticated")
    fi

    # Check Copilot auth
    if [ "$provider" = "copilot" ] && [ ! -d "${HOME}/.copilot" ]; then
        warnings+=("No ~/.copilot/ mounted — Copilot CLI may not be authenticated")
    fi

    for w in "${warnings[@]}"; do
        warn "$w"
    done

    return 0
}

# -------------------------------------------------------------------------
# 3. Instance Directory
# -------------------------------------------------------------------------
setup_instance() {
    if [ ! -f "$INSTANCE/missions.md" ]; then
        log "Initializing instance/ from template"
        cp -r "$KOAN_ROOT/instance.example/"* "$INSTANCE/" 2>/dev/null || true
    fi

    # Ensure required subdirectories exist
    mkdir -p "$INSTANCE/journal" "$INSTANCE/memory" "$INSTANCE/memory/global" \
             "$INSTANCE/memory/projects"
}

# -------------------------------------------------------------------------
# 4. Workspace Setup
# -------------------------------------------------------------------------
setup_workspace() {
    local workspace="$KOAN_ROOT/workspace"

    if [ ! -d "$workspace" ]; then
        mkdir -p "$workspace"
    fi

    # Count projects
    local count
    count=$(find "$workspace" -maxdepth 1 -mindepth 1 -type d -o -type l 2>/dev/null | wc -l | tr -d ' ')
    log "Workspace: $count project(s) mounted"

    # Check for projects.yaml (mounted from host's projects.docker.yaml)
    if [ ! -f "$KOAN_ROOT/projects.yaml" ]; then
        if [ "$count" -gt 0 ]; then
            log "No projects.yaml — $count workspace project(s) will be auto-discovered"
        else
            warn "No projects.yaml and no workspace projects"
            log "  Run setup-docker.sh or mount projects in workspace/"
        fi
    fi
}

# -------------------------------------------------------------------------
# 5. Process Supervision
# -------------------------------------------------------------------------
start_bridge() {
    log "Starting Telegram bridge (awake.py)"
    cd "$KOAN_ROOT/koan" && \
        $PYTHON app/awake.py &
    BRIDGE_PID=$!
    log "Bridge PID: $BRIDGE_PID"
}

start_agent() {
    log "Starting agent loop (run.py)"
    cd "$KOAN_ROOT/koan" && \
        $PYTHON app/run.py &
    AGENT_PID=$!
    log "Agent PID: $AGENT_PID"
}

cleanup() {
    if [ "$STOPPING" = true ]; then
        return
    fi
    STOPPING=true
    log "Shutting down..."

    # Create stop signal for graceful shutdown
    touch "$KOAN_ROOT/.koan-stop"

    # Graceful stop
    [ -n "${BRIDGE_PID:-}" ] && kill "$BRIDGE_PID" 2>/dev/null
    [ -n "${AGENT_PID:-}" ]  && kill "$AGENT_PID" 2>/dev/null

    # Wait up to 10s for graceful exit
    local timeout=10
    while [ $timeout -gt 0 ]; do
        local alive=false
        [ -n "${BRIDGE_PID:-}" ] && kill -0 "$BRIDGE_PID" 2>/dev/null && alive=true
        [ -n "${AGENT_PID:-}" ]  && kill -0 "$AGENT_PID" 2>/dev/null  && alive=true
        [ "$alive" = false ] && break
        sleep 1
        timeout=$((timeout - 1))
    done

    # Force kill if still alive
    [ -n "${BRIDGE_PID:-}" ] && kill -9 "$BRIDGE_PID" 2>/dev/null || true
    [ -n "${AGENT_PID:-}" ]  && kill -9 "$AGENT_PID" 2>/dev/null  || true

    # Clean up signal files
    rm -f "$KOAN_ROOT/.koan-stop"

    success "Shutdown complete"
    exit 0
}

monitor_processes() {
    while true; do
        if [ -n "${AGENT_PID:-}" ] && ! kill -0 "$AGENT_PID" 2>/dev/null; then
            wait "$AGENT_PID" 2>/dev/null || true
            warn "Agent loop exited — restarting in 5s"
            sleep 5
            # Only restart if we're not shutting down
            if [ "$STOPPING" = false ]; then
                start_agent
            fi
        fi

        if [ -n "${BRIDGE_PID:-}" ] && ! kill -0 "$BRIDGE_PID" 2>/dev/null; then
            wait "$BRIDGE_PID" 2>/dev/null || true
            warn "Bridge exited — restarting in 2s"
            sleep 2
            if [ "$STOPPING" = false ]; then
                start_bridge
            fi
        fi

        # Write heartbeat for HEALTHCHECK
        date +%s > "$KOAN_ROOT/.koan-heartbeat"

        sleep 5
    done
}

# =========================================================================
# Main
# =========================================================================
COMMAND="${1:-start}"

case "$COMMAND" in
    start)
        printf "${BOLD}${CYAN}Kōan Docker — initializing${RESET}\n"
        verify_binaries || exit 1
        check_claude_auth || exit 1
        verify_auth
        setup_instance
        setup_workspace

        trap cleanup INT TERM

        # Touch heartbeat so HEALTHCHECK doesn't fail during boot
        date +%s > "$KOAN_ROOT/.koan-heartbeat"

        start_bridge
        sleep 2  # Let bridge initialize before agent
        start_agent

        log "Both processes running — monitoring"
        monitor_processes
        ;;

    agent)
        log "Kōan Docker — agent only"
        verify_binaries || exit 1
        check_claude_auth || exit 1
        verify_auth
        setup_instance
        setup_workspace

        trap cleanup INT TERM
        cd "$KOAN_ROOT/koan" && exec $PYTHON app/run.py
        ;;

    bridge)
        log "Kōan Docker — bridge only"
        setup_instance

        trap cleanup INT TERM
        cd "$KOAN_ROOT/koan" && exec $PYTHON app/awake.py
        ;;

    test)
        log "Running test suite"
        setup_instance
        cd "$KOAN_ROOT/koan" && \
            exec $PYTHON -m pytest tests/ -v
        ;;

    auth)
        section "Claude CLI Authentication"
        if check_claude_auth 2>/dev/null; then
            success "Already authenticated — no action needed"
            exit 0
        fi
        log "Starting interactive Claude CLI login..."
        log "A browser URL will appear — open it to complete authentication."
        log "Auth state will persist in the mounted claude-auth/ volume."
        exec claude
        ;;

    shell)
        exec /bin/bash
        ;;

    *)
        echo "Usage: docker run koan [start|agent|bridge|auth|test|shell]"
        exit 1
        ;;
esac
