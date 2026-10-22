"""Record bounded provider inputs/results once, before any result can become an effect."""

from __future__ import annotations

import base64
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from remote_agent.storage import canonical, digest
from remote_agent.types import JsonObject

if TYPE_CHECKING:
    from remote_agent.store import Store


logger = logging.getLogger(__name__)


class ModelUnresolved(Exception):
    """No definitive durable result. The same call must not be sampled again."""


class ModelFailure(Exception):
    """A recorded, completed provider callback failed."""


class ModelJournal:
    PROFILE = "remote-agent/model-input/1"
    MAX_INPUT_BYTES = 524_288
    MAX_OUTPUT_BYTES = 262_144

    def __init__(self, store: Store) -> None:
        self.store = store

    def run(
        self,
        identifier: str,
        operation: str,
        provider: JsonObject,
        prepare: Callable[[], JsonObject],
        execute: Callable[[JsonObject], JsonObject],
        *,
        max_calls: int,
        timeout_ms: int,
        usage: Callable[[JsonObject], JsonObject | None] | None = None,
    ) -> JsonObject:
        key = "model:" + identifier
        with self.store.lock:
            prior = self.store.get(key)
            if prior:
                if prior["operation"] != operation or prior["provider"] != provider:
                    raise ModelUnresolved(
                        "Model invocation identity or provider changed"
                    )
                if prior["status"] != "completed":
                    raise ModelUnresolved(
                        "Model invocation has no durable result; reconcile before retrying"
                    )
                return self.result(prior["execution"])
            calls = self.store.get("model_calls", 0)
            if calls >= max_calls:
                raise ModelFailure("Model call budget exhausted")
            # Both the output event and materialized result index must fit in one transaction.
            if self.store.max_record_bytes < self.MAX_OUTPUT_BYTES * 8 // 3 + 16_384:
                raise ModelFailure(
                    "Journal record limit cannot retain the bounded model result"
                )
            request = prepare()
            data = {
                "profile": self.PROFILE,
                "id": identifier,
                "operation": operation,
                "provider": provider,
                "request": request,
                "timeout_ms": timeout_ms,
                "max_output_bytes": self.MAX_OUTPUT_BYTES,
            }
            if len(canonical(data)) > self.MAX_INPUT_BYTES:
                raise ModelFailure("Model request exceeds the journaled input limit")
            fingerprint = digest(data)
            pending = {
                "fingerprint": fingerprint,
                "operation": operation,
                "provider": provider,
                "status": "started",
            }
            self.store.commit(
                "model_input", data, updates={key: pending, "model_calls": calls + 1}
            )
        try:
            execution = execute(request)
            encoded = canonical(execution)
            # Process captures are base64 records, so their wire encoding can exceed the raw cap.
            if len(encoded) > self.MAX_OUTPUT_BYTES * 4 // 3 + 8192:
                execution = {
                    "profile": "remote-agent/callback-result/1",
                    "value": None,
                    "failure": "Model callback output capacity exceeded",
                    "uncertain": True,
                    "prefix": base64.b64encode(
                        encoded[: self.MAX_OUTPUT_BYTES]
                    ).decode(),
                }
        except Exception as error:  # noqa: BLE001 -- Every provider callback outcome must be captured before effects can follow.
            execution = {
                "profile": "remote-agent/callback-result/1",
                "value": None,
                "failure": str(error)[:2000],
                "uncertain": bool(getattr(error, "uncertain", False)),
            }
        output = {
            "profile": "remote-agent/model-output/1",
            "id": identifier,
            "input_hash": fingerprint,
            "execution": execution,
        }
        try:
            updates = {key: dict(pending, status="completed", execution=execution)}
            # Usage is an optional diagnostic projection of the captured response.
            # Commit it atomically with that response, so recovery never counts it twice.
            with self.store.lock:
                try:
                    reported = usage(execution) if usage is not None else None
                    aggregate = _add_usage(self.store.get("model_usage", {}), reported)
                    if aggregate is not None:
                        updates["model_usage"] = aggregate
                except Exception as error:  # noqa: BLE001 -- Optional usage callbacks must not discard an already captured model outcome.
                    logger.debug(
                        "Model usage remains unreported: %s", type(error).__name__
                    )
                self.store.commit("model_output", output, updates=updates)
        except Exception as error:
            raise ModelUnresolved(
                "Model result was not durably recorded; reconcile before retrying"
            ) from error
        return self.result(execution)

    @staticmethod
    def result(execution: JsonObject) -> JsonObject:
        if execution["profile"] == "remote-agent/callback-result/1":
            if execution["failure"]:
                exception = ModelUnresolved if execution["uncertain"] else ModelFailure
                raise exception(execution["failure"])
        elif execution["profile"] == "remote-agent/process-result/1":
            if execution["failure"] or not execution["complete"]:
                raise ModelUnresolved(
                    "Provider execution has no definitive result: "
                    + str(execution["failure"])
                )
        else:
            raise ModelUnresolved("Unsupported recorded model execution profile")
        return execution


def _add_usage(previous: JsonObject, reported: object) -> JsonObject | None:
    fields = ("input_tokens", "cached_input_tokens", "output_tokens")
    if (
        not isinstance(reported, dict)
        or set(reported) != {*fields, "estimated_cost_micro_usd"}
        or any(
            type(reported[k]) is not int or not 0 <= reported[k] <= 2_097_152
            for k in fields
        )
        or reported["cached_input_tokens"] > reported["input_tokens"]
    ):
        return None
    cost = reported["estimated_cost_micro_usd"]
    if cost is not None and (type(cost) is not int or not 0 <= cost <= 419_430_400):
        return None
    aggregate = {k: previous.get(k, 0) + reported[k] for k in fields}
    aggregate.update(
        reported_calls=previous.get("reported_calls", 0) + 1,
        costed_calls=previous.get("costed_calls", 0) + int(cost is not None),
        estimated_cost_micro_usd=previous.get("estimated_cost_micro_usd", 0)
        + (cost or 0),
    )
    canonical(aggregate)
    return aggregate


def usage_summary(store: Store) -> JsonObject:
    aggregate = store.get("model_usage", {})
    fields = (
        "reported_calls",
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "costed_calls",
        "estimated_cost_micro_usd",
    )
    result = {k: aggregate.get(k, 0) for k in fields}
    result.update(
        unreported_calls=max(0, store.get("model_calls", 0) - result["reported_calls"]),
        currency="USD",
    )
    return result
