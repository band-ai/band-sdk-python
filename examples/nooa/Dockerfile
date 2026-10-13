# The NOOA examples in a container, so the Python the model writes runs here
# rather than on the host. Builds the SDK from this checkout. From the repo root:
#
#   docker build -f examples/nooa/Dockerfile -t band-nooa-examples .
#
# See examples/nooa/README.md for the run commands.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app

# A frozen sync needs the lock and every workspace member it names. Installing
# non-editable puts the SDK inside the venv, so nothing rebuilds at run time.
COPY pyproject.toml uv.lock README.md ./
COPY packages/ ./packages/
COPY src/ ./src/
RUN uv sync --frozen --no-dev --no-editable --extra nooa

COPY examples/prompts/ ./examples/prompts/
COPY examples/nooa/ ./examples/nooa/

# The model's code runs as this unprivileged user, with no write access to /app.
RUN useradd --create-home agent
USER agent

ENTRYPOINT ["/opt/venv/bin/python"]
CMD ["examples/nooa/01_basic_agent.py"]
