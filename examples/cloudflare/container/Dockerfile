# syntax=docker/dockerfile:1
FROM --platform=linux/amd64 python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /usr/local/bin/uv
WORKDIR /app
COPY server.py server.py.lock /app/
RUN uv export --script /app/server.py --locked --no-hashes --output-file /tmp/requirements.txt \
    && uv pip install --system --requirements /tmp/requirements.txt \
    && rm /tmp/requirements.txt \
    && useradd --no-log-init --create-home --uid 1000 appuser
RUN <<'SH'
cat > /app/start <<'ENTRYPOINT'
#!/bin/sh
set -eu
ca=/etc/cloudflare/certs/cloudflare-containers-ca.crt
if [ ! -s "$ca" ]; then
    echo 'Cloudflare interception CA is missing' >&2
    exit 1
fi
bundle=$(mktemp /tmp/band-ca.XXXXXX)
cat /etc/ssl/certs/ca-certificates.crt "$ca" > "$bundle"
export SSL_CERT_FILE="$bundle"
exec python /app/server.py
ENTRYPOINT
chmod 755 /app/start
SH
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER appuser
EXPOSE 8080
ENTRYPOINT ["/app/start"]
