#!/bin/bash
set -euo pipefail

# =========================================================================
# Kōan Docker Entrypoint
# =========================================================================
# Handles:
#   1. Claude CLI authentication (API key or setup-token)
#   2. Instance directory initialization
#   3. Git credentials for repo operations
#   4. Project repo cloning
#   5. Process supervision (run.sh + awake.py)
#
# Commands:
#   start    — Run both agent loop and Telegram bridge (default)
#   agent    — Run agent loop only
#   bridge   — Run Telegram bridge only
#   test     — Run the test suite
#   shell    — Drop into bash shell
# =========================================================================

KOAN_ROOT="${KOAN_ROOT:-/app}"
PYTHON="$KOAN_ROOT/.venv/bin/python3"
INSTANCE="$KOAN_ROOT/instance"
STOPPING=false

log() {
    echo "[koan-docker] $(date +%H:%M:%S) $*"
}

# -------------------------------------------------------------------------
# 1. Claude CLI Authentication
# -------------------------------------------------------------------------
setup_claude_auth() {
    if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
        log "Auth: using ANTHROPIC_API_KEY (pay-per-token)"
        return 0
    fi

    if [ -n "${CLAUDE_AUTH_TOKEN:-}" ]; then
        log "Auth: setting up Claude subscription token"
        mkdir -p "$HOME/.claude"
        echo "$CLAUDE_AUTH_TOKEN" | claude setup-token 2>/dev/null || {
            log "ERROR: claude setup-token failed"
            return 1
        }
        return 0
    fi

    log "WARNING: No Claude auth configured"
    log "  Set ANTHROPIC_API_KEY or CLAUDE_AUTH_TOKEN"
    return 1
}

# -------------------------------------------------------------------------
# 2. Instance Directory
# -------------------------------------------------------------------------
setup_instance() {
    if [ ! -d "$INSTANCE" ]; then
        log "Initializing instance/ from template"
        cp -r "$KOAN_ROOT/instance.example" "$INSTANCE"
    fi

    # Ensure required subdirectories exist
    mkdir -p "$INSTANCE/journal" "$INSTANCE/memory" "$INSTANCE/memory/global" \
             "$INSTANCE/memory/projects"
}

# -------------------------------------------------------------------------
# 3. Git Credentials
# -------------------------------------------------------------------------
setup_git_credentials() {
    if [ -n "${GITHUB_TOKEN:-}" ]; then
        log "Git: configuring GitHub token credential helper"
        # Read token from env at call-time (not embedded in gitconfig)
        git config --global credential.helper \
            '!f() { echo "username=x-access-token"; echo "password=$GITHUB_TOKEN"; }; f'
        return 0
    fi

    if [ -n "${KOAN_GIT_SSH_KEY:-}" ]; then
        log "Git: configuring SSH key"
        mkdir -p "$HOME/.ssh"
        echo "$KOAN_GIT_SSH_KEY" > "$HOME/.ssh/id_ed25519"
        chmod 600 "$HOME/.ssh/id_ed25519"
        ssh-keyscan github.com >> "$HOME/.ssh/known_hosts" 2>/dev/null
        git config --global core.sshCommand "ssh -i $HOME/.ssh/id_ed25519 -o StrictHostKeyChecking=accept-new"
        return 0
    fi

    log "WARNING: No git credentials configured"
    log "  Set GITHUB_TOKEN or KOAN_GIT_SSH_KEY for push/clone"
}

