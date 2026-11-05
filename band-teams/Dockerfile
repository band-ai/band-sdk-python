FROM python:3.12-slim
RUN pip install --no-cache-dir uv==0.12.23
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --locked --no-dev --no-editable && useradd --uid 10001 --create-home bridge && mkdir /state && chown bridge /state
USER bridge
EXPOSE 3978
ENTRYPOINT ["/app/.venv/bin/python", "-m", "bandteams"]
CMD ["serve", "--manifest", "/config/run.json", "--teams-credentials", "/run/secrets/teams.env", "--band-env", "/run/secrets/band.env", "--state", "/state"]
