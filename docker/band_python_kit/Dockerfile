# Fully-specific tag + manifest-list digest (multi-arch: resolves to the
# correct linux/amd64 or linux/arm64 image automatically). The tag makes the
# pin human-legible; the digest is what actually gets resolved — re-pin by
# passing --build-arg to bump either, but always keep both in sync (the tag
# is documentation, not an independent fallback).
ARG PYTHON_BASE_IMAGE=python:3.12.13-slim-trixie@sha256:229a2c5bfa27522db7815ea81f9bed70af17ccb9de9fc7ad142b1877b5830d36
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.9.13@sha256:f07d1bf7b1fb4b983eed2b31320e25a2a76625bdf83d5ff0208fe105d4d8d2f5
# Shared by both stages so the venv is built at the exact absolute path it
# will live at in the runtime image — see UV_PROJECT_ENVIRONMENT below.
ARG BAND_SDK_HOME=/opt/band

# Named stage so the pinned uv image can be referenced by a fixed alias below
# — COPY --from doesn't support ARG expansion directly.
FROM ${UV_IMAGE} AS uv

# ── Build stage ───────────────────────────────────────────────────────────
FROM ${PYTHON_BASE_IMAGE} AS builder
ARG BAND_SDK_HOME

# Build-only: ARG (not ENV) so it doesn't persist in any image env.
ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

# uv, copied as a pinned binary (no pip install).
COPY --from=uv /uv /uvx /usr/local/bin/

WORKDIR /build
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
# The lockfile's [tool.uv.workspace] includes packages/* (today, only
# band-mcp); `uv sync --locked` validates every member's pyproject.toml
# against the lock even though this image only installs the root band-sdk
# project, so --locked fails as stale without it. Verified: only the
# manifest is read for that check, not the member's src/ or README — a
# second workspace member under packages/ needs its own line here too.
COPY packages/band-mcp/pyproject.toml ./packages/band-mcp/pyproject.toml

# Prefer HTTPS for GitHub URLs to avoid SSH key requirements in container builds.
RUN git config --global --add url."https://github.com/".insteadOf "git@github.com:" \
    && git config --global --add url."https://github.com/".insteadOf "ssh://git@github.com/"

# Defensive guard — a no-op against the committed pyproject.toml, which has no
# [tool.uv.sources] section. Strips any such section a developer's local
# override might introduce so it can't leak a filesystem/git source path into
# the image build; `uv sync --locked` below then resolves from the registry
# (and fails loud on a stale lock rather than pulling from a local source).
RUN sed -i '/^\[tool\.uv\.sources\]/,/^\[/{ /^\[tool\.uv\.sources\]/d; /^\[/!d; }' pyproject.toml

# Core-only by default: this venv is baked once and never touched again.
# Optionally bake exactly one framework extra in at build time (e.g.
# `--build-arg SDK_EXTRA=langgraph`) — deliberately one, not a fixed v1 set,
# since some extras (e.g. crewai vs. parlant/pydantic_ai) conflict and can't
# resolve into the same venv. Any other framework is the customer's own
# venv, created at sandbox runtime, isolated from this one.
ARG SDK_EXTRA=""
# Supply-chain quarantine note: the release pipeline enforces a minimum public
# age on every locked package BEFORE this build runs (scripts/check-lock-age.py
# in kit-publish.yml, reading the upload-times recorded in uv.lock). It is
# deliberately not an in-build `--exclude-newer` flag: a cutoff makes uv treat
# the committed lock itself as outdated, failing `--locked` on every build
# (astral-sh/uv#18775; reproduced on uv 0.9.13 and 0.11.19).
# Build the venv directly at its final runtime path (not e.g. ./.venv, then
# relocated by COPY below): console-script shebangs bake in an absolute
# interpreter path at install time, so copying an already-built venv to a
# different path leaves every entry point (band-acp, band-trigger, ...)
# pointing at the deleted builder path.
ENV UV_PROJECT_ENVIRONMENT=${BAND_SDK_HOME}/venv
RUN if [ -n "$SDK_EXTRA" ]; then \
      uv sync --locked --no-dev --no-editable --extra "$SDK_EXTRA"; \
    else \
      uv sync --locked --no-dev --no-editable; \
    fi

# ── Runtime stage ─────────────────────────────────────────────────────────
FROM ${PYTHON_BASE_IMAGE} AS runtime
ARG BAND_SDK_HOME

