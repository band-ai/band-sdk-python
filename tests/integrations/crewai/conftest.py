"""Require the released CrewAI dependency for its integration contracts."""

from __future__ import annotations

import pytest

pytest.importorskip("crewai")
