# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2.12,<3"]
# ///
"""Print the changelog report's JSON schema as one line for ``--json-schema``.

claude-code-action splits ``claude_args`` with shell-quote, which expands ``$``
and rejects ``'``, so references are inlined and the schema contains neither.
"""

from __future__ import annotations

import json
import sys

from pydantic import JsonValue
from releasedata import ChangelogReport

DEFINITION_PREFIX = "#/$defs/"


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


def cli_schema() -> str:
    schema = ChangelogReport.model_json_schema()
    definitions = schema.pop("$defs", {})
    return json.dumps(inline(schema, definitions), separators=(",", ":"))


if __name__ == "__main__":
    sys.stdout.write(cli_schema() + "\n")
