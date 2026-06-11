ARG TARGETPLATFORM=linux/arm64
FROM --platform=$TARGETPLATFORM python:3.11-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# Convert SSH git URLs to HTTPS so submodules clone without SSH keys
RUN git config --global url."https://github.com/".insteadOf "git@github.com:" \
    && git config --global safe.directory '*'

# Install uv for reproducible dependency resolution
COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /usr/local/bin/uv

WORKDIR /app

# Install dependencies first (cached layer), then install the SDK
COPY pyproject.toml uv.lock ./
COPY src/ src/
RUN uv sync --frozen --extra bridge --extra bridge_agentcore --no-dev

# --- Final stage ---
FROM --platform=$TARGETPLATFORM python:3.11-slim

# Copy virtual environment from builder
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Copy bridge application
COPY band-bridge/ band-bridge/

RUN useradd --no-log-init --create-home --shell /bin/bash appuser

WORKDIR /app/band-bridge

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

USER appuser

# Override this CMD in your docker-compose.yml or Dockerfile
# to point to your own entry point that registers handlers.
# Example: CMD ["python", "run.py"]
CMD ["python", "-m", "bridge_core"]
