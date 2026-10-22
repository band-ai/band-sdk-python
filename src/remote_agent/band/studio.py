"""The selected author consumes durable platform jobs and stages exact completion retries."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from remote_agent.band.authorack import acknowledges_completion
from remote_agent.band.authoring import bounded_text, canonical, validate_result
from remote_agent.band.authortransport import AuthorCapacityError
from remote_agent.types import JsonObject

if TYPE_CHECKING:
    from remote_agent.band.authoring import Journal


class StudioAgent:
    def __init__(
        self,
        journal: Journal,
        transport: Callable[[str, JsonObject], JsonObject],
        *,
        on_hold: Callable[[JsonObject], object] | None = None,
    ) -> None:
        self.journal, self.transport, self.db = journal, transport, journal.db
        self.on_hold = on_hold
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS studio_claim(singleton INTEGER PRIMARY KEY CHECK(singleton=1), id TEXT);
          INSERT OR IGNORE INTO studio_claim VALUES(1,NULL);
          CREATE TABLE IF NOT EXISTS studio_jobs(job TEXT, attempt INTEGER, local_id TEXT NOT NULL,
            body TEXT, acknowledged INTEGER NOT NULL DEFAULT 0, rejected_claim TEXT,
            completion_hold TEXT, PRIMARY KEY(job,attempt));
        """)
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(studio_jobs)")}
        for column in ("rejected_claim", "completion_hold"):
            if column not in columns:
                self.db.execute(
                    "ALTER TABLE studio_jobs ADD COLUMN " + column + " TEXT"
                )
        self.db.commit()

    def held_completions(self) -> list[JsonObject]:
        """Inspect unresolved delivery without exposing saved room/model content."""
        return [
            {
                "job": job,
                "attempt": attempt,
                "local_id": local_id,
                "status": hold,
                "acknowledged": False,
            }
            for job, attempt, local_id, hold in self.db.execute(
                "SELECT job,attempt,local_id,completion_hold FROM studio_jobs "
                "WHERE acknowledged=0 AND completion_hold IS NOT NULL ORDER BY rowid"
            )
        ]

    def retry_held(self, identifier: str, attempt: int) -> JsonObject:
        """One explicit delivery attempt; failures leave the hold and exact body intact."""
        row = self.db.execute(
            "SELECT local_id,body,rejected_claim FROM studio_jobs "
            "WHERE job=? AND attempt=? AND acknowledged=0 AND completion_hold IS NOT NULL",
            (identifier, attempt),
        ).fetchone()
        if row is None:
            raise ValueError("No matching held completion")
        local_id, body, rejected_claim = row
        acknowledged = self._complete(
            identifier, attempt, local_id, body, rejected_claim, held=True
        )
        hold = self.db.execute(
            "SELECT completion_hold FROM studio_jobs WHERE job=? AND attempt=?",
            (identifier, attempt),
        ).fetchone()[0]
        return {
            "job": identifier,
            "attempt": attempt,
            "local_id": local_id,
            "status": "acknowledged" if acknowledged else hold,
            "acknowledged": acknowledged,
        }

    def _complete(
        self,
        identifier: str,
        attempt: int,
        local_id: str,
        body: str,
        rejected_claim: str | None,
        *,
        held: bool = False,
    ) -> bool:
        try:
            response = self.transport(identifier + "/result", json.loads(body))
        except AuthorCapacityError as error:
            self._hold(
                identifier,
                attempt,
                local_id,
                rejected_claim,
                error.reason,
                notify=not held,
            )
            return False
        except HTTPError as error:
            try:
                unavailable = scope_unavailable(error)
            finally:
                error.close()
            if not unavailable:
                raise
            self._hold(
                identifier,
                attempt,
                local_id,
                rejected_claim,
                "scope_unavailable",
                notify=not held,
            )
            return False
        if not acknowledges_completion(response, identifier, attempt) or (
            rejected_claim is not None and response.get("attempt") != attempt
        ):
            raise ValueError("Invalid author completion acknowledgement")
        with self.db:
            self.db.execute(
                "UPDATE studio_jobs SET acknowledged=1,completion_hold=NULL WHERE job=? AND attempt=?",
                (identifier, attempt),
            )
            if rejected_claim is not None:
                self.db.execute(
                    "UPDATE studio_claim SET id=NULL WHERE singleton=1 AND id=?",
                    (rejected_claim,),
                )
        return True

    def _hold(
        self,
        identifier: str,
        attempt: int,
        local_id: str,
        rejected_claim: str | None,
        reason: str,
        *,
        notify: bool = True,
    ) -> None:
        with self.db:
            self.db.execute(
                "UPDATE studio_jobs SET completion_hold=? WHERE job=? AND attempt=?",
                (reason, identifier, attempt),
            )
            if rejected_claim is not None:
                # Retain rejection evidence and release only this claim's slot.
                self.db.execute(
                    "UPDATE studio_claim SET id=NULL WHERE singleton=1 AND id=?",
                    (rejected_claim,),
                )
        if self.on_hold is not None and notify:
            self.on_hold(
                {
                    "job": identifier,
                    "attempt": attempt,
                    "local_id": local_id,
                    "status": reason,
                    "acknowledged": False,
                }
            )

    def _invalid_completion(self, local_id: str, attempt: int, body: str) -> str | None:
        """Classify saved content locally, without inferring acceptance from HTTP errors."""
        try:
            envelope = json.loads(body)
        except ValueError:
            return "invalid_completion"
        if (
            not isinstance(envelope, dict)
            or type(envelope.get("attempt")) is not int
            or envelope["attempt"] != attempt
            or not 0 <= attempt <= 2
        ):
            return "invalid_completion"
        if set(envelope) == {"attempt", "failure", "held"}:
            if (
                not bounded_text(envelope["failure"], 2000)
                or type(envelope["held"]) is not bool
            ):
                return "invalid_completion"
        elif set(envelope) == {"attempt", "result"}:
            row = self.db.execute(
                "SELECT input FROM jobs WHERE id=?", (local_id,)
            ).fetchone()
            if row is None:
                return "invalid_result"
            try:
                validate_result(json.loads(row[0]), envelope["result"])
            except ValueError:
                return "invalid_result"
        else:
            return "invalid_completion"
        return None

    def poll(self) -> None:
        for identifier, attempt, local_id, body, rejected_claim in self.db.execute(
            "SELECT job,attempt,local_id,body,rejected_claim FROM studio_jobs "
            "WHERE acknowledged=0 AND completion_hold IS NULL ORDER BY rowid"
        ).fetchall():
            if body is None:
                result = self.journal.result(local_id)
                if result["status"] in ("pending", "running"):
                    return
                if result["status"] == "complete":
                    envelope = {"attempt": attempt, "result": result["result"]}
                else:
                    error = result.get("result") or {}
                    detail = str(
                        error.get(
                            "detail", "Interrupted model invocation; outcome unknown"
                        )
                    )
                    detail = (
                        detail.encode("utf-8", errors="replace")[:1800].decode(
                            "utf-8", errors="ignore"
                        )
                        or "Model invocation failed"
                    )
                    envelope = {
                        "attempt": attempt,
                        "failure": detail,
                        "held": result["status"] == "held",
                    }
                body = canonical(envelope)
                with self.db:
                    self.db.execute(
                        "UPDATE studio_jobs SET body=? WHERE job=? AND attempt=?",
                        (body, identifier, attempt),
                    )
            reason = self._invalid_completion(local_id, attempt, body)
            if reason:
                # Older complete outputs may predate canonical validation. Keep
                # their exact output/body and uncertain acknowledgement intact;
                # only explicit delivery retry may send them again.
                self._hold(identifier, attempt, local_id, rejected_claim, reason)
                continue
            self._complete(identifier, attempt, local_id, body, rejected_claim)
        row = self.db.execute(
            "SELECT id FROM studio_claim WHERE singleton=1"
        ).fetchone()
        claim = row[0] or str(uuid.uuid4())
        if row[0] is None:
            with self.db:
                self.db.execute(
                    "UPDATE studio_claim SET id=? WHERE singleton=1", (claim,)
                )
        response = self.transport("claim", {"id": claim})
        if not isinstance(response, dict):
            raise ValueError("Invalid author claim response")
        job = response.get("job")
        if job is not None:
            if (
                not isinstance(job, dict)
                or not isinstance(job.get("id"), str)
                or type(job.get("attempt")) is not int
                or not isinstance(job.get("request"), dict)
            ):
                raise ValueError("Invalid author job")
            local_id = "studio:" + job["id"] + ":" + str(job["attempt"])
            # Local input is durable before the claim is cleared. Retrying a lost
            # claim response obtains the same authority job and same model identity.
            try:
                self.journal.submit(local_id, job["request"])
            except ValueError:
                # Rejected input cannot run locally. Stage its exact terminal
                # report and retain the claim through an unknown authority ack.
                body = canonical(
                    {
                        "attempt": job["attempt"],
                        "failure": "Claimed author input rejected by local journal",
                        "held": True,
                    }
                )
                with self.db:
                    self.db.execute(
                        "INSERT OR IGNORE INTO studio_jobs(job,attempt,local_id,body,rejected_claim) VALUES(?,?,?,?,?)",
                        (job["id"], job["attempt"], local_id, body, claim),
                    )
                return
            with self.db:
                self.db.execute(
                    "INSERT OR IGNORE INTO studio_jobs(job,attempt,local_id) VALUES(?,?,?)",
                    (job["id"], job["attempt"], local_id),
                )
                self.db.execute("UPDATE studio_claim SET id=NULL WHERE singleton=1")
        else:
            with self.db:
                self.db.execute("UPDATE studio_claim SET id=NULL WHERE singleton=1")


