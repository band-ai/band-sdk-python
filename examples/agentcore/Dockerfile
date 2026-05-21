ARG TARGETPLATFORM=linux/arm64
FROM --platform=$TARGETPLATFORM python:3.11-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# Convert SSH git URLs to HTTPS so submodules clone without SSH keys
RUN git config --global url."https://github.com/".insteadOf "git@github.com:" \
    && git config --global safe.directory '*'

COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY src/ src/
RUN uv sync --frozen --extra agentcore_runtime --extra anthropic --no-dev

# --- Final stage ---
FROM --platform=$TARGETPLATFORM python:3.11-slim

COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY examples/agentcore/agentcore_llm_server.py examples/agentcore/agentcore_llm_server.py

RUN useradd --no-log-init --create-home --shell /bin/bash appuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/ping')" || exit 1

USER appuser

CMD ["python", "examples/agentcore/agentcore_llm_server.py"]
