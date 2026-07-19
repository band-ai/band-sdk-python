# Fully-specific tag + manifest-list digest (multi-arch: resolves to the
# correct linux/amd64 or linux/arm64 image automatically). The tag makes the
# pin human-legible; the digest is what actually gets resolved — re-pin by
# passing --build-arg to bump either, but always keep both in sync (the tag
# is documentation, not an independent fallback).
ARG PYTHON_BASE_IMAGE=python:3.12.13-slim-trixie@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de
# uv >= 0.11 is required by the quarantine gate: on 0.9.x, `uv sync --locked
# --exclude-newer <date>` discards the lockfile and re-resolves ("Ignoring
# existing lockfile due to addition of timestamp cutoff"), failing --locked on
# every build regardless of package age. 0.11.19 validates the existing lock
# against the cutoff instead (proven live in the pre-merge smoke run).
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.11.19@sha256:b46b03ddfcfbf8f547af7e9eaefdf8a39c8cebcba7c98858d3162bd28cf536f6
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
# Supply-chain quarantine (release builds only). When the release workflow
# passes an RFC 3339 cutoff, `uv sync --locked` re-resolves against it and
# fails if any locked package was published after the cutoff — a
# freshly-poisoned dependency cannot enter the published image until it has
# aged past the window (see docker/band_python_kit/RELEASING.md). This is a
# CI-only build arg, never `[tool.uv] exclude-newer` in pyproject.toml (that
# would be recorded into uv.lock and break `uv lock --check` for every dev who
# doesn't pass the same flag). Empty by default, so local developer builds
# resolve exactly as before.
ARG UV_EXCLUDE_NEWER=""
# Build the venv directly at its final runtime path (not e.g. ./.venv, then
# relocated by COPY below): console-script shebangs bake in an absolute
# interpreter path at install time, so copying an already-built venv to a
# different path leaves every entry point (band-acp, band-trigger, ...)
# pointing at the deleted builder path.
ENV UV_PROJECT_ENVIRONMENT=${BAND_SDK_HOME}/venv
RUN EXCLUDE_NEWER_ARGS=""; \
    if [ -n "$UV_EXCLUDE_NEWER" ]; then \
      EXCLUDE_NEWER_ARGS="--exclude-newer $UV_EXCLUDE_NEWER"; \
    fi; \
    if [ -n "$SDK_EXTRA" ]; then \
      uv sync --locked --no-dev --no-editable --extra "$SDK_EXTRA" $EXCLUDE_NEWER_ARGS; \
    else \
      uv sync --locked --no-dev --no-editable $EXCLUDE_NEWER_ARGS; \
    fi

# ── Runtime stage ─────────────────────────────────────────────────────────
FROM ${PYTHON_BASE_IMAGE}
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
# util-linux: provides setpriv, which entrypoint.sh uses to drop from root to
# the non-root agent user. Essential in trixie-slim (so present today), but
# install it explicitly so container startup doesn't silently break on a base
# bump that stops shipping it.
# openssl: entrypoint.sh validates the decoded proxy CA with `openssl x509`
# before trusting it, so the CLI must be present in the runtime image.
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    git \
    openssh-client \
    openssl \
    util-linux \
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
