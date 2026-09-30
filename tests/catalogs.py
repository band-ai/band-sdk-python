"""An adapter whose harness advertises a model catalog, for start-up checks.

The catalog is only reachable after ``on_started``, like a client that
``on_started`` launches.
"""

from __future__ import annotations

from band.core.model_catalog import ModelCatalog, ModelChoice, ModelSelection
from band.core.simple_adapter import SimpleAdapter

SONNET_CATALOG = ModelCatalog(models=(ModelChoice(id="sonnet", efforts=("high",)),))


class CatalogAdapter(SimpleAdapter[None]):
    """Lists ``SONNET_CATALOG``, but only once ``on_started`` has run."""

    def __init__(
        self, selection: ModelSelection, *, cleanup_fails: bool = False
    ) -> None:
        super().__init__()
        self._selection = selection
        self._cleanup_fails = cleanup_fails
        self._started_catalog: ModelCatalog | None = None
        self.cleaned_up = False

    async def on_started(self, agent_name: str, agent_description: str) -> None:
        await super().on_started(agent_name, agent_description)
        self._started_catalog = SONNET_CATALOG

    @property
    def model_selection(self) -> ModelSelection:
        return self._selection

    async def list_models(self) -> ModelCatalog | None:
        assert self._started_catalog is not None, "listed before on_started"
        return self._started_catalog

    async def on_message(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    async def cleanup_all(self) -> None:
        self.cleaned_up = True
        if self._cleanup_fails:
            raise RuntimeError("cleanup failed")
