"""Shared fixtures for the NOOA adapter tests."""

from __future__ import annotations

import importlib.util

import pytest

from band.testing import FakeAgentTools

# NOOA supports Python 3.12 and 3.13 on POSIX only; elsewhere no module in this
# package can import, so none is collected.
if importlib.util.find_spec("nooa") is None:
    collect_ignore_glob = ["test_*.py", "kit.py"]


@pytest.fixture
def tools() -> FakeAgentTools:
    """A fresh, call-tracking Band tool surface for one test."""
    return FakeAgentTools()
