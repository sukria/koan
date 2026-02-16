#!/usr/bin/env bash
# Install Kōan systemd services.
# Usage: ./install-service.sh <koan_root> <python_path>
set -euo pipefail

KOAN_ROOT="${1:?Usage: $0 <koan_root> <python_path>}"
PYTHON="${2:?Usage: $0 <koan_root> <python_path>}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# --- Validations ---

if [ "$(uname -s)" != "Linux" ]; then
    echo "Error: systemd services are only supported on Linux." >&2
    exit 1
fi

if ! command -v systemctl >/dev/null 2>&1; then
    echo "Error: systemctl not found. systemd is required." >&2
    exit 1
fi

if [ "$(id -u)" -ne 0 ]; then
    echo "Error: must be run as root (use sudo)." >&2
    exit 1
fi

# Resolve to absolute paths
KOAN_ROOT="$(cd "$KOAN_ROOT" && pwd)"
PYTHON="$(cd "$(dirname "$PYTHON")" && pwd)/$(basename "$PYTHON")"

# Build a sanitized PATH: keep system-wide dirs, strip $HOME paths for security
HOMEDIR="$(eval echo ~"$(logname 2>/dev/null || echo root)")"
SAFE_PATH=""
IFS=':' read -ra _path_entries <<< "$PATH"
for _entry in "${_path_entries[@]}"; do
    case "$_entry" in
        "$HOMEDIR"|"$HOMEDIR"/*) ;;  # skip home directory paths
        *) SAFE_PATH="${SAFE_PATH:+$SAFE_PATH:}$_entry" ;;
    esac
done

if [ ! -f "$KOAN_ROOT/koan/app/run.py" ]; then
    echo "Error: $KOAN_ROOT does not look like a Kōan installation." >&2
    exit 1
fi

if [ ! -x "$PYTHON" ]; then
    echo "Error: Python binary not found or not executable: $PYTHON" >&2
    exit 1
fi

# --- Generate service files ---

mkdir -p "$KOAN_ROOT/logs"

for template in "$SCRIPT_DIR"/koan*.service.template; do
    service_name="$(basename "$template" .template)"
    echo "→ Generating $service_name"
    sed \
        -e "s|__KOAN_ROOT__|${KOAN_ROOT}|g" \
        -e "s|__PYTHON__|${PYTHON}|g" \
        -e "s|__PATH__|${SAFE_PATH}|g" \
        "$template" > "/etc/systemd/system/$service_name"
done

# --- Enable and reload ---

echo "→ Reloading systemd daemon"
systemctl daemon-reload

echo "→ Enabling koan-awake.service"
systemctl enable koan-awake.service

echo "→ Enabling koan.service"
systemctl enable koan.service

echo "✓ Kōan systemd services installed and enabled."
echo "  Use 'make start' to start, or: systemctl start koan"
