from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from remote_agent.band.author_service import main as author_main
from remote_agent.band.serve_programs import main as program_main
from remote_agent.band_format import format_source
from remote_agent.cli import main as service_main
from remote_agent.commands import capture
from remote_agent.protocol import canonical, decode
from remote_agent.room_cli import main as room_main
from remote_agent.sdk import Handled, Message, TurnContext
from remote_agent.service import Service
from remote_agent.store import Store
from remote_agent.transport import EngineProcess


class TestCapabilities:
    @pytest.mark.parametrize("operation", ["command", "engine", "service", "storage"])
    def test_unsupported_host_has_no_process_or_state_side_effects(
        self, operation: str, tmp_path: Path
    ) -> None:
        state = tmp_path / "runtime"
        with (
            patch.object(sys, "platform", "win32"),
            patch(
                "subprocess.Popen",
                side_effect=AssertionError("Process startup attempted"),
            ) as process,
        ):
            with pytest.raises(RuntimeError, match="requires Linux"):
                match operation:
                    case "command":
                        capture(["unused"], {})
                    case "engine":
                        EngineProcess(state / "source.band", command=["unused"])
                    case "service":
                        Service({})
                    case "storage":
                        with Store(state / "journal.sqlite3"):
                            pass
            process.assert_not_called()
        assert not state.exists()

    def test_callback_values_and_source_formatting_remain_portable(self) -> None:
        with patch.object(sys, "platform", "win32"):
            context = TurnContext({"status": "active"})
            state = {"answered": 1}
            context.checkpoint(state)
            result = Handled(state, Message("answer", {"value": 5}))
            state["answered"] = 2

            assert context.snapshot == {"answered": 1}
            assert result.message is not None
            assert decode(canonical(result.message.payload)) == {"value": 5}
            assert format_source("type result\n value : integer\n") == (
                "type result\n  value: integer\n"
            )

    @pytest.mark.parametrize("operation", ["service", "author", "program"])
    def test_unqualified_python_is_rejected_before_runtime_startup(
        self, operation: str, tmp_path: Path
    ) -> None:
        config = tmp_path / "unused.json"
        with (
            patch.object(sys, "platform", "linux"),
            patch.object(sys, "version_info", (3, 11, 0, "final", 0)),
            patch.object(
                sys, "argv", ["contract-band-programs", "--config", str(config)]
            ),
            patch(
                "os.umask", side_effect=AssertionError("Process permissions changed")
            ),
            pytest.raises(RuntimeError, match="Python 3.12"),
        ):
            match operation:
                case "service":
                    Service({})
                case "author":
                    author_main(["--config", str(config)])
                case "program":
                    program_main()

    @pytest.mark.parametrize("command", ["service", "room"])
    def test_cli_explains_the_unsupported_interpreter(
        self, command: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with (
            patch.object(sys, "platform", "linux"),
            patch.object(sys, "version_info", (3, 11, 0, "final", 0)),
        ):
            if command == "service":
                exit_code = service_main(["serve", str(tmp_path / "unused.json")])
            else:
                exit_code = room_main(
                    ["http://127.0.0.1:1", str(tmp_path / "unused.key"), "identity"]
                )
        assert exit_code != 0
        assert "Python 3.12" in capsys.readouterr().err
