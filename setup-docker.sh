#!/bin/bash
set -euo pipefail

# =========================================================================
# Kōan Docker Setup — Auto-detect host binaries and generate mounts
# =========================================================================
# This script inspects the host system to find CLI binaries (claude, gh,
# copilot, ollama) and their dependencies (Node.js runtime for Claude),
# then generates a docker-compose.override.yml with the correct volume
# mounts.
#
# Usage:
#   ./setup-docker.sh           # Auto-detect and generate
#   ./setup-docker.sh --dry-run # Show what would be mounted without writing
# =========================================================================

DRY_RUN=false
if [ "${1:-}" = "--dry-run" ]; then
    DRY_RUN=true
fi

OVERRIDE_FILE="docker-compose.override.yml"

log() {
    echo "[setup-docker] $*"
}

warn() {
    echo "[setup-docker] WARNING: $*" >&2
}

error() {
    echo "[setup-docker] ERROR: $*" >&2
}

# -------------------------------------------------------------------------
# Detect binaries and their real paths (resolve symlinks)
# -------------------------------------------------------------------------
declare -a VOLUME_MOUNTS=()
declare -a FOUND_BINS=()

detect_binary() {
    local name="$1"
    local target="/host-bin/$name"

    local path
    path=$(command -v "$name" 2>/dev/null || true)
    if [ -z "$path" ]; then
        return 1
    fi

    # Resolve symlinks to get the real binary
    local real_path
    real_path=$(realpath "$path" 2>/dev/null || readlink -f "$path" 2>/dev/null || echo "$path")

    log "Found $name: $real_path"
    VOLUME_MOUNTS+=("      - ${real_path}:${target}:ro")
    FOUND_BINS+=("$name")
    return 0
}

# Detect a directory if it exists
detect_dir() {
    local host_dir="$1"
    local container_dir="$2"
    local mode="${3:-ro}"
    local label="${4:-$host_dir}"

    # Expand ~ to $HOME
    local expanded="${host_dir/#\~/$HOME}"

    if [ -d "$expanded" ]; then
        log "Found $label: $expanded"
        VOLUME_MOUNTS+=("      - ${expanded}:${container_dir}:${mode}")
        return 0
    fi
    return 1
}

# -------------------------------------------------------------------------
# Detect Node.js runtime (needed for Claude CLI)
# -------------------------------------------------------------------------
detect_node_runtime() {
    local node_path
    node_path=$(command -v node 2>/dev/null || true)
    if [ -z "$node_path" ]; then
        warn "Node.js not found — Claude CLI may not work"
        return 1
    fi

    local real_node
    real_node=$(realpath "$node_path" 2>/dev/null || echo "$node_path")
    log "Found node: $real_node"
    VOLUME_MOUNTS+=("      - ${real_node}:/host-bin/node:ro")

    # Find npm global packages (where @anthropic-ai/claude-code lives)
    local npm_prefix
    npm_prefix=$(npm prefix -g 2>/dev/null || true)
    if [ -n "$npm_prefix" ] && [ -d "$npm_prefix/lib/node_modules" ]; then
        log "Found npm global modules: $npm_prefix/lib/node_modules"
        VOLUME_MOUNTS+=("      - ${npm_prefix}/lib/node_modules:/host-node/lib/node_modules:ro")

        # Also mount the npm bin directory for the claude wrapper script
        if [ -d "$npm_prefix/bin" ]; then
            VOLUME_MOUNTS+=("      - ${npm_prefix}/bin:/host-node/bin:ro")
        fi
    fi

    return 0
}

# -------------------------------------------------------------------------
# Detect host UID/GID
# -------------------------------------------------------------------------
detect_uid_gid() {
    local uid gid
    uid=$(id -u)
    gid=$(id -g)
    log "Host user: UID=$uid GID=$gid"
    echo "HOST_UID=$uid" > .env.docker
    echo "HOST_GID=$gid" >> .env.docker
}

# -------------------------------------------------------------------------
# Resolve workspace symlinks for Docker bind mounts
# -------------------------------------------------------------------------
resolve_workspace() {
    if [ ! -d "workspace" ]; then
        log "No workspace/ directory — skipping project mount resolution"
        return
    fi

    local count=0
    for entry in workspace/*/; do
        [ -d "$entry" ] || continue
        local name
        name=$(basename "$entry")

        if [ -L "workspace/$name" ]; then
            local target
            target=$(realpath "workspace/$name")
            log "Workspace symlink: $name → $target"
            # Symlinks need explicit bind mounts since Docker doesn't follow them
            VOLUME_MOUNTS+=("      - ${target}:/app/workspace/${name}")
            count=$((count + 1))
        fi
    done

    if [ $count -gt 0 ]; then
        log "Resolved $count workspace symlink(s)"
    fi
}

# -------------------------------------------------------------------------
# Generate docker-compose.override.yml
# -------------------------------------------------------------------------
generate_override() {
    if [ ${#VOLUME_MOUNTS[@]} -eq 0 ]; then
        warn "No volume mounts detected — override file will be minimal"
    fi

    local content
    content="# Auto-generated by setup-docker.sh — $(date -Iseconds)
# Re-run ./setup-docker.sh if you change your CLI tools or workspace layout.
#
# Binary mounts: ${#FOUND_BINS[@]} CLI tool(s) detected
# This file is gitignored.

services:
  koan:
    build:
      args:
        HOST_UID: \${HOST_UID:-$(id -u)}
        HOST_GID: \${HOST_GID:-$(id -g)}
    volumes:"

    for mount in "${VOLUME_MOUNTS[@]}"; do
        content="$content
$mount"
    done

    if [ "$DRY_RUN" = true ]; then
        echo ""
        echo "=== Would write to $OVERRIDE_FILE ==="
        echo "$content"
        echo "=== End ==="
    else
        echo "$content" > "$OVERRIDE_FILE"
        log "Written: $OVERRIDE_FILE"
    fi
}

# =========================================================================
# Main
# =========================================================================

log "Detecting host environment..."
echo ""

# 1. CLI binaries
log "--- CLI Binaries ---"
detect_binary "gh" || warn "gh not found — GitHub operations will fail"

# Claude CLI (the main script is usually a node wrapper)
if detect_binary "claude"; then
    detect_node_runtime || true
fi

# GitHub Copilot CLI
detect_binary "github-copilot-cli" || detect_binary "copilot" || true

# Ollama
detect_binary "ollama" || true

# Git (usually available in the image, but mount host version for consistency)
detect_binary "git" || true

echo ""

# 2. Auth directories
log "--- Auth Directories ---"
detect_dir "~/.claude" "/home/koan/.claude" "rw" "Claude auth" || \
    log "~/.claude not found (ok if using ANTHROPIC_API_KEY)"

detect_dir "~/.copilot" "/home/koan/.copilot" "rw" "Copilot auth" || \
    log "~/.copilot not found (ok if not using Copilot)"

detect_dir "~/.config/gh" "/home/koan/.config/gh" "ro" "GitHub CLI auth" || \
    warn "~/.config/gh not found — gh CLI won't be authenticated"

detect_dir "~/.gitconfig" "/home/koan/.gitconfig" "ro" "Git config" || true

echo ""

# 3. Workspace symlink resolution
log "--- Workspace ---"
resolve_workspace

echo ""

# 4. Generate files
log "--- Generating ---"
detect_uid_gid
generate_override

echo ""
log "Done! Next steps:"
log "  1. Review $OVERRIDE_FILE"
log "  2. Ensure .env has your messaging credentials (KOAN_TELEGRAM_TOKEN, etc.)"
log "  3. docker compose up --build"
