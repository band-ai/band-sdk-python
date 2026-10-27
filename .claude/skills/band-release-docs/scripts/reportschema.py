# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2.12,<3"]
# ///
"""Print a model report's JSON schema as one line for ``--json-schema``.

claude-code-action splits ``claude_args`` with shell-quote, which expands ``$``
and rejects ``'``, so references are inlined and the schema contains neither.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from enum import StrEnum

from docsreport import DocsReport
from pydantic import BaseModel, JsonValue
from releasedata import ChangelogReport, HoldbacksReport

DEFINITION_PREFIX = "#/$defs/"


class ReportKind(StrEnum):
    """The CI tasks that return a structured report."""

    CHANGELOG = "changelog"
    HOLDBACKS = "holdbacks"
    DOCS = "docs"


REPORT_MODELS: dict[ReportKind, type[BaseModel]] = {
    ReportKind.CHANGELOG: ChangelogReport,
    ReportKind.HOLDBACKS: HoldbacksReport,
    ReportKind.DOCS: DocsReport,
}


def inline(node: JsonValue, definitions: dict[str, JsonValue]) -> JsonValue:
    """Replace every ``$ref`` with the definition it names."""
    match node:
        case {"$ref": str(reference)}:
            return inline(
                definitions[reference.removeprefix(DEFINITION_PREFIX)], definitions
            )
        case dict():
            return {key: inline(value, definitions) for key, value in node.items()}
        case list():
            return [inline(value, definitions) for value in node]
        case _:
            return node


def cli_schema(kind: ReportKind = ReportKind.CHANGELOG) -> str:
    schema = REPORT_MODELS[kind].model_json_schema()
    definitions = schema.pop("$defs", {})
    return json.dumps(inline(schema, definitions), separators=(",", ":"))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--kind",
        type=ReportKind,
        choices=list(ReportKind),
        default=ReportKind.CHANGELOG,
    )
    args = parser.parse_args(argv)
    sys.stdout.write(cli_schema(args.kind) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
