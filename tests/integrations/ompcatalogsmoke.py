"""Exercise installed OMP CLI discovery and native RPC without a room or turn.

Run with ``python -m tests.integrations.ompcatalogsmoke --executable /path/to/omp``.
The public host probe exercises the CLI-first path. A second, separately owned
native connection asks the actual get_available_models RPC for its wire catalog.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import tempfile
from pathlib import Path

from band.adapters.omp import OmpAdapter, OmpAdapterConfig
from band.integrations.omprpc import OmpRpcClient

logger = logging.getLogger(__name__)


async def smoke(executable: str, timeout_s: float, cwd: str | None) -> None:
    with tempfile.TemporaryDirectory(prefix="band-omp-catalog-wire-") as sandbox:
        workspace = str(Path(cwd).resolve()) if cwd is not None else sandbox
        adapter = OmpAdapter(
            OmpAdapterConfig(command=(executable, "--mode", "rpc"), cwd=workspace)
        )
        try:
            catalog = await adapter.probe_models(timeout_s=timeout_s)
            assert not adapter._rooms
            assert all(choice.id for choice in catalog.models)
            assert catalog.current_model is None

            async def read_native() -> int:
                runtime = OmpRpcClient(
                    (*adapter._spawn_command(workspace), "--no-session"),
                    env=adapter._spawn_env(),
                )
                adapter._harness_probe.own(runtime.close)
                await runtime.start()
                response = await runtime.request("get_available_models")
                models = response["models"]
                assert isinstance(models, list)
                assert all(
                    isinstance(model, dict)
                    and model.get("id")
                    and model.get("provider")
                    for model in models
                )
                return len(models)

            native_count = await adapter._harness_probe.run(
                read_native, timeout_s=timeout_s
            )
            assert not adapter._rooms
            logger.info(
                "OMP host catalog: %d models; native RPC catalog: %d models; no model turns",
                len(catalog.models),
                native_count,
            )
        finally:
            await adapter.cleanup_all()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", default="omp")
    parser.add_argument("--timeout-s", type=float, default=60.0)
    parser.add_argument("--cwd")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(smoke(args.executable, args.timeout_s, args.cwd))


if __name__ == "__main__":
    main()
