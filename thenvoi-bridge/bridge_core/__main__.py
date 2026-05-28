"""Entry point for ``python -m bridge_core``.

Loads :class:`BridgeConfig` from the ``THENVOI_BRIDGE_AGENTS`` env var and runs::

    THENVOI_BRIDGE_AGENTS='[{"agent_id":"...","api_key":"...","target":{"type":"http","url":"..."}}]' \\
        python -m bridge_core
"""

from __future__ import annotations

import asyncio


def _main() -> None:
    from .bridge import main

    asyncio.run(main())


if __name__ == "__main__":
    _main()
