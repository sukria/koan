# Kōan Docker Image
# Runs both the agent loop (run.sh) and Telegram bridge (awake.py)
# in a single container.
#
# Auth options (set ONE):
#   ANTHROPIC_API_KEY  — pay-per-token, simplest
#   CLAUDE_AUTH_TOKEN   — uses Claude subscription quota
#
# Build:  docker build -t koan .
# Run:    docker run --env-file .env.docker koan

FROM node:22-slim

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    python3 \
    python3-pip \
    python3-venv \
    jq \
    curl \
    bash \
    procps \
    openssh-client \
    && rm -rf /var/lib/apt/lists/*

# Claude Code CLI
RUN npm install -g @anthropic-ai/claude-code

# Create non-root user
RUN groupadd -r koan && useradd -r -g koan -m -s /bin/bash koan

# App directory
WORKDIR /app

# Python dependencies (cached layer)
COPY koan/requirements.txt /app/koan/requirements.txt
RUN python3 -m venv /app/.venv \
    && /app/.venv/bin/pip install --no-cache-dir -r /app/koan/requirements.txt \
    && /app/.venv/bin/pip install --no-cache-dir pytest

# Copy application code
COPY koan/ /app/koan/
COPY instance.example/ /app/instance.example/
COPY Makefile /app/
COPY CLAUDE.md /app/
COPY docs/ /app/docs/

# Create directories for runtime state and project repos
RUN mkdir -p /app/instance /app/repos \
    && chown -R koan:koan /app

# Entrypoint
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

# Switch to non-root user
USER koan

# Git config for the koan user
RUN git config --global user.name "Kōan" \
    && git config --global user.email "koan@noreply.github.com" \
    && git config --global init.defaultBranch main

# Health check: verify heartbeat file is fresh (< 60s old)
# The heartbeat file contains a Unix timestamp written by awake.py
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD test -f /app/.koan-heartbeat && \
        [ $(( $(date +%s) - $(cat /app/.koan-heartbeat | cut -d. -f1) )) -lt 60 ]

ENV KOAN_ROOT=/app
ENV PYTHONPATH=/app/koan

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["start"]
