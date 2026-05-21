# Multi-stage Dockerfile for Band Python SDK examples
# Supports LangGraph adapter examples
#
# Usage:
#   docker build -t band-sdk .
#   docker compose up langgraph-01-simple

FROM python:3.11-slim AS base

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install uv package manager
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

# Copy project files
COPY pyproject.toml ./
COPY src/ ./src/
COPY examples/ ./examples/

# Create stage for LangGraph adapter
FROM base AS langgraph

# Install dependencies with langgraph extras (fresh resolution from pyproject.toml)
RUN uv sync --extra langgraph

# Set default command
CMD ["uv", "run", "--extra", "langgraph", "python", "examples/langgraph/01_simple_agent.py"]

# Default stage is langgraph
FROM langgraph AS default
