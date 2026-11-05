"""Obtain transient hosted Band tokens through the public device grant."""

from __future__ import annotations

import asyncio
import time
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, Field, SecretStr

from bandteams.settings import HOSTED_AUTH, SCOPES


class Tokens(BaseModel):
    """Keep all credential fields redacted and in memory."""

    access_token: SecretStr = Field(min_length=1)
    refresh_token: SecretStr | None = None
    expires_in: int = Field(gt=0)


class DeviceCode(BaseModel):
    """Expose a bare verification page and active-entry code only."""

    device_code: SecretStr = Field(min_length=1)
    user_code: SecretStr = Field(min_length=1)
    verification_uri: str
    expires_in: int = Field(gt=0)
    interval: int = Field(default=5, gt=0)


class OAuth:
    """Use discovery without following redirects or unapproved auth hosts."""

    def __init__(self, http: httpx.AsyncClient, client_id: str) -> None:
        self.http = http
        self.client_id = client_id
        self.token_url = ""
        self.device_url = ""

    def approved(self, url: str) -> str:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.netloc != urlsplit(HOSTED_AUTH).netloc
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Authentication endpoint is outside the approved host")
        return url

    async def start(self) -> DeviceCode:
        response = await self.http.get(
            f"{HOSTED_AUTH}/.well-known/openid-configuration"
        )
        response.raise_for_status()
        metadata = response.json()
        self.token_url = self.approved(metadata["token_endpoint"])
        self.device_url = self.approved(metadata["device_authorization_endpoint"])
        response = await self.http.post(
            self.device_url, data={"client_id": self.client_id, "scope": SCOPES}
        )
        response.raise_for_status()
        code = DeviceCode.model_validate(response.json())
        self.approved(code.verification_uri)
        return code

    async def finish(self, code: DeviceCode) -> Tokens:
        interval = code.interval
        deadline = time.monotonic() + code.expires_in
        while time.monotonic() < deadline:
            await asyncio.sleep(interval)
            if time.monotonic() >= deadline:
                break
            response = await self.http.post(
                self.token_url,
                data={
                    "client_id": self.client_id,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "device_code": code.device_code.get_secret_value(),
                },
            )
            if response.is_success:
                return Tokens.model_validate(response.json())
            match response.json().get("error"):
                case "authorization_pending":
                    continue
                case "slow_down":
                    interval += 5
                case _:
                    raise ValueError("Sign-in denied or expired; request a new code")
        raise ValueError("Sign-in expired; request a new code")

    async def refresh(self, tokens: Tokens) -> Tokens:
        if tokens.refresh_token is None:
            raise ValueError("No refresh grant; sign in again")
        response = await self.http.post(
            self.token_url,
            data={
                "grant_type": "refresh_token",
                "client_id": self.client_id,
                "refresh_token": tokens.refresh_token.get_secret_value(),
            },
        )
        response.raise_for_status()
        refreshed = Tokens.model_validate(response.json())
        return refreshed.model_copy(
            update={"refresh_token": refreshed.refresh_token or tokens.refresh_token}
        )
