"""An adapter config never echoes a rejected value, which may be a key or token."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from band.core.adapterconfig import BaseAdapterConfig, EnvAdapterConfig

SECRET = "sk-rejected-secret"


class TestAdapterConfigRedaction:
    @pytest.mark.parametrize("base", [BaseAdapterConfig, EnvAdapterConfig])
    @pytest.mark.parametrize(
        "data",
        [{"port": SECRET}, {"section": {"port": SECRET}}],
        ids=["direct", "nested"],
    )
    def test_a_rejected_value_is_not_echoed(
        self, base: type[BaseAdapterConfig], data: dict[str, object]
    ) -> None:
        # Pydantic takes hide_input_in_errors from the outermost model, so a
        # nested section is only redacted when every config base sets it.
        class Section(BaseAdapterConfig):
            port: int = 0

        class Config(base):
            port: int = 0
            section: Section = Section()

        with pytest.raises(ValidationError) as rejected:
            Config.model_validate(data)

        assert SECRET not in str(rejected.value)
