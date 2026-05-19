# ── Build stage ───────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY examples ./examples

# Prefer HTTPS for GitHub URLs to avoid SSH key requirements in container builds.
RUN git config --global --add url."https://github.com/".insteadOf "git@github.com:" \
    && git config --global --add url."https://github.com/".insteadOf "ssh://git@github.com/"

# Strip host-specific [tool.uv.sources] so uv resolves from the registry.
RUN sed -i '/^\[tool\.uv\.sources\]/,/^\[/{ /^\[tool\.uv\.sources\]/d; /^\[/!d; }' pyproject.toml

# Install Python dependencies.
RUN uv sync --extra claude_sdk

# ── Runtime stage ─────────────────────────────────────────────────────────
FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive

# Runtime-only system dependencies (no build tools).
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    curl \
    git \
    git-lfs \
    gh \
    jq \
    less \
    nodejs \
    openssh-client \
    ripgrep \
    && rm -rf /var/lib/apt/lists/*

# Runtime entrypoint installs optional wheel overrides via `uv pip install`.
RUN pip install --no-cache-dir uv

WORKDIR /app

# Copy Python venv and SDK source from builder.
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
COPY --from=builder /app/examples /app/examples

# Ensure the bundled Claude CLI binary is executable (pip preserves permissions
# inconsistently across layers).
RUN CLAUDE_BIN=/app/.venv/lib/python3.12/site-packages/claude_agent_sdk/_bundled/claude \
    && test -f "$CLAUDE_BIN" \
    && chmod +x "$CLAUDE_BIN"

# Copy entrypoint and runner. Shared repo-init utilities are imported from /app/src.
COPY docker/claude_sdk/entrypoint.sh /app/docker/claude_sdk/entrypoint.sh
COPY docker/claude_sdk/runner.py /app/docker/claude_sdk/runner.py

# Trust bind-mounted repo to avoid "dubious ownership" errors.
# Additional dirs can be configured at runtime via GIT_SAFE_DIRS env var.
RUN git config --global --add safe.directory /workspace/repo

RUN useradd -m -u 1000 -s /bin/bash appuser \
    && mkdir -p /workspace/repo /workspace/notes /workspace/state /workspace/context \
    && chown -R appuser:appuser /app /workspace

USER appuser
ENV HOME=/home/appuser
ENV PATH="/home/appuser/.local/bin:/app/.venv/bin:${PATH}"

WORKDIR /workspace/repo

ENTRYPOINT ["bash", "/app/docker/claude_sdk/entrypoint.sh"]
CMD ["/app/.venv/bin/python", "/app/docker/claude_sdk/runner.py"]
