"""Host-only outbound ID key. No secret is journaled, exported, or given to callbacks."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import hmac
import os
import re
import secrets
import stat
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

from remote_agent.capabilities import exclusive_lock, require_linux
from remote_agent.protocol import canonical
from remote_agent.storage import (
    StoreError,
    private_directory,
    private_file,
    sync_directory,
)
from remote_agent.types import JsonObject, SourcePath

if TYPE_CHECKING:
    from remote_agent.program import Artifact
if TYPE_CHECKING:
    from remote_agent.store import Store


def _publish_new(temporary: SourcePath, destination: SourcePath) -> None:
    """Linux atomic no-replace rename: never expose a partial or hard-linked key."""
    require_linux()
    try:
        rename = ctypes.CDLL(None, use_errno=True).renameat2
    except AttributeError as error:
        raise OSError(
            errno.ENOTSUP, "Atomic exclusive key publication is unavailable"
        ) from error
    rename.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    rename.restype = ctypes.c_int
    # AT_FDCWD, RENAME_NOREPLACE. No overwrite-capable fallback is safe here.
    if rename(-100, os.fsencode(temporary), -100, os.fsencode(destination), 1):
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(destination))


class HostKey:
    def __init__(self, store: Store | SimpleNamespace) -> None:
        self.store = store
        self.path = Path(str(store.path) + ".host-key")
        self.stage_prefix = (
            ".host-key-create-"
            + hashlib.sha256(os.fsencode(self.path)).hexdigest()[:16]
            + "-"
        )
        self.rotate_prefix = self.stage_prefix.replace("-create-", "-rotate-")
        visible = [
            Path("/usr"),
            Path("/lib"),
            Path("/lib64"),
            Path(__file__).resolve().parent,
        ]
        if any(self.path.resolve().is_relative_to(root.resolve()) for root in visible):
            raise StoreError("Host ID key path is visible to the program sandbox")

    def assert_hidden(self, artifact: Artifact) -> None:
        if self.path.resolve() == Path(artifact.path).resolve():
            raise StoreError("Host ID key must not be a mounted program artifact")

    @contextmanager
    def locked(self) -> Generator[None, None, None]:
        if getattr(self.store, "durable", True) is False:
            with self.store.lock:
                yield
            return
        private_directory(self.path.parent)
        descriptor = private_file(str(self.path) + ".lock")
        try:
            exclusive_lock(descriptor)
            # Only this key's namespace is swept, under its lifecycle lock.
            # A killed pre-publication writer must not leave a second secret copy.
            staged = list(self.path.parent.glob(self.stage_prefix + "*"))
            staged.extend(self.path.parent.glob(self.rotate_prefix + "*"))
            # Older writers used an unscoped tempfile suffix. New rotations are
            # key-scoped, so never sweep another current writer's namespace.
            staged.extend(
                path
                for path in self.path.parent.glob(".host-key-rotate-*")
                if re.fullmatch(r"\.host-key-rotate-[a-z0-9_]{8}", path.name)
            )
            for path in staged:
                path.unlink(missing_ok=True)
            if staged:
                sync_directory(self.path.parent)
            yield
        finally:
            os.close(descriptor)

    def read(self) -> bytes | None:
        """Caller holds lifecycle lock. Missing is recoverable; malformed is not."""
        if getattr(self.store, "durable", True) is False:
            return getattr(self.store, "_ephemeral_host_key", None)
        require_linux()
        try:
            descriptor = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except FileNotFoundError:
            return None
        except OSError as error:
            raise StoreError("Cannot open host ID key") from error
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_size != 32
            ):
                raise StoreError(
                    "Host ID key must be an owner-only 32-byte regular file"
                )
            key = os.read(descriptor, 33)
            if len(key) != 32:
                raise StoreError("Invalid host ID key size")
            return key
        finally:
            os.close(descriptor)

    def create(self, key: bytes) -> None:
        """Exclusive, durable publication; caller holds lifecycle lock."""
        if not isinstance(key, bytes) or len(key) != 32:
            raise StoreError("Host ID key must contain exactly 32 bytes")
        if getattr(self.store, "durable", True) is False:
            if getattr(self.store, "_ephemeral_host_key", None) is not None:
                raise StoreError("Host ID key already exists")
            cast("Store", self.store)._ephemeral_host_key = key
            return
        descriptor = None
        temporary = None
        try:
            descriptor, temporary = tempfile.mkstemp(
                prefix=self.stage_prefix, dir=self.path.parent
            )
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = None
                os.fchmod(stream.fileno(), 0o600)
                stream.write(key)
                stream.flush()
                os.fsync(stream.fileno())
            _publish_new(temporary, self.path)
            sync_directory(self.path.parent)
        except OSError as error:
            raise StoreError("Cannot create host ID key") from error
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)

    @staticmethod
    def fingerprint(key: bytes) -> str:
        return hashlib.sha256(key).hexdigest()[:8]

    def _ensure(self) -> bytes:
        key = self.read()
        previous = self.store.get("host_key_id")
        if key is None:
            key = secrets.token_bytes(32)
            self.create(key)
        fingerprint = self.fingerprint(key)
        pending = self.store.get("host_key_rotation")
        if previous != fingerprint:
            kind = (
                "host_key_rotated"
                if pending == fingerprint
                else "host_key_recovered"
                if previous is not None
                else "host_key_created"
            )
            self.store.commit(
                kind,
                {"key_id": fingerprint},
                updates={"host_key_id": fingerprint, "host_key_rotation": None},
            )
        elif pending is not None:
            self.store.commit(
                "host_key_rotation_abandoned",
                {"key_id": pending},
                updates={"host_key_rotation": None},
            )
        return key

    def ensure(self) -> None:
        with self.locked():
            self._ensure()

    def identifier(self, session: str, actor: str, input_id: str, attempt: int) -> str:
        with self.locked():
            key = self._ensure()
            message = canonical(["band-remote-id/1", session, actor, input_id, attempt])
            return "remote:" + hmac.new(key, message, hashlib.sha256).hexdigest()[:40]

    def rotate(self) -> JsonObject:
        """Operator-only rotation. Stored outbox IDs are never recomputed."""
        with self.locked():
            if self.read() is None or self.store.get("host_key_id") is None:
                raise StoreError(
                    "Start the agent to recover or initialize its host ID key first"
                )
            key = secrets.token_bytes(32)
            fingerprint = self.fingerprint(key)
            if getattr(self.store, "durable", True) is False:
                cast("Store", self.store)._ephemeral_host_key = key
                self.store.commit(
                    "host_key_rotated",
                    {"key_id": fingerprint},
                    updates={"host_key_id": fingerprint, "host_key_rotation": None},
                )
                return {"event": "host_key_rotated", "key_id": fingerprint}
            descriptor, temporary = tempfile.mkstemp(
                prefix=self.rotate_prefix, dir=self.path.parent
            )
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    os.fchmod(stream.fileno(), 0o600)
                    stream.write(key)
                    stream.flush()
                    os.fsync(stream.fileno())
                self.store.commit(
                    "host_key_rotation_prepared",
                    {"key_id": fingerprint},
                    updates={"host_key_rotation": fingerprint},
                )
                os.replace(temporary, self.path)
                sync_directory(self.path.parent)
                self.store.commit(
                    "host_key_rotated",
                    {"key_id": fingerprint},
                    updates={"host_key_id": fingerprint, "host_key_rotation": None},
                )
                return {"event": "host_key_rotated", "key_id": fingerprint}
            except OSError as error:
                raise StoreError("Cannot rotate host ID key") from error
            finally:
                Path(temporary).unlink(missing_ok=True)
