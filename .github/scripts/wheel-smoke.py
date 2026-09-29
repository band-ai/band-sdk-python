#!/usr/bin/env python
"""Smoke-test the band wheel in an isolated, extras-free install.

Run with that install's interpreter: a script's own directory, not the
checkout, heads ``sys.path``, so ``band`` and ``band_sdk_core`` resolve to the
installed wheels. It proves the pinned ``band_sdk_core`` wheel serves every
name band refers to, and that band's own wrappers run on it.
"""

from __future__ import annotations

import ast
import logging
from collections.abc import Iterator
from pathlib import Path

import band_sdk_core

import band
from band import Agent, AgentRuntime, BandLink  # noqa: F401 -- import-time smoke
from band.config import load_agent_config  # noqa: F401 -- import-time smoke
from band.core.protocols import to_failure_event
from band.runtime.decisions import DecisionRegistry
from band.runtime.retry_tracker import MessageRetryTracker

CORE_MODULE = "band_sdk_core"

logger = logging.getLogger(__name__)


def core_names_in(tree: ast.Module) -> Iterator[str]:
    """``band_sdk_core`` attributes one module imports or reads."""
    module_aliases = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.name == CORE_MODULE
    }
    for node in ast.walk(tree):
        match node:
            case ast.ImportFrom(module=module, names=names) if module == CORE_MODULE:
                yield from (alias.name for alias in names)
            case ast.Attribute(value=ast.Name(id=name), attr=attr) if (
                name in module_aliases
            ):
                yield attr


def core_names_band_uses() -> set[str]:
    package = Path(band.__file__).parent
    return {
        name
        for source in package.rglob("*.py")
        for name in core_names_in(ast.parse(source.read_text(encoding="utf-8")))
    }


def assert_core_serves_band() -> None:
    used = core_names_band_uses()
    missing = sorted(name for name in used if not hasattr(band_sdk_core, name))
    assert used, "found no band_sdk_core references in the installed band package"
    assert not missing, f"band_sdk_core wheel lacks names band uses: {missing}"


def assert_band_wrappers_run() -> None:
    decisions: DecisionRegistry[str] = DecisionRegistry(max_pending=1)
    registration = decisions.register_keyed("ask", key="ask-1")
    assert registration is not None
    assert decisions.try_claim("ask-1") is registration.entry
    assert decisions.cancel_all() == []

    retries = MessageRetryTracker(max_retries=1)
    assert retries.record_attempt("msg-1") == (1, False)
    retries.mark_success("msg-1")
    assert not retries.is_permanently_failed("msg-1")

    content, metadata = to_failure_event(band_sdk_core.AgentFailure("smoke", ""))
    assert content == "smoke failed without an error message."
    assert metadata["failure"]["provider"] == "smoke"


def main() -> None:
    assert_core_serves_band()
    assert_band_wrappers_run()
    logger.info("Isolated wheel smoke passed")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
