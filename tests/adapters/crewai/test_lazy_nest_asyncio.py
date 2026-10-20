from __future__ import annotations

import concurrent.futures
import importlib
import sys
import threading


class TestLazyNestAsyncio:
    def test_nest_asyncio_not_applied_on_import(self, crewai_mocks):
        """Importing the adapter must not patch the event loop.

        Reloaded rather than evicted-and-reimported: dropping a band module from
        ``sys.modules`` re-executes it, but anything still holding a class from the
        old module object then has a ``__module__`` that resolves to nothing, and
        pydantic (which looks annotations up through that name) can no longer build
        the tool models. The original names are restored afterwards so classes
        imported elsewhere (e.g. ``CrewAIAdapterConfig``) keep their identity.
        """

        nest_mock = sys.modules["nest_asyncio"]
        nest_mock.reset_mock()
        module = importlib.import_module("band.adapters.crewai")
        original_names = dict(vars(module))

        try:
            importlib.reload(module)
            nest_mock.apply.assert_not_called()
        finally:
            vars(module).update(original_names)

    def test_ensure_nest_asyncio_applies_once(
        self, CrewAIAdapter, crewai_mocks, monkeypatch
    ):

        module = importlib.import_module("band.integrations.crewai.runtime")

        # Reset through monkeypatch so the flag is restored — it is process-global
        # and a leaked True would silently disable a later test's assertion.
        monkeypatch.setattr(module, "_nest_asyncio_applied", False)
        nest_mock = sys.modules["nest_asyncio"]
        nest_mock.reset_mock()

        module._ensure_nest_asyncio()
        module._ensure_nest_asyncio()

        assert nest_mock.apply.call_count == 1

    def test_nest_asyncio_lock_exists(self, CrewAIAdapter, crewai_mocks):
        """The integrations.crewai.runtime module owns the threading lock."""

        module = importlib.import_module("band.integrations.crewai.runtime")

        assert hasattr(module, "_nest_asyncio_lock")
        assert isinstance(module._nest_asyncio_lock, type(threading.Lock()))

    def test_ensure_nest_asyncio_is_thread_safe(self, CrewAIAdapter, crewai_mocks):
        """Multiple threads calling _ensure_nest_asyncio should only apply patch once."""

        module = importlib.import_module("band.integrations.crewai.runtime")

        module._nest_asyncio_applied = False
        nest_mock = sys.modules["nest_asyncio"]
        nest_mock.reset_mock()

        # Run multiple threads concurrently calling _ensure_nest_asyncio
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(module._ensure_nest_asyncio) for _ in range(10)]
            concurrent.futures.wait(futures)

        # Should only have been called once despite multiple concurrent threads
        assert nest_mock.apply.call_count == 1
