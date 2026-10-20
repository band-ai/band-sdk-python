from __future__ import annotations

from types import SimpleNamespace

import pytest

from band.core.types import TurnUsage


class TestTurnUsageConstructors:
    """The shared from_object / from_mapping constructors the adapters build on."""

    def test_from_object_reads_named_attrs(self):
        src = SimpleNamespace(inp=100, out=20, cr=5, cw=3)
        assert TurnUsage.from_object(
            src, input="inp", output="out", cache_read="cr", cache_write="cw"
        ) == TurnUsage(100, 20, 5, 3)

    def test_from_object_none_source_is_empty(self):
        assert TurnUsage.from_object(None, input="i", output="o") == TurnUsage()

    def test_from_object_omitted_cache_keys_stay_zero(self):
        src = SimpleNamespace(i=100, o=20, cache_read_tokens=999)
        # cache_read/cache_write not passed → not read even if present on src
        assert TurnUsage.from_object(src, input="i", output="o") == TurnUsage(100, 20)

    def test_from_mapping_reads_named_keys(self):
        data = {"i": 100, "o": 20, "cr": 5, "cw": 3}
        assert TurnUsage.from_mapping(
            data, input="i", output="o", cache_read="cr", cache_write="cw"
        ) == TurnUsage(100, 20, 5, 3)

    @pytest.mark.parametrize("bad", [None, "not a mapping", 42, ["list"]])
    def test_from_mapping_non_mapping_is_empty(self, bad):
        assert TurnUsage.from_mapping(bad, input="i", output="o") == TurnUsage()

    def test_missing_and_non_int_fields_default_to_zero(self):
        data = {"i": "oops", "o": None}  # non-int / missing
        assert TurnUsage.from_mapping(data, input="i", output="o") == TurnUsage()

    def test_fields_are_raw_no_cache_folding(self):
        """Per the convention, values pass through raw — cache is never folded
        into input regardless of provider."""
        data = {"i": 100, "o": 20, "cr": 5, "cw": 3}
        assert TurnUsage.from_mapping(
            data, input="i", output="o", cache_read="cr", cache_write="cw"
        ) == TurnUsage(
            input_tokens=100,
            output_tokens=20,
            cache_read_tokens=5,
            cache_write_tokens=3,
        )
