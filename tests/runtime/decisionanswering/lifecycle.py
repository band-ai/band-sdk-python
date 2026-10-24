"""A registry peer modeling overlapping answers, redelivery and room teardown."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from dataclasses import dataclass
from enum import StrEnum

from band.runtime.decisions import DecisionRegistry


class Step(StrEnum):
    ASK = "ask"
    DELIVER = "deliver"
    TEARDOWN = "teardown"


@dataclass
class Answer:
    token: str
    payload: int
    lifetime: AsyncExitStack


class AnsweringRoom:
    def __init__(self) -> None:
        self.registry: DecisionRegistry[int] = DecisionRegistry()
        self.answers: list[Answer] = []
        self.expected: dict[str, int] = {}
        self.sequence = 0

    async def take(self, step: Step, token: str) -> None:
        match step:
            case Step.ASK:
                self.sequence += 1
                registered = self.registry.register_keyed(self.sequence, key=token)
                if token in self.expected:
                    assert registered is None
                else:
                    assert registered is not None
                    assert self.registry.try_claim(token) is registered.entry
                    future = asyncio.get_running_loop().create_future()
                    future.set_result(self.sequence)
                    lifetime = AsyncExitStack()
                    answer = await lifetime.enter_async_context(
                        self.registry.answering(registered.entry, future, timeout_s=0)
                    )
                    assert answer == self.sequence
                    self.answers.append(Answer(token, self.sequence, lifetime))
                    self.expected[token] = self.sequence
            case Step.DELIVER:
                selected = next(
                    (answer for answer in self.answers if answer.token == token), None
                )
                if selected is not None:
                    self.answers.remove(selected)
                    await selected.lifetime.aclose()
                    if self.expected.get(token) == selected.payload:
                        del self.expected[token]
            case Step.TEARDOWN:
                self.registry.cancel_all()
                self.expected.clear()
        assert dict(self.registry) == self.expected
        assert self.registry.has_claimed() == bool(self.expected)
        assert not self.registry.has_unclaimed()

    async def close(self) -> None:
        for answer in self.answers:
            await answer.lifetime.aclose()
        assert not self.registry
