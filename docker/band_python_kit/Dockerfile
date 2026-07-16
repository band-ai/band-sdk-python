# Fully-specific tag + manifest-list digest (multi-arch: resolves to the
# correct linux/amd64 or linux/arm64 image automatically). The tag makes the
# pin human-legible; the digest is what actually gets resolved — re-pin by
# passing --build-arg to bump either, but always keep both in sync (the tag
# is documentation, not an independent fallback).
ARG PYTHON_BASE_IMAGE=python:3.12.13-slim-trixie@sha256:c3d81d25b3154142b0b42eb1e61300024426268edeb5b5a26dd7ddf64d9daf28
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.9.13@sha256:f07d1bf7b1fb4b983eed2b31320e25a2a76625bdf83d5ff0208fe105d4d8d2f5

# Named stage so the pinned uv image can be referenced by a fixed alias below
# — COPY --from doesn't support ARG expansion directly.
FROM ${UV_IMAGE} AS uv

# ── Build stage ───────────────────────────────────────────────────────────
FROM ${PYTHON_BASE_IMAGE} AS builder

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

# uv, copied as a pinned binary (no pip install).
COPY --from=uv /uv /uvx /usr/local/bin/

WORKDIR /build
COPY pyproject.toml uv.lock README.md ./
COPY src ./src

# Prefer HTTPS for GitHub URLs to avoid SSH key requirements in container builds.
RUN git config --global --add url."https://github.com/".insteadOf "git@github.com:" \
    && git config --global --add url."https://github.com/".insteadOf "ssh://git@github.com/"

# Strip host-specific [tool.uv.sources] overrides so uv resolves from the registry.
RUN sed -i '/^\[tool\.uv\.sources\]/,/^\[/{ /^\[tool\.uv\.sources\]/d; /^\[/!d; }' pyproject.toml

# Core-only by default: this venv is baked once and never touched again.
# Optionally bake exactly one framework extra in at build time (e.g.
# `--build-arg SDK_EXTRA=langgraph`) — deliberately one, not a fixed v1 set,
# since some extras (e.g. crewai vs. parlant/pydantic_ai) conflict and can't
# resolve into the same venv. Any other framework is the customer's own
# venv, created at sandbox runtime, isolated from this one.
ARG SDK_EXTRA=""
RUN if [ -n "$SDK_EXTRA" ]; then \
      uv sync --locked --no-dev --no-editable --extra "$SDK_EXTRA"; \
    else \
      uv sync --locked --no-dev --no-editable; \
    fi

# ── Runtime stage ─────────────────────────────────────────────────────────
FROM ${PYTHON_BASE_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive

# ca-certificates provides the system trust store that the sandbox's
# session-generated proxy CA is installed into at runtime. No proxy env vars
# are set here. SSL_CERT_FILE/REQUESTS_CA_BUNDLE point at that same system
# bundle (not a narrowed, kit-private CA file) so it's additive, not an
# override: httpx and requests otherwise trust only their vendored certifi
# bundle and never see certs update-ca-certificates adds at sandbox runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

# Immutable SDK venv, outside the customer's writable workspace ($HOME).
ENV BAND_SDK_HOME=/opt/band
COPY --from=builder /build/.venv ${BAND_SDK_HOME}/venv
RUN chmod -R a-w ${BAND_SDK_HOME}

# Fixed, absolute interpreter path for launching the SDK. Deliberately not
# added to PATH, so it can never shadow the customer's own venv at runtime.
ENV BAND_SDK_PYTHON=${BAND_SDK_HOME}/venv/bin/python

COPY docker/band_python_kit/entrypoint.sh ${BAND_SDK_HOME}/entrypoint.sh
RUN chmod +x ${BAND_SDK_HOME}/entrypoint.sh

RUN useradd -m -u 1000 -s /bin/bash agent
ENV HOME=/home/agent
WORKDIR /home/agent

# Container starts as root: the entrypoint decodes the sandbox's per-session
# proxy CA (only root can update the system trust store), then drops to the
# non-root "agent" user via setpriv before handing off to CMD.
ENTRYPOINT ["/opt/band/entrypoint.sh"]
CMD ["bash"]
