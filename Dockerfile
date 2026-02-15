# Kōan Docker Image — Mounted Binaries Approach
#
# The container is a runtime sandbox. CLI binaries (claude, gh, copilot)
# and their auth state (~/.claude/, ~/.copilot/) live on the host and
# are mounted as volumes at runtime.
#
# This keeps the image thin (~150MB) and avoids auth conflicts.
#
# Build:  docker build -t koan .
# Run:    docker compose up --build
# Setup:  ./setup-docker.sh  (auto-detects host binaries, generates mounts)

FROM python:3.12-slim

# System dependencies — git, common tools, Node.js runtime for Claude CLI
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    jq \
    curl \
    bash \
    procps \
    openssh-client \
    make \
    && rm -rf /var/lib/apt/lists/*

# Configurable UID/GID — match the host user to avoid permission issues
# on bind-mounted volumes (workspace, ~/.claude, etc.)
ARG HOST_UID=1000
ARG HOST_GID=1000

# Create group — if a group with HOST_GID already exists (e.g. macOS GID 20
# maps to dialout in Debian), reuse it; otherwise create "koan" group.
# Then create the koan user with the desired UID, assigned to that GID.
RUN if getent group ${HOST_GID} >/dev/null 2>&1; then \
        echo "GID ${HOST_GID} already exists — reusing"; \
    else \
        groupadd -g ${HOST_GID} koan; \
    fi \
    && useradd -u ${HOST_UID} -g ${HOST_GID} -m -s /bin/bash koan 2>/dev/null || \
       useradd -u ${HOST_UID} -g ${HOST_GID} -M -s /bin/bash koan 2>/dev/null || true

# App directory
WORKDIR /app

# Python dependencies (cached layer — changes rarely)
COPY koan/requirements.txt /app/koan/requirements.txt
RUN pip install --no-cache-dir -r /app/koan/requirements.txt \
    && pip install --no-cache-dir pytest

# Copy application code
COPY koan/ /app/koan/
COPY instance.example/ /app/instance.example/
COPY Makefile /app/
COPY CLAUDE.md /app/
COPY docs/ /app/docs/
COPY projects.example.yaml /app/

# Entrypoint
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

# Workspace directory — host repos are mounted here
# Directories for mounted binaries and their dependencies
# (populated by bind mounts from the host at runtime)
RUN mkdir -p /app/workspace /app/instance /app/logs /host-bin /host-node \
    && chown -R ${HOST_UID}:${HOST_GID} /app /host-bin /host-node

# Switch to non-root user
USER ${HOST_UID}

# Git config for the koan user (can be overridden by mounting ~/.gitconfig)
RUN git config --global user.name "Kōan" \
    && git config --global user.email "koan@noreply.github.com" \
    && git config --global init.defaultBranch main

# Health check: verify heartbeat file is fresh (< 120s old)
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD test -f /app/.koan-heartbeat && \
        [ $(( $(date +%s) - $(cat /app/.koan-heartbeat | cut -d. -f1) )) -lt 120 ]

ENV KOAN_ROOT=/app
ENV PYTHONPATH=/app/koan
# /host-bin is where mounted CLI binaries (claude, gh, copilot) are linked
ENV PATH="/host-bin:${PATH}"

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["start"]
