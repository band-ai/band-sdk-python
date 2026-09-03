"""Entry point for ``python -m bridge_core``.

Loads :class:`BridgeConfig` from the ``BAND_BRIDGE_AGENTS`` env var and runs::

    BAND_BRIDGE_AGENTS='[{"agent_id":"...","api_key":"...","target":{"type":"http","url":"..."}}]' \\
        python -m bridge_core
"""

from __future__ import annotations

import asyncio

from .bridge import main


def _main() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    _main()
