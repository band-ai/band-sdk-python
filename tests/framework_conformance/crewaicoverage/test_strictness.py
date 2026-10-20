from __future__ import annotations

import pytest

from tests.framework_configs.sentinel import StrictnessSettings


class TestMissingFrameworkOptoutIsParsedAsABoolean:
    @pytest.mark.parametrize("flag", ["1", "0", ""])
    def test_missing_framework_optout_is_parsed_as_a_boolean(
        self, flag: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``BAND_ALLOW_MISSING_FRAMEWORKS=0`` must keep strict mode ON.

        The flag is set explicitly rather than conditionally: e2e.yml sends "0" for every
        lane that should stay strict, and only ci.yml's crewai/parlant jobs send "1". A
        presence check reads that "0" as an opt-out — so the value has to be parsed as a
        boolean, or a cell asking to stay strict silently disables the fail-loud guard
        instead.
        """
        monkeypatch.setenv("CI", "true")
        monkeypatch.setenv("BAND_ALLOW_MISSING_FRAMEWORKS", flag)

        settings = StrictnessSettings()
        strict = settings.ci and not settings.band_allow_missing_frameworks
        assert strict is (flag != "1")
