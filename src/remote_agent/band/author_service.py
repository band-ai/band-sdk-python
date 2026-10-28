"""Dedicated selected-agent protocol author, with durable claims and completion retries."""

from __future__ import annotations

import argparse
import json
import os
import signal
import threading
from collections.abc import Callable, Sequence
from functools import partial
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener

from remote_agent.band.authoring import (
    Journal,
    author_prompt,
    output_schema,
    process_one,
    protocol_limit,
)
from remote_agent.band.authortransport import AuthorCapacityError, encode_request
from remote_agent.band.configuration import (
    credential,
    model_config,
    object_file,
    origin,
    private_path,
)
from remote_agent.band.modelbridge import invoke
from remote_agent.band.studio import NoRedirect, StudioAgent
from remote_agent.capabilities import exclusive_lock, require_runtime
from remote_agent.output import write_line
from remote_agent.storage import private_directory, private_file
from remote_agent.types import JsonObject, SourcePath


def load_config(path: SourcePath) -> JsonObject:
    config = object_file(path)
    required = {"profile", "state", "platform_url", "key_file", "model_config"}
    if (
        not required <= config.keys()
        or config.keys()
        - (required | {"max_model_calls", "poll_interval_ms", "model_timeout_seconds"})
        or config["profile"] != "band-contract/author-service/1"
        or type(config.get("max_model_calls", 20)) is not int
        or not 1 <= config.get("max_model_calls", 20) <= 100000
        or type(config.get("poll_interval_ms", 1000)) is not int
        or not 100 <= config.get("poll_interval_ms", 1000) <= 60000
        or type(config.get("model_timeout_seconds", 180)) is not int
        or not 1 <= config.get("model_timeout_seconds", 180) <= 900
    ):
        raise ValueError("Invalid author service configuration")
    config["platform_url"] = origin(config["platform_url"])
    for key in ("state", "key_file", "model_config"):
        config[key] = private_path(config[key])
    credential(config["key_file"])
    model_config(config["model_config"])
    return config


def generate(
    root: SourcePath,
    guide: str,
    job: JsonObject,
    config: JsonObject,
    delivered: JsonObject | None = None,
) -> JsonObject:
    """``guide`` is the bundled grammar. A verified delivery replaces both bundled
    assets when it carries a skill and grammar, and sets the protocol limit."""
    timeout = config.get("model_timeout_seconds", 180)
    skill = None
    if delivered is not None and "skill" in delivered:
        guide, skill = delivered["grammar"]["content"], delivered["skill"]["content"]
    limit = protocol_limit(delivered)
    result = invoke(
        root,
        author_prompt(guide, job, skill=skill, max_protocols=limit),
        output_schema(job, max_protocols=limit),
        configuration=config["model_config"],
        timeout=timeout,
        timeout_limit=timeout,
    )
    # Accepted attempts (0..2) have the same encoded size. Reject an unsendable
    # production result before the journal treats it as complete.
    encode_request({"attempt": 2, "result": result})
    return result


def transport(config: JsonObject) -> Callable[[str, JsonObject], JsonObject]:
    opener = build_opener(ProxyHandler({}), NoRedirect())
    base = config["platform_url"] + "/api/v1/agent/contracts/authoring/"

    def send(operation: str, body: JsonObject) -> JsonObject:
        encoded = encode_request(body)
        request = Request(
            base + operation,
            data=encoded,
            headers={
                "x-api-key": credential(config["key_file"]),
                "accept": "application/json",
                "content-type": "application/json",
            },
        )
        with opener.open(request, timeout=8) as response:
            result = response.read(1048577)
        if len(result) > 1048576:
            raise AuthorCapacityError("response")
        return json.loads(result)

    return send


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--list-held",
        action="store_true",
        help="List unresolved completion holds and exit",
    )
    action.add_argument(
        "--retry-held",
        metavar="JOB",
        help="Retry one saved completion once, without invoking the model",
    )
    parser.add_argument(
        "--attempt", type=int, help="Exact held attempt to retry with --retry-held"
    )
    args = parser.parse_args(argv)
    if (args.retry_held is None) != (args.attempt is None) or (
        args.attempt is not None and args.attempt < 0
    ):
        parser.error("--retry-held requires --attempt with a nonnegative integer")
    require_runtime()
    os.umask(0o077)
    config = load_config(args.config)
    root = private_directory(Path(config["state"]))
    stopping = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stopping.set())
    # Two workers must not mark each other's live provider request interrupted.
    with os.fdopen(private_file(root / "service.owner"), "a+b") as owner:
        try:
            exclusive_lock(owner.fileno(), nonblocking=True)
        except BlockingIOError:
            raise ValueError("Author service is already running") from None
        journal = Journal(root, maximum=config.get("max_model_calls", 20))
        try:

            def held_event(held: JsonObject) -> None:
                write_line(
                    json.dumps({"event": "protocol_author_completion_held", **held}),
                    flush=True,
                )

            studio = StudioAgent(journal, transport(config), on_hold=held_event)
            if args.list_held:
                write_line(json.dumps({"held": studio.held_completions()}), flush=True)
                return
            if args.retry_held is not None:
                try:
                    result = studio.retry_held(args.retry_held, args.attempt)
                except (OSError, ValueError) as error:
                    write_line(
                        json.dumps(
                            {
                                "event": "protocol_author_completion_retry",
                                "job": args.retry_held,
                                "attempt": args.attempt,
                                "acknowledged": False,
                                "error": type(error).__name__,
                            }
                        ),
                        flush=True,
                    )
                    raise SystemExit(1) from None
                write_line(
                    json.dumps({"event": "protocol_author_completion_retry", **result}),
                    flush=True,
                )
                if not result["acknowledged"]:
                    raise SystemExit(2)
                return
            journal.recover()
            guide = (Path(__file__).parent / "assets/grammar.md").read_text()
            model = partial(generate, root, guide, config=config)
            write_line(json.dumps({"event": "protocol_author_ready"}), flush=True)
            for held in studio.held_completions():
                held_event(held)
            while not stopping.is_set():
                try:
                    studio.poll()
                except (OSError, ValueError):
                    # Stable claims and staged results survive bounded transport
                    # failures. No model invocation is repeated by this retry.
                    pass
                if not stopping.is_set():
                    process_one(journal, model)
                stopping.wait(config.get("poll_interval_ms", 1000) / 1000)
        finally:
            journal.close()


if __name__ == "__main__":
    main()
