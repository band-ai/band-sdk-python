"""A Parlant NLP service that never answers, for driving the real engine
without a model: every generation parks until the engine cancels it.

Import only where ``parlant`` is installed (the dev-parlant venv).
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any, Generic, TypeVar

from parlant.core.nlp.embedding import Embedder, EmbeddingResult
from parlant.core.nlp.generation import SchematicGenerator
from parlant.core.nlp.moderation import ModerationService, NoModeration
from parlant.core.nlp.service import NLPService
from parlant.core.nlp.tokenization import EstimatingTokenizer, ZeroEstimatingTokenizer

T = TypeVar("T")

EMBEDDING_DIMENSIONS = 8
MAX_TOKENS = 100_000


class ParkedGenerator(SchematicGenerator[T], Generic[T]):
    """Every generation parks, so a turn stays in the engine until cancelled."""

    def __init__(self, entered: asyncio.Event) -> None:
        self._entered = entered

    async def generate(self, prompt: Any, hints: Mapping[str, Any] = {}) -> Any:
        self._entered.set()
        await asyncio.Future[None]()

    @property
    def id(self) -> str:
        return "parked"

    @property
    def max_tokens(self) -> int:
        return MAX_TOKENS

    @property
    def tokenizer(self) -> EstimatingTokenizer:
        return ZeroEstimatingTokenizer()


class ZeroEmbedder(Embedder):
    async def embed(
        self, texts: list[str], hints: Mapping[str, Any] = {}
    ) -> EmbeddingResult:
        return EmbeddingResult(vectors=[[0.0] * EMBEDDING_DIMENSIONS for _ in texts])

    @property
    def id(self) -> str:
        return "zero"

    @property
    def max_tokens(self) -> int:
        return MAX_TOKENS

    @property
    def tokenizer(self) -> EstimatingTokenizer:
        return ZeroEstimatingTokenizer()

    @property
    def dimensions(self) -> int:
        return EMBEDDING_DIMENSIONS


class StubNLP(NLPService):
    """``entered`` is set once the engine asks the model anything."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()

    def factory(self, container: Any) -> NLPService:
        """The ``nlp_service=`` callable a Parlant server takes."""
        return self

    @property
    def supports_streaming(self) -> bool:
        return False

    async def get_schematic_generator(
        self, t: type[T], hints: Mapping[str, Any] = {}
    ) -> SchematicGenerator[T]:
        return ParkedGenerator[t](self.entered)

    async def get_streaming_text_generator(self, hints: Mapping[str, Any] = {}) -> Any:
        raise NotImplementedError

    async def get_embedder(self, hints: Mapping[str, Any] = {}) -> Embedder:
        return ZeroEmbedder()

    async def get_moderation_service(self) -> ModerationService:
        return NoModeration()
