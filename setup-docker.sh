#!/bin/bash
set -euo pipefail

# =========================================================================
# Kōan Docker Setup — Auto-detect host binaries and generate mount config
# =========================================================================
# This script:
#   1. Detects claude and gh CLI binary paths on the host
#   2. Detects Claude CLI runtime dependencies (node/bun)
#   3. Reads projects.yaml for project repo paths
#   4. Generates docker-compose.override.yml with all necessary volume mounts
#
# Usage: ./setup-docker.sh
# =========================================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OVERRIDE_FILE="$SCRIPT_DIR/docker-compose.override.yml"

log() { echo "→ $*"; }
warn() { echo "⚠ $*"; }
err() { echo "✗ $*" >&2; }

# -------------------------------------------------------------------------
# Detect binary paths
# -------------------------------------------------------------------------
detect_binary() {
    local name="$1"
    local path
    path=$(which "$name" 2>/dev/null) || true

    if [ -z "$path" ]; then
        warn "$name not found in PATH — skipping"
        return 1
    fi

    # Resolve symlinks to get the real path
    local real_path
    if [ "$(uname)" = "Darwin" ]; then
        # macOS: use -f if available (coreutils), otherwise fallback
        real_path=$(readlink "$path" 2>/dev/null || echo "$path")
        # Handle relative symlinks
        if [[ "$real_path" != /* ]]; then
            real_path="$(dirname "$path")/$real_path"
        fi
    else
        real_path=$(readlink -f "$path" 2>/dev/null || echo "$path")
    fi

    log "Found $name: $path → $real_path"
    echo "$real_path"
}

# -------------------------------------------------------------------------
# Detect Claude CLI runtime dependencies
# -------------------------------------------------------------------------
detect_claude_runtime() {
    local claude_path="$1"
    local mounts=()

    # Claude Code is a Node.js/Bun application installed via npm.
    # We need to mount the npm package directory too.

    # Check if it's an npm global install
    local npm_root
    npm_root=$(npm root -g 2>/dev/null) || true

    if [ -n "$npm_root" ] && [ -d "$npm_root/@anthropic-ai" ]; then
        log "Claude npm packages: $npm_root/@anthropic-ai/"
        mounts+=("$npm_root/@anthropic-ai:$npm_root/@anthropic-ai:ro")
    fi

    # Check if node/bun runtime is available
    local node_path
    node_path=$(which node 2>/dev/null) || true
    if [ -n "$node_path" ]; then
        log "Node.js runtime: $node_path"
        mounts+=("$node_path:/usr/local/bin/node:ro")
    fi

    local bun_path
    bun_path=$(which bun 2>/dev/null) || true
    if [ -n "$bun_path" ]; then
        log "Bun runtime: $bun_path"
        mounts+=("$bun_path:/usr/local/bin/bun:ro")
    fi

    # Return mounts (newline-separated)
    printf '%s\n' "${mounts[@]}"
}

# -------------------------------------------------------------------------
# Read project paths from projects.yaml
# -------------------------------------------------------------------------
read_projects() {
    local yaml="$SCRIPT_DIR/projects.yaml"
    if [ ! -f "$yaml" ]; then
        warn "projects.yaml not found — no project mounts"
        return
    fi

    # Simple YAML path extraction (no PyYAML needed)
    grep "path:" "$yaml" | while IFS= read -r line; do
        local path
        path=$(echo "$line" | sed 's/.*path: *//' | tr -d '"' | tr -d "'")
        if [ -d "$path" ]; then
            log "Project: $path"
            echo "$path"
        else
            warn "Project path not found: $path"
        fi
    done
}

# -------------------------------------------------------------------------
# Generate docker-compose.override.yml
# -------------------------------------------------------------------------
generate_override() {
    local claude_path gh_path
    local extra_mounts=()

    log ""
    log "Detecting host binaries..."
    log ""

    # Claude CLI
    claude_path=$(detect_binary "claude") || {
        err "claude CLI is required. Install it first: npm install -g @anthropic-ai/claude-code"
        exit 1
    }

    # Claude runtime deps
    local claude_runtime
    claude_runtime=$(detect_claude_runtime "$claude_path")
    if [ -n "$claude_runtime" ]; then
        while IFS= read -r mount; do
            [ -n "$mount" ] && extra_mounts+=("$mount")
        done <<< "$claude_runtime"
    fi

    # GitHub CLI
    gh_path=$(detect_binary "gh") || {
        warn "gh CLI not found — GitHub operations will not work in container"
        gh_path=""
    }

    # SSH keys (optional)
    if [ -d "$HOME/.ssh" ]; then
        log "SSH keys: ~/.ssh/ (will mount read-only)"
        extra_mounts+=("$HOME/.ssh:/home/koan/.ssh:ro")
    fi

    # Project paths
    log ""
    log "Reading project paths..."
    local project_paths=()
    while IFS= read -r path; do
        [ -n "$path" ] && project_paths+=("$path")
    done < <(read_projects)

    # UID/GID detection
    local uid gid
    uid=$(id -u)
    gid=$(id -g)

    # Generate the override file
    log ""
    log "Generating $OVERRIDE_FILE..."

    cat > "$OVERRIDE_FILE" <<EOF
# Auto-generated by setup-docker.sh — $(date -Iseconds)
# Re-run ./setup-docker.sh to regenerate after changing projects.yaml

services:
  koan:
    build:
      args:
        HOST_UID: "$uid"
        HOST_GID: "$gid"
    volumes:
      # --- CLI binaries (from host, read-only) ---
      - ${claude_path}:/usr/local/bin/claude:ro
EOF

    if [ -n "$gh_path" ]; then
        echo "      - ${gh_path}:/usr/local/bin/gh:ro" >> "$OVERRIDE_FILE"
    fi

    # Extra mounts (node runtime, npm packages, SSH)
    for mount in "${extra_mounts[@]}"; do
        echo "      - ${mount}" >> "$OVERRIDE_FILE"
    done

    # Project repo mounts
    if [ ${#project_paths[@]} -gt 0 ]; then
        echo "" >> "$OVERRIDE_FILE"
        echo "      # --- Project repos (read-write, same path as host) ---" >> "$OVERRIDE_FILE"
        for path in "${project_paths[@]}"; do
            echo "      - ${path}:${path}" >> "$OVERRIDE_FILE"
        done
    fi

    echo "" >> "$OVERRIDE_FILE"

    log ""
    log "Done! Generated: $OVERRIDE_FILE"
    log ""
    log "Next steps:"
    log "  1. Copy env.docker.example → .env.docker (fill in Telegram token)"
    log "  2. docker compose up --build"
    log ""
    log "To test without starting: docker compose run koan test"
}

# -------------------------------------------------------------------------
# Main
# -------------------------------------------------------------------------
main() {
    echo ""
    echo "  Kōan Docker Setup"
    echo "  ================="
    echo ""

    generate_override
}

main
