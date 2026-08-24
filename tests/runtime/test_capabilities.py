"""Tests for capability <-> platform feature-flag negotiation."""

from __future__ import annotations

from band.core.types import AdapterFeatures, Capability
from band.runtime.capabilities import prune_unsupported


class TestPruneUnsupported:
    """``prune_unsupported`` is pure; every state is a direct input/output check."""

    def test_none_feature_flags_keeps_everything(self) -> None:
        """No fetch happened (or it failed): no information, so nothing is pruned."""
        features = AdapterFeatures(capabilities={Capability.FILES, Capability.MEMORY})

        result = prune_unsupported(features, None)

        assert result is features

    def test_flag_true_keeps_capability(self) -> None:
        features = AdapterFeatures(capabilities={Capability.FILES})

        result = prune_unsupported(features, {"ff_file_transfer": True})

        assert result.capabilities == frozenset({Capability.FILES})

    def test_flag_false_prunes_capability(self) -> None:
        features = AdapterFeatures(capabilities={Capability.FILES, Capability.MEMORY})

        result = prune_unsupported(features, {"ff_file_transfer": False})

        assert result.capabilities == frozenset({Capability.MEMORY})

    def test_missing_key_prunes_capability(self) -> None:
        """A present dict missing the key means the platform predates the flag --
        distinct from ``None``, and the state most likely to regress silently."""
        features = AdapterFeatures(capabilities={Capability.FILES, Capability.CONTACTS})

        result = prune_unsupported(features, {})

        assert result.capabilities == frozenset({Capability.CONTACTS})

    def test_capabilities_with_no_platform_gate_are_never_pruned(self) -> None:
        """MEMORY/CONTACTS have no CAPABILITY_FEATURE_FLAGS entry -- only FILES
        is subject to this negotiation today."""
        features = AdapterFeatures(
            capabilities={Capability.MEMORY, Capability.CONTACTS}
        )

        result = prune_unsupported(features, {})

        assert result.capabilities == features.capabilities

    def test_other_adapter_features_survive_a_prune(self) -> None:
        features = AdapterFeatures(
            capabilities={Capability.FILES},
            include_tools=("band_send_message",),
        )

        result = prune_unsupported(features, {})

        assert result.capabilities == frozenset()
        assert result.include_tools == ("band_send_message",)
