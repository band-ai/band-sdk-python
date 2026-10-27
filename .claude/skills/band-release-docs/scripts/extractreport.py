# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2.12,<3"]
# ///
"""Read the model's structured report from claude-code-action's execution file.

The execution file is the JSON array of SDK messages the action writes; the
report is the ``structured_output`` of its result message.
"""

from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, ValidationError
from releasedata import shape_errors
from reportschema import REPORT_MODELS, ReportKind, add_kind_argument

logger = logging.getLogger(__name__)


def extract_report(
    execution_file: Path, kind: ReportKind = ReportKind.CHANGELOG
) -> BaseModel:
    messages = json.loads(execution_file.read_text(encoding="utf-8"))
    results = [message for message in messages if message.get("type") == "result"]
    if not results:
        raise ValueError("the execution file holds no result message")
    output = results[-1].get("structured_output")
    if not output:
        raise ValueError("the result message carries no structured_output")
    return REPORT_MODELS[kind].model_validate(output)


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("execution_file", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    add_kind_argument(parser)
    args = parser.parse_args(argv)

    try:
        report = extract_report(args.execution_file, args.kind)
    except ValidationError as exc:
        # Without the input: model text may hold a secret.
        logger.error("The report does not match the schema:\n%s", shape_errors(exc))
        return 1
    except ValueError as exc:
        logger.error("No report: %s", exc)
        return 1
    args.out.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    logger.info("Read the %s report", args.kind)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
