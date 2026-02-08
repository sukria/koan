# Kōan Docker Image — Thin Runtime
#
# The container is a runtime sandbox, NOT a self-contained environment.
# CLI binaries (claude, gh) and their auth state (~/.claude/) live on
# the host and are mounted as volumes at runtime.
#
# This keeps the image small (~200MB vs ~1.2GB) and eliminates all
# auth headaches — no setup-token, no session conflicts.
#
# Build:  docker build -t koan .
# Run:    docker compose up

FROM python:3.12-slim

# Minimal system deps — no node, no claude, no gh (all mounted from host)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    bash \
    procps \
    jq \
    && rm -rf /var/lib/apt/lists/*

# Create user matching host UID (configurable at build/run time)
ARG HOST_UID=501
ARG HOST_GID=20
RUN groupadd -g ${HOST_GID} koan 2>/dev/null || true \
    && useradd -u ${HOST_UID} -g ${HOST_GID} -m -s /bin/bash koan

WORKDIR /app

# Python dependencies (cached layer)
COPY koan/requirements.txt /app/koan/requirements.txt
RUN pip install --no-cache-dir -r /app/koan/requirements.txt pytest

# Application code
COPY koan/ /app/koan/
COPY skills/ /app/skills/
COPY instance.example/ /app/instance.example/
COPY Makefile CLAUDE.md /app/

# Runtime state directories
RUN mkdir -p /app/instance && chown -R koan:koan /app

USER koan

# Git identity for koan user (overridable via mounted ~/.gitconfig)
RUN git config --global user.name "Kōan" \
    && git config --global user.email "koan@noreply.github.com" \
    && git config --global init.defaultBranch main

# Health check: verify heartbeat file is fresh (< 120s old)
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD test -f /app/.koan-heartbeat && \
        [ $(( $(date +%s) - $(cat /app/.koan-heartbeat | cut -d. -f1) )) -lt 120 ]

ENV KOAN_ROOT=/app
ENV PYTHONPATH=/app/koan

COPY docker-entrypoint.sh /app/docker-entrypoint.sh

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["start"]
