"""Tests for the bug-hunting skill's example inventory script.

Mechanisms (PEP 723 parsing, following a local ``BaseSettings`` import, command
extraction, unreadable files) are tested against small synthetic example trees,
so they assert the behaviour directly and survive any rename under ``examples/``.
The repository scan is then asserted on structural properties only — enough to
catch a parse regression that silently empties or corrupts the inventory,
without pinning this skill's tests to particular example filenames.
"""

from __future__ import annotations

from pathlib import Path
from types import ModuleType

import pytest

from tests.paths import REPO_ROOT

METADATA = """# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[anthropic]"]
# ///
"""

SETTINGS_MODULE = '''"""Example settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class ExampleSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="demo_")

    agent_key: str = "darter"
    band_ws_url: str = "wss://example.invalid"
    other_key: str
'''

EXAMPLE_MODULE = (
    METADATA
    + '''"""A demo example.

Run with:
    uv run examples/demo/example.py
"""

import os

from settings import ExampleSettings

from band import Agent


async def main() -> None:
    settings = ExampleSettings()
    Agent.from_config(settings.agent_key)
    Agent.from_config(settings.other_key)
    print(os.environ["EXTRA_TOKEN"])
'''
)


@pytest.fixture
def examples_tree(tmp_path: Path) -> Path:
    """A repository root holding ``examples/demo/`` with a local settings module."""
    demo = tmp_path / "examples" / "demo"
    demo.mkdir(parents=True)
    (demo / "settings.py").write_text(SETTINGS_MODULE, encoding="utf-8")
    (demo / "example.py").write_text(EXAMPLE_MODULE, encoding="utf-8")
    return tmp_path


def test_multiline_pep723_dependencies(discovery: ModuleType) -> None:
    source = """# /// script
# requires-python = \">=3.11\"
# dependencies = [
#   \"band-sdk[anthropic]\",
#   \"fastapi>=0.110\",
# ]
# ///
"""
    assert discovery.metadata_dependencies(source) == (
        "band-sdk[anthropic]",
        "fastapi>=0.110",
    )


def test_local_settings_import_is_followed(
    discovery: ModuleType, examples_tree: Path
) -> None:
    """Config keys and env inputs come from the settings module next door."""
    (example,) = discovery.discover(examples_tree, "examples", None)

    assert example.path == "examples/demo/example.py"
    assert example.family == "demo"
    assert example.summary == "A demo example."
    # A field default resolves to the literal key; a field without one reports
    # the environment variable that has to supply it.
    assert example.config_keys == ("${DEMO_OTHER_KEY}", "darter")
    assert set(example.environment) == {
        "DEMO_AGENT_KEY",
        "DEMO_BAND_WS_URL",
        "DEMO_OTHER_KEY",
        "EXTRA_TOKEN",
    }
    assert example.documented_commands == ("uv run examples/demo/example.py",)


@pytest.mark.parametrize(
    ("docstring", "expected"),
    [
        (
            "Run with:\n    uv run examples/demo/example.py\n",
            ("uv run examples/demo/example.py",),
        ),
        (
            "    python examples/demo/example.py   \n",
            ("python examples/demo/example.py",),
        ),
        (
            "    python -m app --host localhost --port 10000\n",
            ("python -m app --host localhost --port 10000",),
        ),
        (
            "    python3 examples/demo/example.py\n",
            ("python3 examples/demo/example.py",),
        ),
        ("Note: Must be run from repo root as it imports characters.py\n", ()),
        ("    uv run one.py\n    uv run one.py\n", ("uv run one.py",)),
    ],
    ids=[
        "uv-run",
        "python-trailing-space",
        "python-module",
        "python3",
        "prose",
        "deduped",
    ],
)
def test_documented_commands_match_the_forms_examples_use(
    discovery: ModuleType, docstring: str, expected: tuple[str, ...]
) -> None:
    assert discovery.documented_commands(docstring) == expected


def test_unreadable_file_does_not_abort_the_inventory(
    discovery: ModuleType, examples_tree: Path
) -> None:
    (examples_tree / "examples" / "demo" / "binary.py").write_bytes(
        b"\xff\xfe not utf-8"
    )

    inventory = discovery.discover(examples_tree, "examples", None)

    assert [item.path for item in inventory] == ["examples/demo/example.py"]


def test_repository_inventory_stays_healthy(discovery: ModuleType) -> None:
    inventory = discovery.discover(REPO_ROOT, "examples", None)

    # A floor, not a count: guards a parse regression that empties the inventory.
    assert len(inventory) > 20
    assert all(item.summary and item.dependencies for item in inventory)
    # Multi-line PEP 723 dependency blocks really occur, and really parse.
    assert any(len(item.dependencies) > 1 for item in inventory)
    commands = [command for item in inventory for command in item.documented_commands]
    assert commands
    assert all(command.startswith(("uv run", "python")) for command in commands)
    assert all(command == command.strip() for command in commands)


def test_a_top_level_example_belongs_to_no_family(discovery: ModuleType) -> None:
    """An example directly under ``examples/`` has no family directory to name."""
    inventory = discovery.discover(REPO_ROOT, "examples", None)

    top_level = [
        item for item in inventory if "/" not in item.path.removeprefix("examples/")
    ]
    assert top_level
    assert [item.family for item in top_level] == [""] * len(top_level)


def test_family_filter_scopes_to_one_directory(discovery: ModuleType) -> None:
    inventory = discovery.discover(REPO_ROOT, "examples", None)
    family = sorted({item.family for item in inventory if item.family})[0]

    scoped = discovery.discover(REPO_ROOT, "examples", family)

    assert scoped
    assert all(item.path.startswith(f"examples/{family}/") for item in scoped)
    assert {item.path for item in scoped} == {
        item.path for item in inventory if item.family == family
    }
