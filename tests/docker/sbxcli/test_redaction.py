from __future__ import annotations

import sys

import pytest

from tests.docker.toolkit.sbx_cli import (
    run_redacting_secret,
)


class TestSecretRedaction:
    def test_run_redacting_secret_masks_the_secret_on_failure(self) -> None:
        # The never-in-VM proof passes real Band keys through secret-bearing argv
        # (set-custom --value, the files_containing grep). A failure must raise
        # with the key masked — including a key EMBEDDED inside a longer argument,
        # not just one that is a whole argument.
        secret = "band-sk-neverVM-SEKRET"
        argv = [
            sys.executable,
            "-c",
            "import sys; print('daemon hiccup', file=sys.stderr); sys.exit(3)",
            f"grep -ralF -- {secret} /workspace",
            secret,
        ]

        with pytest.raises(RuntimeError) as excinfo:
            run_redacting_secret(argv, secret=secret)

        message = str(excinfo.value)
        assert secret not in message
        assert "***" in message
        # The useful diagnostics survive redaction.
        assert "exit 3" in message
        assert "daemon hiccup" in message

    def test_run_redacting_secret_returns_stdout_on_success(self) -> None:
        secret = "band-sk-neverVM-SEKRET"
        argv = [sys.executable, "-c", "print('clean')", secret]

        assert run_redacting_secret(argv, secret=secret) == "clean\n"

    def test_secret_timeout_does_not_expose_command_or_captured_output(self) -> None:
        secret = "band-timeout-secret"
        argv = [sys.executable, "-c", "import time; time.sleep(5)", secret]
        with pytest.raises(RuntimeError, match="timed out") as error:
            run_redacting_secret(argv, secret=secret, timeout=1)
        assert secret not in str(error.value)
        assert "***" in str(error.value)
