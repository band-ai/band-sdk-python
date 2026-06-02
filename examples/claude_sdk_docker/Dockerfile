# Dockerfile for Band Claude SDK Agent Runner
#
# Build:   docker build -t band-claude-sdk .
# Run:     docker compose up
#
# This image provides a YAML-configured agent runner for the Band platform.

FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install uv package manager (pinned version for reproducibility)
# Install to /usr/local/bin so it's available to all users
RUN curl -LsSf https://astral.sh/uv/0.5.18/install.sh | sh && \
    cp /root/.local/bin/uv /usr/local/bin/uv

# Copy SDK source and install
# Note: uv.lock must exist in the repo root for reproducible builds.
# If building from a partial clone, run `uv lock` first to generate it.
COPY pyproject.toml uv.lock ./
COPY src/ ./src/

# Install Band SDK with claude_sdk extras
# Rewrite SSH URLs to HTTPS to avoid SSH auth issues in Docker
RUN git config --global url."https://github.com/".insteadOf "git@github.com:" && \
    uv sync --extra claude_sdk

# Copy runner and example tools
COPY examples/claude_sdk_docker/runner.py ./runner.py
COPY examples/claude_sdk_docker/tools/ ./tools/

# Create non-root user for security
RUN useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app

# Copy entrypoint script (runtime git safe.directory configuration)
COPY examples/claude_sdk_docker/entrypoint.sh ./entrypoint.sh
RUN chmod +x ./entrypoint.sh

USER appuser

# Config will be mounted at runtime
VOLUME /app/config

# Entrypoint configures git safe.directory at runtime for bind-mounted dirs
ENTRYPOINT ["./entrypoint.sh"]

# Run the agent runner
CMD ["uv", "run", "--extra", "claude_sdk", "python", "runner.py"]
