# Copilot + band-mcp in ONE container ("just run this container").
#
# Copilot reaches Band tools over the container's own loopback (127.0.0.1:3000);
# only the ACP TCP port (8080) is published. Copilot is a Node CLI and band-mcp is
# a Python package, so this image carries both runtimes.
FROM node:22-slim

# ca-certificates: node:22-slim ships no system CA bundle, and Copilot's TLS layer
# uses the system store — without it, token validation fails with an opaque
# "network fetch ... builder error" (Node's own fetch bundles roots, masking it).
RUN apt-get update \
    && apt-get install -y --no-install-recommends socat ca-certificates python3 python3-venv \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g @github/copilot \
    # band-mcp in its own venv (Debian's system Python is externally managed).
    && python3 -m venv /opt/band-mcp \
    && /opt/band-mcp/bin/pip install --no-cache-dir band-mcp

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8080
ENTRYPOINT ["/entrypoint.sh"]
