"""Band's copies of Parlant vocabulary match Parlant's own definitions.

They are copied so the code reading them needs no Parlant import; these
tests are what keeps each copy honest.
"""

from __future__ import annotations

from typing import get_args

import pytest

from band.adapters.parlant.responses import PARLANT_PREAMBLE_TAG
from band.integrations.parlant.customschema import Descriptor, ParlantType

tags = pytest.importorskip("parlant.core.tags")
tools = pytest.importorskip("parlant.core.tools")


def test_preamble_tag_is_parlants():
    assert PARLANT_PREAMBLE_TAG == tags.Tag.preamble().name


def test_advertised_types_are_parlant_parameter_types():
    assert set(ParlantType) <= set(get_args(tools.ToolParameterType))


def test_descriptor_keys_are_parlant_descriptor_keys():
    assert (
        Descriptor.__annotations__.keys()
        <= tools.ToolParameterDescriptor.__annotations__.keys()
    )
