"""band-trigger's entry point: exit codes, errors on stderr, and stdout carrying only the room id."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from pytest_httpx import HTTPXMock

from band.cli.trigger import AuthMode, main
from tests.cli.triggerwire.kit import (
    CHATS,
    INVALID_KEY,
    MESSAGES,
    PARTICIPANTS,
    PEERS,
    ROOM_ID,
    TARGET,
    added_participant,
    answer,
    answer_peer_pages,
    api_url,
    cli_args,
    created_room,
    message_sent,
    page_params,
    platform_error,
)
from tests.logsupport import band_log_env

MODE = AuthMode.AGENT


def argv(*extra: str) -> list[str]:
    return ["band-trigger", *cli_args(MODE, f"@{TARGET['handle']}"), *extra]


def run_main() -> int | str | None:
    with pytest.raises(SystemExit) as exited:
        main()
    return exited.value.code


class TestMain:
    def test_success_writes_only_the_room_id_to_stdout(
        self,
        httpx_mock: HTTPXMock,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        cli_logging: None,
    ) -> None:
        """BAND_LOG_STREAM cannot move logs onto stdout, which callers parse."""
        answer_peer_pages(httpx_mock, MODE, [[TARGET]])
        answer(httpx_mock, MODE, "POST", CHATS, created_room())
        answer(httpx_mock, MODE, "POST", PARTICIPANTS, added_participant(TARGET))
        answer(httpx_mock, MODE, "POST", MESSAGES, message_sent(TARGET))
        monkeypatch.setattr("sys.argv", argv())

        with band_log_env(monkeypatch, STREAM="stdout", FILE=None):
            code = run_main()

        out, err = capsys.readouterr()
        assert code == 0
        assert out == f"{ROOM_ID}\n"
        assert f"Created chatroom: {ROOM_ID}" in err

    def test_missing_api_key_exits_1_with_the_reason(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        cli_logging: None,
    ) -> None:
        monkeypatch.delenv("BAND_API_KEY", raising=False)
        monkeypatch.setattr("sys.argv", ["band-trigger"])

        assert run_main() == 1
        assert "API key is required" in capsys.readouterr().err

    def test_platform_rejection_exits_1_with_its_message_and_no_traceback(
        self,
        httpx_mock: HTTPXMock,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        cli_logging: None,
    ) -> None:
        answer(
            httpx_mock,
            MODE,
            "GET",
            PEERS,
            platform_error("unauthorized", INVALID_KEY),
            status=401,
            params=page_params(1),
        )
        monkeypatch.setattr("sys.argv", argv())

        code = run_main()

        err = capsys.readouterr().err
        assert code == 1
        assert f"Error: Failed to look up peer: {INVALID_KEY}\n" in err
        assert "Traceback" not in err

    def test_unexpected_failure_exits_1_and_logs_the_traceback(
        self,
        httpx_mock: HTTPXMock,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        cli_logging: None,
    ) -> None:
        httpx_mock.add_exception(
            httpx.ConnectError("connection refused"),
            url=api_url(MODE, PEERS, page_params(1)),
        )
        monkeypatch.setattr("sys.argv", argv())

        code = run_main()

        err = capsys.readouterr().err
        assert code == 1
        assert "Unexpected error" in err
        assert "Error: connection refused\n" in err

    @pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
    def test_timeout_exits_1_naming_the_budget(
        self,
        httpx_mock: HTTPXMock,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        cli_logging: None,
    ) -> None:
        """A zero budget expires at the first wait, whichever request it is."""

        async def never_answers(request: httpx.Request) -> httpx.Response:
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        httpx_mock.add_callback(never_answers, url=api_url(MODE, PEERS, page_params(1)))
        monkeypatch.setattr("sys.argv", argv("--timeout", "0"))

        assert run_main() == 1
        assert "Error: operation timed out after 0 seconds" in capsys.readouterr().err
