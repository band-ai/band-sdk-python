"""Installed service command. Diagnostics never print raw configuration or credentials."""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import threading
from collections.abc import Sequence

from remote_agent import operations
from remote_agent.capabilities import UnsupportedHostError, require_runtime
from remote_agent.output import write_line
from remote_agent.service import ConfigurationError, Service, load_config
from remote_agent.storage import StoreError
from remote_agent.transport import EngineCompileError, EngineProcess


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="contract-service")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("serve", "check-config"):
        commands.add_parser(command).add_argument("config")
    commands.add_parser("inspect").add_argument("source")
    journal = commands.add_parser("journal")
    journal.add_argument("source")
    journal.add_argument("--after", type=int, default=0)
    journal.add_argument("--through", type=int)
    journal.add_argument("--limit", type=int, default=50)
    journal.add_argument("--kind")
    journal.add_argument("--query", default="")
    commands.add_parser("rotate-host-key").add_argument("source")
    for command in ("backup", "restore", "export"):
        operator = commands.add_parser(command)
        operator.add_argument("source")
        operator.add_argument("destination")
    replay = commands.add_parser("replay")
    replay.add_argument("source")
    replay.add_argument("identifier")
    replay.add_argument("destination")
    replay.add_argument("--kind", choices=("program", "model"), default="program")
    args = parser.parse_args(argv)
    runtime = None
    stopping = threading.Event()
    prior = {}
    exit_code = 0

    def stop(*_: object) -> None:
        stopping.set()
        if runtime is not None:
            runtime.request_stop()

    try:
        require_runtime()
        if args.command not in ("serve", "check-config"):
            if args.command == "rotate-host-key":
                result = operations.rotate_host_key(args.source)
            elif args.command == "inspect":
                result = operations.inspect(args.source)
            elif args.command == "journal":
                result = operations.journal(
                    args.source,
                    after=args.after,
                    through=args.through,
                    limit=args.limit,
                    kind=args.kind,
                    query=args.query,
                )
            elif args.command == "replay":
                result = operations.replay(
                    args.source, args.identifier, args.destination, kind=args.kind
                )
            else:
                result = getattr(operations, args.command)(
                    args.source, args.destination
                )
            write_line(json.dumps(result))
            exit_code = (
                3 if args.command == "replay" and not result["replayable"] else 0
            )
        else:
            config = load_config(args.config)
            if args.command == "check-config":
                options = dict(config["engine"])
                options["timeout"] = options.pop("timeout_ms") / 1000
                engine = EngineProcess(**options)
                engine.close()
                write_line(json.dumps({"valid": True, "kind": config["kind"]}))
            else:
                logging.basicConfig(
                    level=logging.INFO, format="%(message)s", stream=sys.stderr
                )
                for signum in (signal.SIGTERM, signal.SIGINT):
                    prior[signum] = signal.signal(signum, stop)
                runtime = Service(config, stopping=stopping)
                if not stopping.is_set():
                    write_line(
                        json.dumps(
                            {
                                "event": "service_ready",
                                "kind": config["kind"],
                                "url": runtime.http.url,
                            }
                        ),
                        flush=True,
                    )
                runtime.run()
    except UnsupportedHostError as error:
        write_line(
            json.dumps({"event": "unsupported_runtime", "message": str(error)}),
            file=sys.stderr,
        )
        exit_code = 2
    except EngineCompileError as error:
        write_line(
            json.dumps({"event": "configuration_error", "error": error.diagnostic}),
            file=sys.stderr,
        )
        exit_code = 2
    except ConfigurationError as error:
        write_line(
            json.dumps({"event": "configuration_error", "message": str(error)}),
            file=sys.stderr,
        )
        exit_code = 2
    except StoreError:
        write_line(
            json.dumps({"event": "operation_refused", "category": "StoreError"}),
            file=sys.stderr,
        )
        exit_code = 2
    except Exception as error:  # noqa: BLE001 -- The CLI reports only an arbitrary service failure's category.
        write_line(
            json.dumps({"event": "service_failed", "category": type(error).__name__}),
            file=sys.stderr,
        )
        exit_code = 1
    finally:
        if runtime is not None:
            try:
                runtime.close()
            except Exception as error:  # noqa: BLE001 -- Cleanup failures must preserve an active interruption and never disclose private details.
                exit_code = 1
                write_line(
                    json.dumps(
                        {"event": "shutdown_failed", "category": type(error).__name__}
                    ),
                    file=sys.stderr,
                )
        for signum, handler in prior.items():
            signal.signal(signum, handler)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
