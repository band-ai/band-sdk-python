FROM ghcr.io/astral-sh/uv:0.12.23-debian-slim@sha256:cb8750e12902fdaca60c1e48a796d19c02e6c2037ca801f322689fa97f0a44f2 AS build
ENV UV_PYTHON_INSTALL_DIR=/opt/band-claude/python
ENV UV_PYTHON_PREFERENCE=only-managed
WORKDIR /opt/band-claude
COPY pyproject.toml uv.lock ./
RUN uv python install 3.12.12 \
    && uv sync --locked --no-dev --no-install-project --python 3.12.12
COPY bridge.py ./
COPY band-claude /out/usr/local/bin/band-claude
RUN chmod 0755 /out/usr/local/bin/band-claude

FROM scratch
COPY --from=build /opt/band-claude /opt/band-claude
COPY --from=build /out/ /
