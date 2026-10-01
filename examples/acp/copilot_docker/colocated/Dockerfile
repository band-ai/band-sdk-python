# Copilot + band-mcp in ONE image; the SDK runs one container per Band room.
#
# Each container speaks ACP on its stdio (`docker run -i`); Copilot reaches Band
# tools over the container's own loopback (127.0.0.1:3000). Nothing is published.
FROM node:22-slim

# band-mcp release to install; override to pin a version.
ARG BAND_MCP_SPEC="band-mcp>=2.2.2"

# ca-certificates: node:22-slim ships no system CA bundle, and Copilot's TLS layer
# uses the system store — without it, token validation fails with an opaque
# "network fetch ... builder error" (Node's own fetch bundles roots, masking it).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates python3 python3-venv \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g @github/copilot \
    # band-mcp in its own venv (Debian's system Python is externally managed).
    && python3 -m venv /opt/band-mcp \
    && /opt/band-mcp/bin/pip install --no-cache-dir "${BAND_MCP_SPEC}"

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
