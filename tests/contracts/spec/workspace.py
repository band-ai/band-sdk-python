"""Run source tooling against disposable SDK-shaped contract bundles."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from tests.paths import CONTRACTS_ROOT, REPO_ROOT


class ToolEnvironment(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore", case_sensitive=False, env_ignore_empty=True
    )

    PATH: str = os.defpath
    HOME: str = str(Path.home())
    LANG: str = "C.UTF-8"
    MIX_ENV: str = "dev"
    ERL_FLAGS: str = "+S 2:2 +A 1"
    SYSTEMROOT: str | None = None


def link_or_copy(source: Path, destination: Path) -> None:
    """Keep temporary bundles usable without Windows symlink privileges."""
    try:
        destination.symlink_to(source, target_is_directory=source.is_dir())
    except OSError:
        if os.name != "nt":
            raise
        if source.is_dir():
            shutil.copytree(
                source, destination, ignore=shutil.ignore_patterns("__pycache__")
            )
        else:
            shutil.copy2(source, destination)


def isolated_root(directory: str | Path, copy_contracts: bool = False) -> Path:
    root = Path(directory) / "contracts"
    root.mkdir()
    (root / "__init__.py").write_text("from __future__ import annotations\n")
    shutil.copytree(
        CONTRACTS_ROOT / "spec",
        root / "spec",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    for name in (
        "deploy",
        "README.md",
        "PRODUCTION.md",
        "agent-runtime",
        "header-spikes",
        "skills",
    ):
        link_or_copy(CONTRACTS_ROOT / name, root / name)
    shutil.copyfile(
        CONTRACTS_ROOT / "requirements-dev.in", root / "requirements-dev.in"
    )
    if copy_contracts:
        (root / "contract-lab").mkdir()
        for child in (CONTRACTS_ROOT / "contract-lab").iterdir():
            destination = root / "contract-lab" / child.name
            if child.name == "contracts":
                shutil.copytree(child, destination)
            else:
                link_or_copy(child, destination)
    else:
        link_or_copy(CONTRACTS_ROOT / "contract-lab", root / "contract-lab")
    return root


def run_tool(
    root: Path,
    tool: str,
    *args: str,
    optimize: bool = False,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        *(["-O"] if optimize else []),
        "-m",
        "contracts.spec.tools." + Path(tool).stem,
        *args,
    ]
    environment = {
        **ToolEnvironment().model_dump(exclude_none=True),
        "PYTHONPATH": os.pathsep.join((str(REPO_ROOT), str(REPO_ROOT / "src"))),
        "PYTHONDONTWRITEBYTECODE": "1",
        **(env or {}),
    }
    return subprocess.run(
        command,
        cwd=root.parent,
        env=environment,
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
