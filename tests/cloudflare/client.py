"""Host-side client for the harness routes running inside workerd."""

from __future__ import annotations

from typing import Any

import httpx

from tests.cloudflare.contract import Scenario


class WorkerRuntime:
    def __init__(self, client: httpx.Client) -> None:
        self.client = client

    def run(self, scenario: Scenario, **payload: Any) -> dict[str, Any]:
        """Run one harness scenario and return its observations."""
        return self.client.post(f"/{scenario}", json=payload).raise_for_status().json()
