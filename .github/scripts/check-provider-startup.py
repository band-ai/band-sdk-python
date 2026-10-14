"""Start installed providers without inference and verify the selected OpenAI major."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from importlib.metadata import version

from pydantic import BaseModel
from wheelchecks import Framework

logger = logging.getLogger(__name__)
MODEL_ID = "gpt-4o-mini"
API_KEY = "test"


async def start_pydantic_ai() -> None:
    import httpx2  # noqa: PLC0415 -- only modern consumers install HTTPX2
    from pydantic_ai.models import infer_model  # noqa: PLC0415 -- optional consumer dependency

    from band.adapters.pydantic_ai import (  # noqa: PLC0415 -- only the Pydantic AI consumer installs this adapter
        PydanticAIAdapter,
        PydanticAIAdapterConfig,
    )

    for alias in ("openai", "openai-chat"):
        adapter = PydanticAIAdapter(
            PydanticAIAdapterConfig(model=f"{alias}:{MODEL_ID}")
        )
        await adapter.on_started("Wheel consumer", "Provider startup check")
        assert adapter._agent is not None
        async with adapter._agent:
            model = infer_model(f"{alias}:{MODEL_ID}")
            assert isinstance(model.provider.client._client, httpx2.AsyncClient)


async def start_crewai() -> None:
    from crewai import LLM  # noqa: PLC0415 -- only the CrewAI consumer installs this provider

    LLM(model=f"openai/{MODEL_ID}", api_key=API_KEY)


async def start_strands() -> None:
    from strands.models.openai_responses import OpenAIResponsesModel  # noqa: PLC0415 -- only the Strands consumer installs this provider

    OpenAIResponsesModel(model_id=MODEL_ID, client_args={"api_key": API_KEY})


async def start_nooa() -> None:
    from nooa.unifiedllm.registry import get_llm_client  # noqa: PLC0415 -- only the NOOA consumer installs this provider

    get_llm_client(f"openai/{MODEL_ID}", api_key=API_KEY)


async def start_langgraph() -> None:
    from langchain_openai import ChatOpenAI  # noqa: PLC0415 -- only LangGraph consumers install this provider

    model = ChatOpenAI(model=MODEL_ID, api_key=API_KEY)
    await model.root_async_client.close()
    model.root_client.close()


async def start_parlant() -> None:
    import httpx2  # noqa: PLC0415 -- only modern consumers install HTTPX2
    from parlant.adapters.nlp.openai_service import OpenAIService  # noqa: PLC0415 -- only the Parlant consumer installs this provider
    from parlant.core.loggers import StdoutLogger  # noqa: PLC0415 -- optional consumer dependency
    from parlant.core.meter import LocalMeter  # noqa: PLC0415 -- optional consumer dependency
    from parlant.core.tracer import LocalTracer  # noqa: PLC0415 -- optional consumer dependency

    tracer = LocalTracer()
    provider_logger = StdoutLogger(tracer)
    service = OpenAIService(provider_logger, tracer, LocalMeter(provider_logger))
    generator = await service.get_schematic_generator(BaseModel)
    assert isinstance(generator._client._client, httpx2.AsyncClient)
    await generator._client.close()


async def start_provider(framework: Framework) -> None:
    match framework:
        case Framework.PYDANTIC_AI:
            await start_pydantic_ai()
        case Framework.CREWAI:
            await start_crewai()
        case Framework.STRANDS:
            await start_strands()
        case Framework.NOOA:
            await start_nooa()
        case Framework.LANGGRAPH:
            await start_langgraph()
        case Framework.PARLANT:
            await start_parlant()
    logger.info("%s provider starts", framework)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--framework", type=Framework, choices=Framework, action="append", required=True
    )
    parser.add_argument("--openai-major", type=int, required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    selected_version = version("openai")
    assert int(selected_version.split(".")[0]) == args.openai_major, selected_version
    # Upstream string-based providers read this; no real credentials or inference are needed.
    os.environ["OPENAI_API_KEY"] = API_KEY
    for framework in args.framework:
        await start_provider(framework)
    logger.info("OpenAI %s: provider startup checks passed", selected_version)


if __name__ == "__main__":
    asyncio.run(main())
