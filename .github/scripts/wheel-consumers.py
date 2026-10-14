"""Resolve published extras independently of workspace conflicts and constraints."""

from __future__ import annotations

import argparse
import logging
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

CASES: dict[str, tuple[str, int]] = {
    "pydantic-ai": ("pydantic-ai", 3),
    "crewai": ("crewai", 2),
    "strands": ("strands", 2),
    "nooa": ("nooa", 2),
    "langgraph-strands": ("langgraph,strands", 2),
    "langgraph": ("langgraph", 3),
    "parlant": ("parlant", 3),
}

STARTUP: dict[str, str] = {
    "pydantic-ai": """
import asyncio
import httpx2
from pydantic_ai import Agent
from pydantic_ai.models import infer_model
async def start():
    for alias in ('openai', 'openai-chat'):
        async with Agent(f'{alias}:gpt-4o-mini'):
            pass
        model = infer_model(f'{alias}:gpt-4o-mini')
        assert isinstance(model.provider.client._client, httpx2.AsyncClient)
        await model.provider.client.close()
asyncio.run(start())
""",
    "crewai": "from crewai import LLM; LLM(model='openai/gpt-4o-mini', api_key='test')",
    "strands": "from strands.models.openai_responses import OpenAIResponsesModel; OpenAIResponsesModel(model_id='gpt-4o-mini', client_args={'api_key': 'test'})",
    "nooa": "from nooa.unifiedllm.registry import get_llm_client; get_llm_client('openai/gpt-4o-mini', api_key='test')",
    "langgraph": "from langchain_openai import ChatOpenAI; ChatOpenAI(model='gpt-4o-mini', api_key='test')",
    "parlant": """
import asyncio
import httpx2
from pydantic import BaseModel
from parlant.adapters.nlp.openai_service import OpenAIService
from parlant.core.loggers import StdoutLogger
from parlant.core.tracer import LocalTracer
from parlant.core.meter import LocalMeter
async def start():
    tracer = LocalTracer()
    logger = StdoutLogger(tracer)
    service = OpenAIService(logger, tracer, LocalMeter(logger))
    generator = await service.get_schematic_generator(BaseModel)
    assert isinstance(generator._client._client, httpx2.AsyncClient)
    await generator._client.close()
asyncio.run(start())
""",
}


def run(command: list[str], cwd: Path, *, reject: bool = False) -> None:
    result = subprocess.run(
        command, cwd=cwd, text=True, capture_output=True, check=False
    )
    logger.info("$ %s\n%s%s", " ".join(command), result.stdout, result.stderr)
    if reject:
        if result.returncode == 0 or "No solution found" not in result.stderr:
            raise RuntimeError(
                "Mixed consumer extras did not fail dependency resolution"
            )
    elif result.returncode != 0:
        raise RuntimeError(f"Consumer validation failed: {command}")


def validate(wheel: Path, case: str) -> None:
    extras, major = CASES[case]
    with tempfile.TemporaryDirectory(prefix="band-wheel-") as directory:
        cwd = Path(directory)
        environment = cwd / "venv"
        run(["uv", "venv", "--python", "3.12", str(environment)], cwd)
        python = environment / "bin/python"
        run(
            [
                "uv",
                "pip",
                "install",
                "--python",
                str(python),
                f"{wheel}[{extras}]",
                *(["openai>=3.26,<4"] if major == 3 else []),
            ],
            cwd,
        )
        run(["uv", "pip", "check", "--python", str(python)], cwd)
        startup = "\n".join(STARTUP[extra] for extra in extras.split(","))
        probe = (
            f"import os, importlib.metadata as metadata\nos.environ['OPENAI_API_KEY']='test'\nversion=metadata.version('openai')\nassert int(version.split('.')[0]) == {major}, version\n"
            + startup
            + "\nimport logging; logging.basicConfig(level=logging.INFO); logging.info('OpenAI %s: provider starts', version)"
        )
        run([str(python), "-c", probe], cwd)
        if case in {"crewai", "strands", "nooa"}:
            run(
                [
                    "uv",
                    "pip",
                    "install",
                    "--python",
                    str(python),
                    f"{wheel}[pydantic-ai,{extras}]",
                ],
                cwd,
                reject=True,
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    parser.add_argument("--case", choices=CASES, required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    validate(args.wheel.resolve(), args.case)


if __name__ == "__main__":
    main()
