"""Describe native capabilities without hiding failures on supported hosts."""

from __future__ import annotations

import ctypes
import os
import shutil
import sys
from pathlib import Path

from remote_agent.capabilities import (
    UnsupportedHostError,
    require_linux,
    require_runtime,
)
from tests.paths import CONTRACT_BAND_ENGINE_ROOT, CONTRACT_ENGINE_ROOT

CAPABILITIES = frozenset(
    {
        "contract_posix",
        "contract_linux",
        "contract_runtime",
        "contract_system_runtime",
        "contract_isolation",
        "contract_elixir",
        "contract_engine",
        "contract_node",
    }
)


def missing(capability: str, *, engine: str = "current") -> str | None:
    """Return the unavailable prerequisite; executable failures remain test failures."""
    match capability:
        case "contract_posix":
            if os.name != "posix":
                return "This test requires a POSIX process or filesystem boundary."
        case "contract_linux":
            try:
                require_linux()
            except UnsupportedHostError as error:
                return str(error)
        case "contract_runtime":
            try:
                require_runtime()
            except UnsupportedHostError as error:
                return str(error)
        case "contract_system_runtime":
            reason = missing("contract_runtime")
            if reason:
                return reason
            python = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
            if not python.is_relative_to("/usr"):
                return (
                    "Isolated callbacks require the base Python interpreter under /usr."
                )
        case "contract_isolation":
            reason = missing("contract_system_runtime")
            if reason:
                return reason
            if shutil.which("bwrap") is None:
                return "Isolated callbacks require bubblewrap."
            try:
                ctypes.CDLL("libseccomp.so.2")
            except OSError:
                return "Isolated callbacks require libseccomp.so.2."
        case "contract_elixir":
            if shutil.which("elixir") is None:
                return "This test requires the Elixir executable."
        case "contract_engine":
            reason = missing("contract_elixir")
            if reason:
                return reason
            if shutil.which("mix") is None:
                return "Compiled contract engine tests require Mix."
            match engine:
                case "current":
                    root = CONTRACT_ENGINE_ROOT
                case "band":
                    root = CONTRACT_BAND_ENGINE_ROOT
                case _:
                    raise ValueError("Unknown contract engine prerequisite: " + engine)
            canonical = (
                root
                / "_build/dev/lib/contract_lab/ebin/Elixir.ContractLab.Canonical.beam"
            )
            if not canonical.is_file():
                return f"Compile the {engine} contract engine in MIX_ENV=dev: {root}"
        case "contract_node":
            if shutil.which("node") is None:
                return "The JavaScript parity check requires Node.js."
        case _:
            raise ValueError("Unknown contract test capability: " + capability)
    return None
