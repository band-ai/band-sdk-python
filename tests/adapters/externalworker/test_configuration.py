"""The adapter needs a way to reach its worker, read from arguments or the environment."""

from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from band.adapters.external_worker import (
    ExternalWorkerAdapter,
    ExternalWorkerAdapterConfig,
)
from band.core.exceptions import BandConfigError
from tests.adapters.externalworker.kit import ENV_PREFIX

pytestmark = pytest.mark.usefixtures("no_worker_environment")


class TestConfiguration:
    def test_an_adapter_with_no_url_and_no_trigger_is_refused(self) -> None:
        with pytest.raises(BandConfigError, match="trigger_url or a trigger"):
            ExternalWorkerAdapter()

    def test_a_token_without_a_url_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="trigger_token is set"):
            ExternalWorkerAdapterConfig(trigger_token=SecretStr("orphan"))

    @pytest.mark.parametrize("field", ["timeout_seconds", "poll_interval_seconds"])
    @pytest.mark.parametrize("value", [0, -1])
    def test_a_non_positive_interval_is_refused(self, field: str, value: int) -> None:
        with pytest.raises(ValidationError, match=field):
            ExternalWorkerAdapterConfig.model_validate({field: value})

    def test_settings_come_from_prefixed_environment_variables(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(f"{ENV_PREFIX}TRIGGER_URL", "https://worker.example/run")
        monkeypatch.setenv(f"{ENV_PREFIX}TRIGGER_TOKEN", "routine-secret")
        monkeypatch.setenv(f"{ENV_PREFIX}TIMEOUT_SECONDS", "45")

        config = ExternalWorkerAdapterConfig()

        assert (
            config.trigger_url,
            config.trigger_token,
            config.timeout_seconds,
        ) == ("https://worker.example/run", SecretStr("routine-secret"), 45)
