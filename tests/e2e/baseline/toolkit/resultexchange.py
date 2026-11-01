"""Loopback native work with independent producer and consumer release gates."""

from __future__ import annotations

import asyncio
import shlex
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from http import HTTPStatus
from pathlib import Path
from uuid import uuid4

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from band.integrations.uvicorn_server import ManagedUvicornServer


class ExchangeOperation(StrEnum):
    PRODUCE = "produce"
    COMMIT = "commit"
    CONSUME = "consume"


@dataclass
class ExchangeWork:
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)
    settled: asyncio.Event = field(default_factory=asyncio.Event)


class ResultExchange:
    """Serve bytes unknown to the model through actual native command I/O."""

    def __init__(self, *, deadline_s: float) -> None:
        self.deadline_s = deadline_s
        self.note = uuid4().hex
        self.producer = ExchangeWork()
        self.consumer = ExchangeWork()
        self.committed = asyncio.Event()
        self.result = b""
        self._url: str | None = None

    def _path(self, operation: ExchangeOperation) -> str:
        return f"/{self.note}/{operation.value}"

    async def _produce(self, request: Request) -> Response:
        if self.producer.entered.is_set():
            return Response(status_code=HTTPStatus.CONFLICT)
        self.result = uuid4().hex.encode("ascii")
        self.producer.entered.set()
        try:
            await self.producer.release.wait()
            return Response(self.result, media_type="application/octet-stream")
        finally:
            self.producer.settled.set()

    async def _commit(self, request: Request) -> Response:
        value = await request.body()
        if (
            not self.producer.release.is_set()
            or not self.result
            or value != self.result
            or self.committed.is_set()
        ):
            return Response(status_code=HTTPStatus.CONFLICT)
        self.committed.set()
        return Response(status_code=HTTPStatus.NO_CONTENT)

    async def _consume(self, request: Request) -> Response:
        if self.consumer.entered.is_set():
            return Response(status_code=HTTPStatus.CONFLICT)
        self.consumer.entered.set()
        try:
            await self.consumer.release.wait()
            if not self.committed.is_set():
                return Response(status_code=HTTPStatus.CONFLICT)
            return Response(self.result, media_type="application/octet-stream")
        finally:
            self.consumer.settled.set()

    async def wait(self, event: asyncio.Event, *, phase: str) -> None:
        try:
            async with asyncio.timeout(self.deadline_s):
                await event.wait()
        except TimeoutError:
            raise TimeoutError(f"Missing native exchange barrier: {phase}") from None

    def url(self, operation: ExchangeOperation) -> str:
        if self._url is None:
            raise RuntimeError("Result exchange has not started")
        return f"{self._url}{self._path(operation)}"

    def argv(self, operation: ExchangeOperation) -> tuple[str, ...]:
        if operation == ExchangeOperation.COMMIT:
            raise ValueError("Commit belongs to the producer command")
        url = self.url(operation)
        program = (
            "import urllib.request as u; local = u.build_opener(u.ProxyHandler({})); "
            f"value = local.open({url!r}).read(); "
        )
        if operation == ExchangeOperation.PRODUCE:
            commit = self.url(ExchangeOperation.COMMIT)
            program += (
                f"local.open(u.Request({commit!r}, data=value, method='POST')).read(); "
            )
        program += "print(value.decode('ascii'), flush=True)"
        return Path(sys.executable).as_posix(), "-c", program

    def command(self, operation: ExchangeOperation) -> str:
        return shlex.join(self.argv(operation))

    @asynccontextmanager
    async def releasing_on_exit(self) -> AsyncIterator[None]:
        try:
            yield
        finally:
            self.producer.release.set()
            self.consumer.release.set()
            for work in (self.producer, self.consumer):
                if work.entered.is_set():
                    await self.wait(work.settled, phase="released HTTP work settled")

    @asynccontextmanager
    async def running(self) -> AsyncIterator[ResultExchange]:
        server = ManagedUvicornServer(
            Starlette(
                routes=[
                    Route(self._path(ExchangeOperation.PRODUCE), self._produce),
                    Route(
                        self._path(ExchangeOperation.COMMIT),
                        self._commit,
                        methods=["POST"],
                    ),
                    Route(self._path(ExchangeOperation.CONSUME), self._consume),
                ]
            ),
            host="127.0.0.1",
            port=0,
        )
        try:
            await server.start()
            self._url = f"http://127.0.0.1:{server.bound_port}"
            async with self.releasing_on_exit():
                yield self
        finally:
            self._url = None
            await server.stop()
