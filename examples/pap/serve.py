# /// script
# requires-python = ">=3.11"
# dependencies = ["band-pap>=0.1.0.dev0", "uvicorn>=0.30,<1", "pydantic-settings>=2,<3"]
# ///
"""Serve a host-configured PAP application; see the adjacent deployment guide.

From this checkout: uv run --all-packages python examples/pap/serve.py
PAP_APP_FACTORY names the host's synchronous ASGI factory, e.g. myapp.pap:build.
"""

from __future__ import annotations

import uvicorn
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore",
        case_sensitive=False,
        env_ignore_empty=True,
        env_file=".env.test",
    )
    PAP_APP_FACTORY: str = Field(pattern=r"^[a-zA-Z][\w.]*:[a-zA-Z]\w*$")
    PAP_BIND_HOST: str = "127.0.0.1"
    PAP_BIND_PORT: int = Field(default=8080, ge=1, le=65535)


def main() -> None:
    settings = Settings()
    uvicorn.run(
        settings.PAP_APP_FACTORY,
        factory=True,
        host=settings.PAP_BIND_HOST,
        port=settings.PAP_BIND_PORT,
        proxy_headers=False,
        access_log=False,
    )


if __name__ == "__main__":
    main()
