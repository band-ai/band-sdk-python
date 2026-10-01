"""Every adapter is built the same way (see ``band.core.adapterconfig``).

Scans the source instead of importing it, so it runs in every CI lane without
each framework's optional extra installed.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.paths import REPO_ROOT, SRC_ROOT

CONFIG_BASES = frozenset({"BaseAdapterConfig", "EnvAdapterConfig"})
# The base every adapter extends; its constructor is the shared features entry
# point, not an adapter's public surface.
ADAPTER_BASE = "SimpleAdapter"


@dataclass(frozen=True)
class SourceClass:
    path: Path
    node: ast.ClassDef

    @property
    def label(self) -> str:
        return f"{self.path.relative_to(REPO_ROOT)}:{self.node.name}"

    @property
    def base_names(self) -> set[str]:
        return {_bare_name(base) for base in self.node.bases}

    @property
    def init(self) -> ast.FunctionDef | None:
        return next(
            (
                node
                for node in self.node.body
                if isinstance(node, ast.FunctionDef) and node.name == "__init__"
            ),
            None,
        )


def _bare_name(node: ast.expr) -> str:
    """``SimpleAdapter[X]`` -> ``SimpleAdapter``; ``a.B`` -> ``B``."""
    match node:
        case ast.Subscript(value=value):
            return _bare_name(value)
        case ast.Attribute(attr=attr):
            return attr
        case ast.Name(id=name):
            return name
    return ast.unparse(node)


def _source_classes() -> list[SourceClass]:
    return [
        SourceClass(path, node)
        for path in sorted(SRC_ROOT.rglob("*.py"))
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.ClassDef)
    ]


SOURCE_CLASSES = _source_classes()
ADAPTER_NAMES = {c.node.name for c in SOURCE_CLASSES if c.node.name.endswith("Adapter")}
ADAPTERS = [
    c
    for c in SOURCE_CLASSES
    if c.node.name in ADAPTER_NAMES - {ADAPTER_BASE}
    and c.base_names & (ADAPTER_NAMES | {ADAPTER_BASE})
]
ADAPTER_CONFIGS = [
    c
    for c in SOURCE_CLASSES
    if c.node.name.endswith("AdapterConfig") and c.node.name not in CONFIG_BASES
]


def _constructor_shape_errors(adapter: SourceClass) -> list[str]:
    init = adapter.init
    if init is None:
        return []
    args = init.args
    positional = [arg.arg for arg in [*args.posonlyargs, *args.args][1:]]
    errors = []
    if positional != ["config"]:
        errors.append(f"positional parameters are {positional}, expected ['config']")
    if args.vararg is not None:
        errors.append(f"takes *{args.vararg.arg}")
    if args.kwarg is None or args.kwarg.arg != "features":
        errors.append("does not end with **features: Unpack[FeatureKwargs]")
    config = next((arg for arg in args.args if arg.arg == "config"), None)
    expected = f"{adapter.node.name}Config"
    if config is not None and (
        config.annotation is None or expected not in ast.unparse(config.annotation)
    ):
        errors.append(f"config is not annotated as {expected}")
    return errors


@pytest.mark.parametrize("adapter", ADAPTERS, ids=lambda c: c.label)
def test_adapter_takes_its_config_then_keyword_only_arguments(
    adapter: SourceClass,
) -> None:
    assert _constructor_shape_errors(adapter) == []


@pytest.mark.parametrize("config", ADAPTER_CONFIGS, ids=lambda c: c.label)
def test_adapter_config_extends_the_shared_base(config: SourceClass) -> None:
    config_names = {c.node.name for c in ADAPTER_CONFIGS}
    assert config.base_names & (CONFIG_BASES | config_names), (
        f"{config.label} must subclass BaseAdapterConfig or EnvAdapterConfig"
    )


def test_every_adapter_has_its_config_class() -> None:
    config_names = {c.node.name for c in ADAPTER_CONFIGS}
    missing = [
        adapter.label
        for adapter in ADAPTERS
        if f"{adapter.node.name}Config" not in config_names
    ]
    assert missing == []
