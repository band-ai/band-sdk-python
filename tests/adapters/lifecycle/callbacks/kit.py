"""A provider process paired with its actual lifecycle host and tool resource."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from tests.adaptercleanup import Host
from tests.adapters.lifecycle.kit import RetentionHost


@dataclass
class CallbackHost:
    provider: RetentionHost
    host: Host
    resource: httpx.AsyncClient
    fail_release: bool = False

    async def close_resource(self) -> None:
        if self.fail_release:
            raise OSError("Resource release failed")
        await self.resource.aclose()
