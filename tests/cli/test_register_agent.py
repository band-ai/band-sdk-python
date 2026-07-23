from __future__ import annotations

from importlib.resources import as_file, files
from unittest.mock import MagicMock, patch

import pytest

from band.cli.register_agent import SCRIPT_NAME, build_parser, main


# --- Parser ---


def test_api_key_is_optional() -> None:
    """The positional API key is optional (falls back to the env var)."""
    args = build_parser().parse_args([])
    assert args.api_key is None

    args = build_parser().parse_args(["user-key-123"])
    assert args.api_key == "user-key-123"


# --- Bundled script resolution ---


def test_bundled_script_is_packaged() -> None:
    """register-agent.sh ships inside band.cli and is resolvable at runtime."""
    with as_file(files("band.cli").joinpath(SCRIPT_NAME)) as script_path:
        assert script_path.exists()
        assert script_path.name == SCRIPT_NAME
        assert script_path.read_text().startswith("#!/usr/bin/env bash")


# --- main() wiring ---


def _run_main(argv: list[str], *, returncode: int = 0) -> MagicMock:
    """Invoke main() with argv, mocking the subprocess call. Returns the mock."""
    completed = MagicMock(returncode=returncode)
    with (
        patch("band.cli.register_agent.subprocess.run", return_value=completed) as run,
        patch("sys.argv", ["band-register-agent", *argv]),
        pytest.raises(SystemExit) as exc,
    ):
        main()
    run.exit_code = exc.value.code  # type: ignore[attr-defined]
    return run


def test_runs_bash_against_bundled_script() -> None:
    """main() invokes bash on the bundled script and mirrors its exit code."""
    run = _run_main([], returncode=0)
    assert run.exit_code == 0  # type: ignore[attr-defined]

    cmd = run.call_args.args[0]
    assert cmd[0] == "bash"
    assert cmd[1].endswith(SCRIPT_NAME)


def test_positional_key_populates_env() -> None:
    """A positional API key is exported as BAND_USER_API_KEY for the script."""
    run = _run_main(["user-key-xyz"])
    env = run.call_args.kwargs["env"]
    assert env["BAND_USER_API_KEY"] == "user-key-xyz"


def test_no_key_leaves_env_untouched() -> None:
    """Without an argument, the script reads BAND_USER_API_KEY from the inherited env."""
    with patch.dict("os.environ", {"BAND_USER_API_KEY": "from-env"}, clear=False):
        run = _run_main([])
    env = run.call_args.kwargs["env"]
    assert env["BAND_USER_API_KEY"] == "from-env"


def test_nonzero_exit_code_is_propagated() -> None:
    """A failing script surfaces its exit code (so callers/CI see the failure)."""
    run = _run_main([], returncode=1)
    assert run.exit_code == 1  # type: ignore[attr-defined]


def test_missing_bash_exits_127() -> None:
    """If bash is not on PATH, exit 127 with a clear message."""
    with (
        patch(
            "band.cli.register_agent.subprocess.run",
            side_effect=FileNotFoundError,
        ),
        patch("sys.argv", ["band-register-agent"]),
        pytest.raises(SystemExit) as exc,
    ):
        main()
    assert exc.value.code == 127
