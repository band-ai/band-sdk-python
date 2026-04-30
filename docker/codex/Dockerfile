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
RUN uv sync --extra codex

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

# Install Codex CLI directly in runtime (npm resolves platform-specific
# optional deps at install time; copying from builder breaks resolution).
RUN apt-get update && apt-get install -y --no-install-recommends npm \
    && npm i -g @openai/codex@latest \
    && apt-get purge -y npm \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# Copy entrypoint and runner. Shared repo-init utilities are imported from /app/src.
COPY docker/codex/entrypoint.sh /app/docker/codex/entrypoint.sh
COPY docker/codex/runner.py /app/docker/codex/runner.py

# Trust bind-mounted repo to avoid "dubious ownership" errors.
# Additional dirs can be configured at runtime via GIT_SAFE_DIRS env var.
RUN git config --global --add safe.directory /workspace/repo

# Verify Codex CLI is functional.
RUN codex --version \
    && codex app-server --help >/dev/null

RUN useradd -m -u 1000 -s /bin/bash codex \
    && mkdir -p /workspace/repo /workspace/notes /workspace/state /workspace/context /prompts /state /home/codex/.codex \
    && chown -R codex:codex /app /workspace /prompts /state /home/codex

USER codex
ENV HOME=/home/codex
ENV CODEX_HOME=/home/codex/.codex
ENV PATH="/home/codex/.local/bin:/app/.venv/bin:${PATH}"

WORKDIR /workspace/repo

ENTRYPOINT ["bash", "/app/docker/codex/entrypoint.sh"]
CMD ["/app/.venv/bin/python", "/app/docker/codex/runner.py"]
