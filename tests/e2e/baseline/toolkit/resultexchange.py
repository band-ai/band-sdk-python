"""Loopback native work with independent producer and consumer release gates."""

from __future__ import annotations

import asyncio
import sys
import tempfile
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

BARRIER = "native exchange barrier"


class ExchangeOperation(StrEnum):
    PRODUCE = "produce"
    COMMIT = "commit"
    CONSUME = "consume"


@dataclass
class ExchangeWork:
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)
    settled: asyncio.Event = field(default_factory=asyncio.Event)


async def wait_for(event: asyncio.Event, *, deadline_s: float, barrier: str) -> None:
    """Await ``event``; the deadline only guards a hang."""
    try:
        async with asyncio.timeout(deadline_s):
            await event.wait()
    except TimeoutError:
        raise TimeoutError(f"Missing {barrier}") from None


async def release_all(
    works: tuple[ExchangeWork, ...], *, deadline_s: float, barrier: str
) -> None:
    """Release every hold and await each entered one's settle."""
    for work in works:
        work.release.set()
    for work in works:
        if work.entered.is_set():
            await wait_for(
                work.settled,
                deadline_s=deadline_s,
                barrier=f"{barrier}: released work settled",
            )


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
        self._scripts: Path | None = None

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
        await wait_for(event, deadline_s=self.deadline_s, barrier=f"{BARRIER}: {phase}")

    def url(self, operation: ExchangeOperation) -> str:
        if self._url is None:
            raise RuntimeError("Result exchange has not started")
        return f"{self._url}{self._path(operation)}"

    def _program(self, operation: ExchangeOperation) -> str:
        url = self.url(operation)
        program = (
            "import urllib.request as u\n"
            "local = u.build_opener(u.ProxyHandler({}))\n"
            f"value = local.open({url!r}).read()\n"
        )
        if operation == ExchangeOperation.PRODUCE:
            commit = self.url(ExchangeOperation.COMMIT)
            program += (
                f"local.open(u.Request({commit!r}, data=value, method='POST')).read()\n"
            )
        return program + "print(value.decode('ascii'), flush=True)\n"

    def _script(self, operation: ExchangeOperation) -> Path:
        # The note in the name puts it on the agent's command line, so a
        # leaked command line trips the privacy sentinel.
        if self._scripts is None:
            raise RuntimeError("Result exchange has not started")
        return self._scripts / f"{self.note}-{operation.value}.py"

    def argv(self, operation: ExchangeOperation) -> tuple[str, str]:
        if operation == ExchangeOperation.COMMIT:
            raise ValueError("Commit belongs to the producer command")
        return Path(sys.executable).as_posix(), self._script(operation).as_posix()

    def command(self, operation: ExchangeOperation) -> str:
        """A command line that needs no quoting, so any host shell runs it as is."""
        argv = self.argv(operation)
        if any(part.split() != [part] for part in argv):
            raise RuntimeError(f"Command path needs shell quoting: {argv}")
        return " ".join(argv)

    @asynccontextmanager
    async def releasing_on_exit(self) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await release_all(
                (self.producer, self.consumer),
                deadline_s=self.deadline_s,
                barrier=BARRIER,
            )

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
            with tempfile.TemporaryDirectory(prefix="band-e2e-exchange-") as scripts:
                self._scripts = Path(scripts).resolve()
                for operation in (ExchangeOperation.PRODUCE, ExchangeOperation.CONSUME):
                    self._script(operation).write_text(
                        self._program(operation), encoding="utf-8"
                    )
                async with self.releasing_on_exit():
                    yield self
        finally:
            self._url = None
            self._scripts = None
            await server.stop()
