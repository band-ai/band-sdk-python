#!/usr/bin/env python3
"""Discover runnable examples and the configuration they actually consume."""

from __future__ import annotations

import argparse
import ast
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Example:
    path: str
    family: str
    summary: str
    config_keys: tuple[str, ...]
    environment: tuple[str, ...]
    dependencies: tuple[str, ...]
    documented_commands: tuple[str, ...]


def literal_string(node: ast.AST) -> str | None:
    return (
        node.value
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        else None
    )


def call_name(node: ast.AST) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def metadata_dependencies(source: str) -> tuple[str, ...]:
    match = re.search(
        r"(?ms)^# /// script\s*$.*?^# dependencies\s*=\s*(\[[^\n]*\])\s*$.*?^# ///\s*$",
        source,
    )
    if match is None:
        return ()
    try:
        value = ast.literal_eval(match.group(1))
    except (SyntaxError, ValueError):
        return ()
    return tuple(str(item) for item in value) if isinstance(value, list) else ()


def inspect_example(path: Path, root: Path) -> Example | None:
    source = path.read_text(encoding="utf-8")
    dependencies = metadata_dependencies(source)
    if not dependencies:
        return None
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return None
    has_main = any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "main"
        for node in tree.body
    )
    if not has_main:
        return None

    config_keys: set[str] = set()
    environment: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = call_name(node.func)
            if name.endswith(("from_config", "load_agent_config")) and node.args:
                value = literal_string(node.args[0])
                if value:
                    config_keys.add(value)
            if name in {"os.getenv", "os.environ.get"} and node.args:
                value = literal_string(node.args[0])
                if value:
                    environment.add(value)
        if isinstance(node, ast.Subscript) and call_name(node.value) == "os.environ":
            value = literal_string(node.slice)
            if value:
                environment.add(value)

    relative = path.relative_to(root)
    docstring = ast.get_docstring(tree) or ""
    summary = next(
        (line.strip() for line in docstring.splitlines() if line.strip()), ""
    )
    commands = tuple(
        dict.fromkeys(
            re.findall(
                r"(?m)^\s*((?:uv|python) run [^\n]+|uv run [^\n]+)\s*$", docstring
            )
        )
    )
    return Example(
        path=relative.as_posix(),
        family=relative.parts[1]
        if len(relative.parts) > 1
        else relative.parent.as_posix(),
        summary=summary,
        config_keys=tuple(sorted(config_keys)),
        environment=tuple(sorted(environment)),
        dependencies=dependencies,
        documented_commands=commands,
    )


def discover(repo: Path, examples_root: str, family: str | None) -> list[Example]:
    root = repo.resolve()
    search_root = root / examples_root
    paths = sorted(search_root.rglob("*.py"))
    found = [
        item for path in paths if (item := inspect_example(path, root)) is not None
    ]
    return [item for item in found if family is None or item.family == family]


def parser() -> argparse.ArgumentParser:
    default_repo = Path(__file__).resolve().parents[4]
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--repo", type=Path, default=default_repo)
    result.add_argument("--root", default="examples")
    result.add_argument("--family", help="Restrict to one examples/<family> directory")
    result.add_argument("--json", action="store_true", dest="as_json")
    return result


def main() -> None:
    args = parser().parse_args()
    examples = discover(args.repo, args.root, args.family)
    if args.as_json:
        payload: list[dict[str, Any]] = [asdict(example) for example in examples]
        print(json.dumps(payload, indent=2))
        return
    for example in examples:
        keys = ",".join(example.config_keys) or "-"
        print(f"{example.path}\tconfig={keys}\t{example.summary}")


if __name__ == "__main__":
    main()