# Build-only: ARG (not ENV) so it doesn't leak into the final runtime image env.
ARG DEBIAN_FRONTEND=noninteractive

# ca-certificates provides the system trust store that the sandbox's
# session-generated proxy CA is installed into at runtime. No proxy env vars
# are set here. SSL_CERT_FILE/REQUESTS_CA_BUNDLE point at that same system
# bundle (not a narrowed, kit-private CA file) so it's additive, not an
# override: httpx and requests otherwise trust only their vendored certifi
# bundle and never see certs update-ca-certificates adds at sandbox runtime.
# git + openssh-client: band.docker.repo_init (always in the baked venv, part
# of core band-sdk) shells out to git for clone/checkout/rev-parse and to
# ssh-keygen for SSH host-key verification — both are needed in the base
# runtime image because repo initialization runs directly inside it.
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    curl \
    ca-certificates \
    git \
    openssh-client \
    openssl \
    && rm -rf /var/lib/apt/lists/*

ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

# Static OCI labels. GHCR uses org.opencontainers.image.source to link a
# package to its repository (and, from there, to its build provenance). The
# Actions-pushed image links automatically via GITHUB_TOKEN, but a CLI/manual
# push and the kit OCI artifact only inherit the linkage from this label — so
# bake it in rather than relying on the pusher. Dynamic, per-build labels
# (version, revision, created) are added by docker/metadata-action at build
# time and are deliberately not hardcoded here.
LABEL org.opencontainers.image.source="https://github.com/band-ai/band-sdk-python" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.title="band-python-kit" \
      org.opencontainers.image.description="Run a locked Python agent workspace on Band inside a Docker Sandbox microVM."

# Immutable SDK venv, outside the customer's writable workspace ($HOME). Copied
# from the exact same absolute path it was built at (see UV_PROJECT_ENVIRONMENT
# in the builder stage) — console-script shebangs bake in an absolute
# interpreter path, so copying to a *different* path would leave every entry
# point (band-acp, band-trigger, ...) pointing at the deleted builder path.
ENV BAND_SDK_HOME=${BAND_SDK_HOME}
COPY --from=builder ${BAND_SDK_HOME}/venv ${BAND_SDK_HOME}/venv
# Same digest-pinned uv as the build stage, for the sandbox launcher's locked
# customer-venv sync (`uv sync --locked`) at runtime. Lives under the
# read-only SDK home and stays off PATH for the same reason as
# BAND_SDK_PYTHON: the customer's own tooling must never be shadowed.
COPY --from=uv /uv ${BAND_SDK_HOME}/bin/uv
RUN chmod -R a-w ${BAND_SDK_HOME}

# Fixed, absolute interpreter path for launching the SDK. Deliberately not
# added to PATH, so it can never shadow the customer's own venv at runtime.
ENV BAND_SDK_PYTHON=${BAND_SDK_HOME}/venv/bin/python
# Fixed, absolute path to the pinned uv binary; the launcher must use this
# and never download or upgrade uv at runtime.
ENV BAND_SDK_UV=${BAND_SDK_HOME}/bin/uv

RUN useradd -m -u 1000 -s /bin/bash agent \
    && touch /etc/sandbox-persistent.sh
ENV BASH_ENV=/etc/sandbox-persistent.sh
ENV HOME=/home/agent
WORKDIR /home/agent
USER agent
ENTRYPOINT ["/bin/bash", "-c", "exec \"$BAND_SDK_PYTHON\" -m band.docker.launcher"]
CMD []

FROM runtime AS cli
USER root
# Keep nodejs on the image — the codex and claude CLIs are node programs and
# fail at runtime without it (do NOT purge/autoremove node here).
# Pin the CLI versions so a rehearsed image matches the show run (never @latest).
ARG CODEX_VERSION=0.145.0
ARG CLAUDE_CODE_VERSION=2.1.217
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm \
    && npm i -g "@openai/codex@${CODEX_VERSION}" "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
    && rm -rf /var/lib/apt/lists/* \
    && codex --version \
    && codex app-server --help >/dev/null \
    && claude --version

# The CLIs need writable homes; the kit runs the agent as uid 1000 (`agent`).
ENV CODEX_HOME=/home/agent/.codex
RUN mkdir -p /home/agent/.codex /home/agent/.claude \
    && chown -R agent:agent /home/agent/.codex /home/agent/.claude

USER agent

# Default publication excludes the optional coding CLIs.
FROM runtime AS default
