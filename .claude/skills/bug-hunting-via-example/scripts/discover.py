#!/usr/bin/env python3
"""Discover runnable examples and the configuration they actually consume."""

from __future__ import annotations

import argparse
import ast
import json
import re
import tomllib
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


@dataclass(frozen=True)
class SettingField:
    environment: str
    default: str | None


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


def script_metadata(source: str) -> dict[str, Any] | None:
    lines = source.splitlines()
    try:
        start = next(
            index for index, line in enumerate(lines) if line.strip() == "# /// script"
        )
        end = next(
            index
            for index, line in enumerate(lines[start + 1 :], start + 1)
            if line.strip() == "# ///"
        )
    except StopIteration:
        return None
    content: list[str] = []
    for line in lines[start + 1 : end]:
        if not line.startswith("#"):
            return None
        value = line[1:]
        content.append(value[1:] if value.startswith(" ") else value)
    try:
        return tomllib.loads("\n".join(content))
    except tomllib.TOMLDecodeError:
        return None


def metadata_dependencies(source: str) -> tuple[str, ...] | None:
    metadata = script_metadata(source)
    if metadata is None:
        return None
    dependencies = metadata.get("dependencies", [])
    if not isinstance(dependencies, list) or not all(
        isinstance(item, str) for item in dependencies
    ):
        return None
    return tuple(dependencies)


def settings_config_prefix(node: ast.ClassDef) -> str:
    for statement in node.body:
        value: ast.AST | None = None
        if isinstance(statement, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "model_config"
            for target in statement.targets
        ):
            value = statement.value
        elif (
            isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
            and statement.target.id == "model_config"
        ):
            value = statement.value
        if isinstance(value, ast.Call) and call_name(value.func).endswith(
            "SettingsConfigDict"
        ):
            for keyword in value.keywords:
                if keyword.arg == "env_prefix":
                    return literal_string(keyword.value) or ""
    return ""


def settings_classes(tree: ast.Module) -> dict[str, dict[str, SettingField]]:
    classes: dict[str, dict[str, SettingField]] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or not any(
            call_name(base).endswith("BaseSettings") for base in node.bases
        ):
            continue
        prefix = settings_config_prefix(node)
        fields: dict[str, SettingField] = {}
        for statement in node.body:
            if not isinstance(statement, ast.AnnAssign) or not isinstance(
                statement.target, ast.Name
            ):
                continue
            name = statement.target.id
            if name.startswith("_") or name == "model_config":
                continue
            fields[name] = SettingField(
                environment=f"{prefix}{name}".upper(),
                default=literal_string(statement.value)
                if statement.value is not None
                else None,
            )
        classes[node.name] = fields
    return classes


def imported_settings_classes(
    tree: ast.Module, path: Path
) -> dict[str, dict[str, SettingField]]:
    imported: dict[str, dict[str, SettingField]] = {}
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        base = path.parent
        for _ in range(max(node.level - 1, 0)):
            base = base.parent
        module_path = (base / Path(*node.module.split("."))).with_suffix(".py")
        if not module_path.is_file():
            continue
        try:
            module_tree = ast.parse(
                module_path.read_text(encoding="utf-8"), filename=str(module_path)
            )
        except SyntaxError:
            continue
        available = settings_classes(module_tree)
        for alias in node.names:
            if alias.name in available:
                imported[alias.asname or alias.name] = available[alias.name]
    return imported


def settings_instances(
    tree: ast.Module, classes: dict[str, dict[str, SettingField]]
) -> dict[str, dict[str, SettingField]]:
    instances: dict[str, dict[str, SettingField]] = {}
    for node in ast.walk(tree):
        targets: list[ast.AST] = []
        value: ast.AST | None = None
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value = node.value
        if not isinstance(value, ast.Call):
            continue
        fields = classes.get(call_name(value.func))
        if fields is None:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                instances[target.id] = fields
    return instances


def settings_config_key(
    node: ast.AST, instances: dict[str, dict[str, SettingField]]
) -> str | None:
    if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
        return None
    field = instances.get(node.value.id, {}).get(node.attr)
    if field is None:
        return None
    return field.default or f"${{{field.environment}}}"


def has_main(tree: ast.Module) -> bool:
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "main"
        for node in tree.body
    )


def inspect_configuration(tree: ast.Module, path: Path) -> tuple[set[str], set[str]]:
    classes = settings_classes(tree)
    classes.update(imported_settings_classes(tree, path))
    instances = settings_instances(tree, classes)
    config_keys: set[str] = set()
    environment = {
        field.environment for fields in instances.values() for field in fields.values()
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = call_name(node.func)
            if name.endswith(("from_config", "load_agent_config")) and node.args:
                value = literal_string(node.args[0]) or settings_config_key(
                    node.args[0], instances
                )
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
    return config_keys, environment


def docstring_summary(docstring: str) -> str:
    return next((line.strip() for line in docstring.splitlines() if line.strip()), "")


def documented_commands(docstring: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            re.findall(
                r"(?m)^\s*((?:uv|python) run [^\n]+|uv run [^\n]+)\s*$",
                docstring,
            )
        )
    )


def inspect_example(path: Path, root: Path) -> Example | None:
    source = path.read_text(encoding="utf-8")
    dependencies = metadata_dependencies(source)
    if dependencies is None:
        return None
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return None
    if not has_main(tree):
        return None
    config_keys, environment = inspect_configuration(tree, path)
    relative = path.relative_to(root)
    docstring = ast.get_docstring(tree) or ""
    return Example(
        path=relative.as_posix(),
        family=relative.parts[1]
        if len(relative.parts) > 1
        else relative.parent.as_posix(),
        summary=docstring_summary(docstring),
        config_keys=tuple(sorted(config_keys)),
        environment=tuple(sorted(environment)),
        dependencies=dependencies,
        documented_commands=documented_commands(docstring),
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
