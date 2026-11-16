"""Shipped code runs a custom tool for a model only through the shared runner."""

from __future__ import annotations

import ast
from pathlib import Path

from tests.paths import REPO_ROOT, SHIPPED_SOURCE_ROOTS

RAW_EXECUTORS = frozenset({"execute_custom_tool", "invoke_validated_custom_tool"})

# The runner wraps the raw executors. CrewAI Flow tools are called from the
# user's own Flow code, so their exceptions belong to that code, not a model.
ALLOWED_RAW_CALLERS = frozenset(
    REPO_ROOT / path
    for path in (
        "src/band/runtime/custom_tools.py",
        "src/band/adapters/crewai_flow.py",
    )
)


def raw_executor_calls(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        f"{path.relative_to(REPO_ROOT)}:{node.lineno}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and called_name(node.func) in RAW_EXECUTORS
    ]


def called_name(func: ast.expr) -> str | None:
    match func:
        case ast.Name(id=name) | ast.Attribute(attr=name):
            return name
        case _:
            return None


class TestCustomToolRunnerBoundary:
    def test_raw_executors_are_called_only_by_the_runner(self) -> None:
        offenders = [
            call
            for root in SHIPPED_SOURCE_ROOTS
            for path in sorted(root.rglob("*.py"))
            if path not in ALLOWED_RAW_CALLERS
            for call in raw_executor_calls(path)
        ]

        assert offenders == [], (
            "Run custom tools through run_custom_tool / run_validated_custom_tool "
            "so a failure reaches the model as a failed result."
        )
