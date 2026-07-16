# Fully-specific tag + manifest-list digest (multi-arch: resolves to the
# correct linux/amd64 or linux/arm64 image automatically). The tag makes the
# pin human-legible; the digest is what actually gets resolved — re-pin by
# passing --build-arg to bump either, but always keep both in sync (the tag
# is documentation, not an independent fallback).
ARG PYTHON_BASE_IMAGE=python:3.12.13-slim-trixie@sha256:c3d81d25b3154142b0b42eb1e61300024426268edeb5b5a26dd7ddf64d9daf28
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
FROM ${PYTHON_BASE_IMAGE}
ARG BAND_SDK_HOME

ENV DEBIAN_FRONTEND=noninteractive

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
    ca-certificates \
    git \
    openssh-client \
    && rm -rf /var/lib/apt/lists/*

ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

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

# Fixed system path, deliberately NOT under ${BAND_SDK_HOME}: ENTRYPOINT's
# exec-form JSON array is one of the few Dockerfile instructions Docker does
# NOT substitute ARG/ENV into, so a literal path is unavoidable here — keep
# it independent of the configurable SDK home rather than letting a
# --build-arg BAND_SDK_HOME override silently break container startup.
COPY docker/band_python_kit/entrypoint.sh /usr/local/bin/band-entrypoint.sh
RUN chmod +x /usr/local/bin/band-entrypoint.sh

RUN useradd -m -u 1000 -s /bin/bash agent
ENV HOME=/home/agent
WORKDIR /home/agent

# Container starts as root: the entrypoint decodes the sandbox's per-session
# proxy CA (only root can update the system trust store), then drops to the
# non-root "agent" user via setpriv before handing off to CMD.
ENTRYPOINT ["/usr/local/bin/band-entrypoint.sh"]
CMD ["bash"]
