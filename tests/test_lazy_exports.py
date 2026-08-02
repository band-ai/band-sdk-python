"""Guard the lazy export surface of packages built with ``lazy_exports``.

Each such package states its exports twice: once in the ``TYPE_CHECKING`` block
that static analysis reads, once in the ``lazy_exports`` call the interpreter
reads. Only the second one resolves at runtime, so a name added to one and not
the other fails silently — a type checker that sees a symbol the import machinery
cannot produce, or a working import no tool knows about.

The comparison is done on the parsed source, so it costs no optional extra: a
missing framework would otherwise make the runtime map unreadable.
"""

from __future__ import annotations

import ast

import pytest

from tests.paths import SRC_ROOT

_LAZY_PACKAGES = ("adapters", "converters", "testing")


def _package_source(package: str) -> ast.Module:
    return ast.parse((SRC_ROOT / package / "__init__.py").read_text())


def _is_type_checking(test: ast.expr) -> bool:
    match test:
        case ast.Name(id="TYPE_CHECKING") | ast.Attribute(attr="TYPE_CHECKING"):
            return True
        case _:
            return False


def _type_checking_exports(tree: ast.Module) -> set[tuple[str, str]]:
    """Collect (submodule, name) pairs re-exported under ``if TYPE_CHECKING``."""
    return {
        (node.module.rsplit(".", 1)[-1], alias.name)
        for branch in ast.walk(tree)
        if isinstance(branch, ast.If) and _is_type_checking(branch.test)
        for node in ast.walk(branch)
        if isinstance(node, ast.ImportFrom) and node.module
        for alias in node.names
    }


def _lazy_exports_call(tree: ast.Module) -> set[tuple[str, str]]:
    """Collect (submodule, name) pairs declared in the ``lazy_exports`` call."""
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "lazy_exports"
    ]
    assert len(calls) == 1, "expected exactly one lazy_exports call per package"
    return {
        (keyword.arg, element.value)
        for keyword in calls[0].keywords
        if keyword.arg and isinstance(keyword.value, ast.List)
        for element in keyword.value.elts
        if isinstance(element, ast.Constant)
    }


@pytest.mark.parametrize("package", _LAZY_PACKAGES)
def test_type_checking_block_matches_lazy_exports(package: str) -> None:
    tree = _package_source(package)
    declared = _lazy_exports_call(tree)
    annotated = _type_checking_exports(tree)

    assert declared == annotated, (
        f"band.{package} exports drifted between its TYPE_CHECKING block and its "
        f"lazy_exports call: only lazy={sorted(declared - annotated)}, "
        f"only typed={sorted(annotated - declared)}"
    )


def test_first_access_binds_the_name_into_the_package() -> None:
    """PEP 562 consults ``__getattr__`` only for names the module does not have.

    A resolved export that never lands in the namespace re-enters importlib on
    every single read.
    """
    import band.testing

    vars(band.testing).pop("FakeAgentTools", None)

    resolved = band.testing.FakeAgentTools

    assert vars(band.testing)["FakeAgentTools"] is resolved


def test_unknown_attribute_raises_attribute_error() -> None:
    import band.adapters

    with pytest.raises(AttributeError, match="NoSuchAdapter"):
        band.adapters.NoSuchAdapter
