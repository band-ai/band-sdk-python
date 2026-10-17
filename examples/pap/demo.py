# /// script
# requires-python = ">=3.11"
# dependencies = ["band-pap>=0.1.0.dev0", "httpx>=0.28,<1"]
# ///
"""Run the complete offline PAP collaboration demo without accounts or models.

From the repository root:
uv run --no-project --with . --with ./packages/band-pap python -m examples.pap.demo
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from examples.pap.host import build


async def run_demo() -> dict[str, Any]:
    application = build()
    async with application.router.lifespan_context(application):
        host = application.state.demo
        result = await host.run()
        path = host.path
    result["evidence"]["temporary_state_removed"] = not path.exists()
    return result


def main() -> None:
    sys.stdout.write(json.dumps(asyncio.run(run_demo()), indent=2) + "\n")


if __name__ == "__main__":
    main()
