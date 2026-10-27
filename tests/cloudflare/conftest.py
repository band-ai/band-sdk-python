"""Run the example's Python code in Cloudflare's actual workerd runtime."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic_settings import BaseSettings, SettingsConfigDict

from tests.cloudflare.client import WorkerRuntime
from tests.cloudflare.contract import Scenario
from tests.paths import EXAMPLES_ROOT
from tests.ports import reserve_port

EXAMPLE = EXAMPLES_ROOT / "cloudflare"
HARNESS_MODULES = ("contract.py", "kit.py", "lifecycle.py", "harness.py")
# JSONC (comments, trailing commas) is valid JavaScript object-literal syntax.
READ_JSONC = (
    'process.stdout.write(JSON.stringify(eval("(" + '
    'require("fs").readFileSync(0, "utf8") + ")")))'
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore", case_sensitive=False, env_ignore_empty=True
    )
    CLOUDFLARE_TESTS_ENABLED: bool = False


def shipped_compatibility() -> dict[str, Any]:
    shipped = json.loads(
        subprocess.run(
            ["node", "-e", READ_JSONC],
            input=(EXAMPLE / "wrangler.jsonc").read_text(),
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    return {
        "compatibility_date": shipped["compatibility_date"],
        "compatibility_flags": shipped["compatibility_flags"],
    }


def harness_project(root: Path) -> None:
    source = root / "src"
    source.mkdir()
    shutil.copy(EXAMPLE / "src" / "worker.py", source / "worker.py")
    for name in HARNESS_MODULES:
        shutil.copy(Path(__file__).with_name(name), source / name)
    shutil.copytree(EXAMPLE / "python_modules", root / "python_modules")
    config = {
        "name": "band-python-tests",
        "main": "src/harness.py",
        **shipped_compatibility(),
        "durable_objects": {"bindings": [{"name": "HARNESS", "class_name": "Harness"}]},
        "migrations": [{"tag": "v1", "new_sqlite_classes": ["Harness"]}],
    }
    (root / "wrangler.json").write_text(json.dumps(config))


@pytest.fixture(scope="package")
def runtime(tmp_path_factory: pytest.TempPathFactory) -> Iterator[WorkerRuntime]:
    if not Settings().CLOUDFLARE_TESTS_ENABLED:
        pytest.skip(
            "Set CLOUDFLARE_TESTS_ENABLED=true for real Python Worker runtime tests"
        )
    subprocess.run(
        ["uv", "run", "--locked", "pywrangler", "sync"],
        cwd=EXAMPLE,
        check=True,
        capture_output=True,
        text=True,
    )
    root = tmp_path_factory.mktemp("cloudflare")
    harness_project(root)
    port = reserve_port()
    with (root / "runtime.log").open("w+") as log:
        process = subprocess.Popen(
            [
                "npx",
                "--yes",
                "wrangler@4.153.0",
                "dev",
                "--port",
                str(port),
                "--show-interactive-dev-session=false",
            ],
            cwd=root,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            with httpx.Client(
                base_url=f"http://127.0.0.1:{port}", timeout=30
            ) as client:
                deadline = time.monotonic() + 60
                while True:
                    try:
                        # Only process readiness uses real time; scenarios use gates/controlled clocks.
                        client.get(
                            "/cdn-cgi/local/explorer/api/local/workers"
                        ).raise_for_status()
                        break
                    except httpx.HTTPError:
                        if process.poll() is not None or time.monotonic() >= deadline:
                            log.seek(0)
                            pytest.fail("Python Worker failed to start:\n" + log.read())
                        time.sleep(0.1)
                yield WorkerRuntime(client)
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


@pytest.fixture(scope="package")
def constants(runtime: WorkerRuntime) -> dict[str, Any]:
    """The Worker's own settings vocabulary and wiring, read from workerd."""
    return runtime.run(Scenario.CONSTANTS)
