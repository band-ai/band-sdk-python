"""A corrupted downloaded TCK must never replace the installed executable."""

from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

import pytest

from tests.loaders import load_script_module

installer = load_script_module("scripts/install-kit-tck.py", "kit_tck_install")


def test_checksum_failure_preserves_existing_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "kit-tck"
    destination.write_bytes(b"existing executable")
    monkeypatch.setattr(installer.platform, "system", lambda: "Linux")
    monkeypatch.setattr(installer.platform, "machine", lambda: "aarch64")

    def download(url: str, *, timeout: int) -> io.BytesIO:
        if url.endswith("checksums.txt"):
            return io.BytesIO(
                ("0" * 64 + "  kit-tck_3.0.0-m.8_linux_arm64.tar.gz\n").encode()
            )
        return io.BytesIO(b"corrupted archive")

    monkeypatch.setattr(installer.urllib.request, "urlopen", download)
    with pytest.raises(ValueError, match="checksum mismatch"):
        installer.install(destination)
    assert destination.read_bytes() == b"existing executable"


@pytest.mark.parametrize(
    "machine,architecture", [("ARM64", "arm64"), ("AMD64", "amd64")]
)
def test_windows_machine_case_selects_released_archive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    machine: str,
    architecture: str,
) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("kit-tck.exe", b"verified executable")
    archive = buffer.getvalue()
    name = f"kit-tck_{installer.VERSION}_windows_{architecture}.zip"
    requested: list[str] = []

    def download(url: str, *, timeout: int) -> io.BytesIO:
        requested.append(url)
        if url.endswith("checksums.txt"):
            return io.BytesIO(
                f"{hashlib.sha256(archive).hexdigest()}  {name}\n".encode()
            )
        return io.BytesIO(archive)

    monkeypatch.setattr(installer.platform, "system", lambda: "Windows")
    monkeypatch.setattr(installer.platform, "machine", lambda: machine)
    monkeypatch.setattr(installer.urllib.request, "urlopen", download)
    destination = tmp_path / "kit-tck.exe"
    installer.install(destination)
    assert requested[-1] == f"{installer.RELEASE}/{name}"
    assert destination.read_bytes() == b"verified executable"