# -------------------------------------------------------------------------
# 4. Project Repos
# -------------------------------------------------------------------------
clone_project_repos() {
    if [ -z "${KOAN_DOCKER_REPOS:-}" ]; then
        log "No KOAN_DOCKER_REPOS configured — skipping repo clone"
        return 0
    fi

    mkdir -p "$KOAN_ROOT/repos"

    # Format: "name:git_url;name2:git_url2"
    # Also builds KOAN_PROJECTS env var (name:path;name2:path2) for run.sh
    local projects=""
    IFS=';' read -ra REPOS <<< "$KOAN_DOCKER_REPOS"
    for entry in "${REPOS[@]}"; do
        name="${entry%%:*}"
        url="${entry#*:}"
        target="$KOAN_ROOT/repos/$name"

        if [ -d "$target/.git" ]; then
            log "Repo $name: pulling latest"
            git -C "$target" pull --ff-only 2>/dev/null || \
                log "  pull failed (non-ff), skipping"
        else
            log "Repo $name: cloning $url"
            git clone "$url" "$target" || {
                log "ERROR: failed to clone $name"
                continue
            }
        fi

        # Build KOAN_PROJECTS from cloned repos
        if [ -n "$projects" ]; then
            projects="$projects;$name:$target"
        else
            projects="$name:$target"
        fi
    done

    if [ -n "$projects" ]; then
        export KOAN_PROJECTS="$projects"
        log "Projects: $KOAN_PROJECTS"
    fi
}

# -------------------------------------------------------------------------
# 5. Instance Git Sync
# -------------------------------------------------------------------------
sync_instance_state() {
    if [ ! -d "$INSTANCE/.git" ]; then
        log "Instance is not a git repo — skipping state sync"
        return 0
    fi

    log "Syncing instance state from git"
    git -C "$INSTANCE" pull --ff-only 2>/dev/null || \
        log "  instance pull failed (non-ff or no remote), using local state"
}

# -------------------------------------------------------------------------
# 6. Process Supervision
# -------------------------------------------------------------------------
start_bridge() {
    log "Starting Telegram bridge (awake.py)"
    cd "$KOAN_ROOT/koan" && \
        "$PYTHON" app/awake.py &
    BRIDGE_PID=$!
    log "Bridge PID: $BRIDGE_PID"
}

start_agent() {
    log "Starting agent loop (run.sh)"
    "$KOAN_ROOT/koan/run.sh" &
    AGENT_PID=$!
    log "Agent PID: $AGENT_PID"
}

cleanup() {
    if [ "$STOPPING" = true ]; then
        return
    fi
    STOPPING=true
    log "Shutting down..."

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
    [ -n "${BRIDGE_PID:-}" ] && kill -9 "$BRIDGE_PID" 2>/dev/null
    [ -n "${AGENT_PID:-}" ]  && kill -9 "$AGENT_PID" 2>/dev/null

    log "Shutdown complete"
    exit 0
}

monitor_processes() {
    while true; do
        if [ -n "${AGENT_PID:-}" ] && ! kill -0 "$AGENT_PID" 2>/dev/null; then
            log "Agent loop exited (quota exhausted or max runs reached)"
            # Agent can exit normally — keep bridge alive for Telegram
            unset AGENT_PID
        fi

        if [ -n "${BRIDGE_PID:-}" ] && ! kill -0 "$BRIDGE_PID" 2>/dev/null; then
            log "ERROR: Bridge crashed — restarting"
            sleep 2
            start_bridge
        fi

        # Exit if both are gone
        if [ -z "${AGENT_PID:-}" ] && [ -z "${BRIDGE_PID:-}" ]; then
            log "Both processes exited — container stopping"
            break
        fi

        sleep 5
    done
}

# =========================================================================
# Main
# =========================================================================
COMMAND="${1:-start}"

case "$COMMAND" in
    start)
        log "Kōan Docker — initializing"
        setup_claude_auth || exit 1
        setup_instance
        setup_git_credentials
        clone_project_repos
        sync_instance_state

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
        setup_claude_auth || exit 1
        setup_instance
        setup_git_credentials
        clone_project_repos
        sync_instance_state

        trap cleanup INT TERM
        exec "$KOAN_ROOT/koan/run.sh"
        ;;

    bridge)
        log "Kōan Docker — bridge only"
        setup_instance
        setup_git_credentials

        trap cleanup INT TERM
        cd "$KOAN_ROOT/koan" && exec "$PYTHON" app/awake.py
        ;;

    test)
        log "Running test suite"
        setup_instance
        cd "$KOAN_ROOT/koan" && \
            exec "$PYTHON" -m pytest tests/ -v
        ;;

    shell)
        exec /bin/bash
        ;;

    *)
        echo "Usage: docker run koan [start|agent|bridge|test|shell]"
        exit 1
        ;;
esac
