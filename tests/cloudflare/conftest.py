"""Run the example's Python code in Cloudflare's actual workerd runtime."""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from pydantic_settings import BaseSettings, SettingsConfigDict

from tests.paths import EXAMPLES_ROOT


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore", case_sensitive=False, env_ignore_empty=True
    )
    CLOUDFLARE_TESTS_ENABLED: bool = False


@pytest.fixture(scope="module")
def worker(tmp_path_factory: pytest.TempPathFactory) -> Iterator[httpx.Client]:
    if not Settings().CLOUDFLARE_TESTS_ENABLED:
        pytest.skip(
            "Set CLOUDFLARE_TESTS_ENABLED=true for real Python Worker runtime tests"
        )
    example = EXAMPLES_ROOT / "cloudflare"
    subprocess.run(
        ["uv", "run", "--locked", "pywrangler", "sync"],
        cwd=example,
        check=True,
        capture_output=True,
        text=True,
    )
    root = tmp_path_factory.mktemp("cloudflare")
    source = root / "src"
    source.mkdir()
    shutil.copy(example / "src" / "worker.py", source / "worker.py")
    for name in ("kit.py", "harness.py"):
        shutil.copy(Path(__file__).with_name(name), source / name)
    shutil.copytree(example / "python_modules", root / "python_modules")
    config = {
        "name": "band-python-tests",
        "main": "src/harness.py",
        "compatibility_date": "2026-09-29",
        "compatibility_flags": ["python_workers"],
        "durable_objects": {"bindings": [{"name": "HARNESS", "class_name": "Harness"}]},
        "migrations": [{"tag": "v1", "new_sqlite_classes": ["Harness"]}],
    }
    (root / "wrangler.json").write_text(json.dumps(config))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
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
                yield client
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