def scope_unavailable(error: HTTPError) -> bool:
    """Only the bounded authoring API response establishes lost completion scope."""
    if (
        error.code != 404
        or error.headers is None
        or error.headers.get_content_type() != "application/json"
    ):
        return False
    try:
        body = error.read(4097)
        # Preserve object pairs so duplicate keys cannot masquerade as this
        # specific application response. Proxy/generic 404s remain retryable.
        return len(body) <= 4096 and json.loads(
            body.decode("utf-8"), object_pairs_hook=list
        ) == [("error", "not_found")]
    except (OSError, ValueError):
        return False


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args: object, **_kwargs: object) -> None:
        return None


def from_config(journal: Journal, path: Path) -> StudioAgent:
    config = json.loads(path.read_text())
    base = config["url"].rstrip("/")
    key = Path(config["key_file"]).read_text().strip()
    opener = build_opener(NoRedirect())

    def transport(operation: str, body: JsonObject) -> JsonObject:
        request = Request(
            base + "/" + operation,
            data=canonical(body).encode(),
            headers={
                "x-api-key": key,
                "accept": "application/json",
                "content-type": "application/json",
            },
        )
        with opener.open(request, timeout=8) as response:
            value = response.read(2_097_153)
            if len(value) > 2_097_152:
                raise ValueError("Author response exceeds capacity")
            return json.loads(value)

    return StudioAgent(journal, transport)
