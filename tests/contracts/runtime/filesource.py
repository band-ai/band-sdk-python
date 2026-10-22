from __future__ import annotations

import hashlib
import shutil
from typing import Any

from tests.paths import CONTRACT_RUNTIME_TESTS, CONTRACTS_ROOT

ROOT = CONTRACTS_ROOT


REFERENCE = "references/workflows/normalize.band"


def engine_command(*, pinned: bool = True) -> Any:
    lab = ROOT / ("engines/band-v1/contract-lab" if pinned else "contract-lab")
    command = [shutil.which("elixir"), "--erl", "+S 2:2"]
    for ebin in sorted((lab / "_build/dev/lib").glob("*/ebin")):
        command += ["-pa", str(ebin)]
    return command + [str(lab / "examples/engine_endpoint.exs")]


def normalization_source(format: str = "standard") -> dict[str, Any]:
    content = (
        CONTRACT_RUNTIME_TESTS / "fixtures/band/label-normalization.band.txt"
    ).read_text()
    if format == "frontmatter":
        return {
            "profile": "band-contract/file-source/1",
            "format": format,
            "content": content,
            "sha256": hashlib.sha256(content.encode()).hexdigest(),
        }
    # This fixed test fixture has exactly these two frontmatter delimiters.
    body = content.split("---\n", 2)[2]
    entry = (
        "---\nname: standard-normalization\ndescription: >-\n"
        "  Normalize labels from a canonical standard package.\n"
        'metadata: {band-engine: "0.2.0-lab.1", band-roles: "requester,normalizer",\n'
        f'  band-contract: "{REFERENCE}",\n'
        f'  band-contract-sha256: "{hashlib.sha256(body.encode()).hexdigest()}"}}\n'
        "---\nPackage guidance with exact UTF-8 bytes: café.\n"
    )
    return {
        "profile": "band-contract/file-source/1",
        "format": "standard",
        "content": entry,
        "sha256": hashlib.sha256(entry.encode()).hexdigest(),
        "body": body,
        "package_name": "standard-normalization",
    }


def package(root: Any, source: Any) -> Any:
    """Materialize this known fixture for an independent existing standard load."""
    directory = root / source["package_name"]
    body = directory / REFERENCE
    body.parent.mkdir(parents=True)
    (directory / "SKILL.md").write_text(source["content"])
    body.write_text(source["body"])
    return directory / "SKILL.md"
