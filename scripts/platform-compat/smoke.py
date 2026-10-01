"""Released-wheel messaging check against an explicitly configured local stack."""

from __future__ import annotations

import asyncio
import importlib.metadata
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit
from uuid import uuid4

from band_rest import AsyncRestClient
from pydantic import SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

import band
from band import Agent
from band.core.simple_adapter import SimpleAdapter

# Only test helpers are imported from this checkout; src is never on sys.path.
sys.path.append(str(Path(sys.argv[1]).resolve()))
sys.path.append(str(Path(__file__).resolve().parent))
from report import write_report

from tests.e2e.baseline.toolkit.user_ops import UserOps

if TYPE_CHECKING:
    from band.core.protocols import AgentToolsProtocol
    from band.core.types import ChatMessage, PlatformMessage


class ReportSettings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore",
        case_sensitive=False,
        env_ignore_empty=True,
        hide_input_in_errors=True,
    )
    results_dir: Path


class Settings(ReportSettings):
    band_rest_url: str
    band_base_url: str
    band_ws_url: str
    band_api_key_user: SecretStr
    band_api_key: SecretStr
    test_agent_id: str

    def validate_endpoints(self) -> None:
        for value, scheme in (
            (self.band_rest_url, "http"),
            (self.band_base_url, "http"),
            (self.band_ws_url, "ws"),
        ):
            url = urlsplit(value)
            if (
                url.scheme != scheme
                or url.hostname not in {"localhost", "127.0.0.1"}
                or url.username
                or url.password
            ):
                raise ValueError(
                    "Only explicit local ephemeral-stack endpoints are supported"
                )


class RoundtripAdapter(SimpleAdapter[str]):
    def __init__(self, token: str) -> None:
        super().__init__()
        self.token = token
        self.received = asyncio.Event()

    async def on_message(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        history: list[ChatMessage],
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        if self.token in msg.content:
            await tools.send_message(self.token, mentions=[msg.sender_id])
            self.received.set()


async def scenario(settings: Settings, row: dict[str, object]) -> None:
    token = f"sdk-smoke-{uuid4().hex}"
    adapter = RoundtripAdapter(token)
    agent = Agent.create(
        adapter=adapter,
        agent_id=settings.test_agent_id,
        api_key=settings.band_api_key.get_secret_value(),
        rest_url=settings.band_rest_url,
        ws_url=settings.band_ws_url,
    )
    user = UserOps(
        AsyncRestClient(
            api_key=settings.band_api_key_user.get_secret_value(),
            base_url=settings.band_rest_url,
        )
    )
    room_id = None
    try:
        async with asyncio.timeout(90):
            room_id = await user.create_room(title=token)
            row["room_id"] = room_id
            write_report(settings.results_dir, row)
            await user.add_participant(room_id, settings.test_agent_id)
            await agent.start()
            row["category"] = "runtime"
            await user.send_message(
                room_id,
                token,
                mention_id=settings.test_agent_id,
                mention_name="smoke-agent",
            )
            await adapter.received.wait()
            messages = await user.list_messages(room_id)
            if not any(
                message.sender_id == settings.test_agent_id and message.content == token
                for message in messages
            ):
                raise AssertionError("Agent reply was not persisted in the room")
            row.update(
                outcome="pass", reason="SDK received the probe and persisted its reply"
            )
    finally:
        cleanup_errors = []
        try:
            await asyncio.wait_for(agent.stop(), timeout=15)
        except Exception:  # noqa: BLE001 - cleanup must attempt both resources
            cleanup_errors.append("agent-stop")
        if room_id is not None:
            try:
                await asyncio.wait_for(user.delete_room(room_id), timeout=15)
            except Exception:  # noqa: BLE001 - preserve original scenario failure
                cleanup_errors.append("room-delete")
        row["cleanup"] = "failed" if cleanup_errors else "pass"
        if cleanup_errors and row["outcome"] == "pass":
            row.update(outcome="fail", category="cleanup", reason="Cleanup failed")


def main() -> int:
    # SDK transport logging can include request data; reports use fixed reasons only.
    logging.disable(logging.CRITICAL)
    row: dict[str, object] = {
        "outcome": "incomplete",
        "category": "setup",
        "cleanup": "not-started",
    }
    report_settings = ReportSettings()
    try:
        settings = Settings()
        settings.validate_endpoints()
        installed = Path(band.__file__).resolve()
        if not installed.is_relative_to(Path(sys.prefix).resolve()):
            raise ValueError("SDK import is outside the isolated environment")
        row["versions"] = {
            name: importlib.metadata.version(name)
            for name in ("band-sdk", "band-sdk-core", "band-client-rest")
        }
        row["sdk_import"] = str(installed)
        asyncio.run(scenario(settings, row))
    except ValidationError:
        row.update(outcome="incomplete", reason="Invalid harness configuration")
    except TimeoutError:
        row.update(outcome="incomplete", reason="Scenario exceeded its deadline")
    except Exception as error:  # noqa: BLE001 - report without exposing API error bodies
        row.update(
            outcome="fail" if row["category"] == "runtime" else "incomplete",
            reason=f"{row['category']} error ({type(error).__name__})",
        )
    write_report(report_settings.results_dir, row)
    return 0 if row["outcome"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
