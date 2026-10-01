# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[strands]>=1.6.0"]
# ///
"""
Strands agent on Amazon Bedrock.

Strands is model-agnostic, but it has no provider-prefix string shorthand: a
bare ``StrandsAdapterConfig(model=...)`` string is a **Bedrock** model id, not
a provider route. The other examples therefore pass an ``OpenAIModel`` as
``llm=``, while this one uses the string form and its explicit ``BedrockModel``
equivalent — reach for the latter when you need a region, profile, or client
config.

Requires Band credentials plus AWS credentials with Bedrock access, e.g.:
    aws configure          # or AWS_PROFILE / AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY
    export AWS_REGION=us-east-1

Run with:
    uv run examples/strands/05_bedrock_model.py
"""

from __future__ import annotations

import asyncio
import logging
import os

from dotenv import load_dotenv
from strands.models import BedrockModel

from band import Agent, configure_logging
from band.adapters import StrandsAdapter, StrandsAdapterConfig

configure_logging(logging.INFO)
logger = logging.getLogger(__name__)

MODEL_ID = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"


async def main() -> None:
    load_dotenv()

    # `StrandsAdapterConfig(model=MODEL_ID)` is equivalent to this and picks up the
    # ambient AWS region; construct the provider when you need to pin one.
    model = BedrockModel(model_id=MODEL_ID, region_name=os.getenv("AWS_REGION"))

    adapter = StrandsAdapter(
        StrandsAdapterConfig(
            custom_section="You are a helpful assistant. Be concise and friendly."
        ),
        llm=model,
    )

    logger.info("Starting Strands agent on Bedrock model %s...", MODEL_ID)
    async with Agent.from_config(
        "strands_agent",
        adapter=adapter,
    ) as agent:
        await agent.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
