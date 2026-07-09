"""SingleInstanceGuard: one running process per agent id per host."""

from __future__ import annotations

import pytest

from band.core.exceptions import BandConfigError
from band.runtime.single_instance import SingleInstanceGuard, release_all_held


class TestSingleInstanceGuard:
    def test_second_holder_is_refused(self, tmp_path):
        first = SingleInstanceGuard("agent-1", lock_dir=tmp_path)
        second = SingleInstanceGuard("agent-1", lock_dir=tmp_path)
        first.acquire()
        try:
            with pytest.raises(BandConfigError, match="already running"):
                second.acquire()
        finally:
            first.release()

    def test_release_frees_the_lock_for_reacquire(self, tmp_path):
        first = SingleInstanceGuard("agent-1", lock_dir=tmp_path)
        first.acquire()
        first.release()

        second = SingleInstanceGuard("agent-1", lock_dir=tmp_path)
        second.acquire()
        second.release()

    def test_different_agent_ids_do_not_contend(self, tmp_path):
        one = SingleInstanceGuard("agent-1", lock_dir=tmp_path)
        two = SingleInstanceGuard("agent-2", lock_dir=tmp_path)
        one.acquire()
        try:
            two.acquire()
            two.release()
        finally:
            one.release()

    def test_acquire_and_release_are_idempotent(self, tmp_path):
        guard = SingleInstanceGuard("agent-1", lock_dir=tmp_path)
        guard.acquire()
        guard.acquire()  # holder re-acquiring is a no-op, not a conflict
        guard.release()
        guard.release()

    def test_lock_file_is_left_behind_holderless(self, tmp_path):
        """Files are never unlinked (unlink races a concurrent reacquire)."""
        guard = SingleInstanceGuard("agent-1", lock_dir=tmp_path)
        guard.acquire()
        guard.release()
        assert guard.lock_path.exists()

    def test_release_all_held_reaps_abandoned_locks(self, tmp_path):
        """A lock leaked by a killed owner must be reapable so the same
        process can run the agent again (e2e reruns depend on this)."""
        leaked = SingleInstanceGuard("agent-1", lock_dir=tmp_path)
        leaked.acquire()  # owner "dies" without release

        assert release_all_held() == ["agent-1"]
        fresh = SingleInstanceGuard("agent-1", lock_dir=tmp_path)
        fresh.acquire()
        fresh.release()

    def test_release_all_held_is_empty_after_clean_release(self, tmp_path):
        guard = SingleInstanceGuard("agent-1", lock_dir=tmp_path)
        guard.acquire()
        guard.release()
        assert release_all_held() == []
