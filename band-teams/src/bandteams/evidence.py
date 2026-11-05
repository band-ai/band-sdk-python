"""Persist restricted metadata claims before dependent side effects."""

from __future__ import annotations

import json
import sqlite3
import time
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import Field

from bandteams.settings import StrictModel


class Delivery(StrEnum):
    """Keep Band acceptance independent of Teams receipt delivery."""

    REVIEW = "Not sent to Band"
    PENDING = "Send accepted for processing"
    SENDING = "Sending to Band"
    SENT = "Delivered to Band"
    FAILED = "Not delivered"
    UNKNOWN = "Delivery unknown"
    CANCELLED = "Cancelled; not sent to Band"
    EXPIRED = "Expired; not sent to Band"


class Receipt(StrEnum):
    """Record whether the known card reflects the outcome."""

    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"


class Verdict(StrEnum):
    """Distinguish absence of access from a measured incompatibility."""

    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    NOT_RUN = "NOT RUN"


class Claim(StrictModel):
    """Store correlation only; bodies, hashes and credentials have no field."""

    proposal_id: str
    tenant: str
    actor: str
    alias: str
    band_user: str
    room: str
    generation: str
    team: str
    channel: str
    conversation: str
    source: str
    recipient: str
    version: int = 1
    expires_at: float
    review_id: str = ""
    delivery: Delivery = Delivery.REVIEW
    receipt: Receipt = Receipt.PENDING
    band_id: str = ""


class Probe(StrictModel):
    """Record one redacted measurement with no free-form payload metadata."""

    probe: str = Field(pattern=r"^Q[1-9]$")
    boundary: str = Field(pattern=r"^(offline|live)$")
    verdict: Verdict
    reason: str = Field(pattern=r"^[a-z0-9 -]{1,160}$")
    artifacts: tuple[str, ...] = ()


class Journal:
    """Commit metadata on local SQLite before dependent side effects."""

    def __init__(self, directory: Path) -> None:
        if directory.is_symlink():
            raise ValueError("State directory cannot be a symlink")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory.chmod(0o700)
        self.path = directory / "metadata.sqlite3"
        if self.path.is_symlink():
            raise ValueError("Database cannot be a symlink")
        owner_path = directory / "owner.sqlite3"
        if owner_path.is_symlink():
            raise ValueError("Owner database cannot be a symlink")
        self.owner = sqlite3.connect(owner_path, timeout=0)
        try:
            self.owner.execute("BEGIN EXCLUSIVE")
        except sqlite3.Error:
            self.owner.close()
            raise ValueError(
                "Another process already owns this state directory"
            ) from None
        owner_path.chmod(0o600)
        self.db = sqlite3.connect(self.path)
        self.path.chmod(0o600)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS records (sequence INTEGER PRIMARY KEY, run TEXT NOT NULL, at REAL NOT NULL, kind TEXT NOT NULL, value TEXT NOT NULL)"
        )
        self.run = str(uuid4())
        self.failed = False
        self.append("run", {"generation": self.run})

    @property
    def records(self) -> list[dict[str, Any]]:
        return [
            {"run": run, "at": at, "kind": kind, "value": json.loads(value)}
            for run, at, kind, value in self.db.execute(
                "SELECT run, at, kind, value FROM records ORDER BY sequence"
            )
        ]

    def append(self, kind: str, value: dict[str, Any]) -> None:
        if self.failed:
            raise OSError("Evidence journal unavailable")
        try:
            with self.db:
                self.db.execute(
                    "INSERT INTO records(run, at, kind, value) VALUES (?, ?, ?, ?)",
                    (self.run, time.time(), kind, json.dumps(value)),
                )
        except sqlite3.Error as error:
            self.failed = True
            raise OSError("Evidence journal unavailable") from error

    def claim(self, claim: Claim) -> None:
        self.append("claim", claim.model_dump(mode="json"))

    def probe(self, probe: Probe) -> None:
        self.append("probe", probe.model_dump(mode="json"))

    def resource(self, alias: str, conversation: str, activity: str) -> None:
        self.append(
            "resource",
            {"alias": alias, "conversation": conversation, "activity": activity},
        )

    def recover(self) -> dict[str, Claim]:
        """Fence previous process work without replaying any remote write."""
        latest = {
            record["value"]["proposal_id"]: Claim.model_validate(record["value"])
            for record in self.records
            if record["kind"] == "claim"
        }
        for key, claim in latest.items():
            match claim.delivery:
                case Delivery.REVIEW:
                    claim = claim.model_copy(update={"delivery": Delivery.CANCELLED})
                case Delivery.PENDING:
                    claim = claim.model_copy(update={"delivery": Delivery.FAILED})
                case Delivery.SENDING:
                    claim = claim.model_copy(update={"delivery": Delivery.UNKNOWN})
            self.claim(claim)
            latest[key] = claim
        return latest

    def close(self) -> None:
        self.db.close()
        self.owner.close()
