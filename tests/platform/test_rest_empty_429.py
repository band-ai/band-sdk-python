"""Empty HTTP 429 bodies must raise a clean ApiError (GitHub #108).

AWS ALB rate-limit responses often have ``Content-Length: 0``. The Fern
raw client still calls ``_response.json()`` for that status, then wraps
the resulting ``JSONDecodeError`` in ``ApiError`` without ``from None``.
Python therefore prints both tracebacks.

Interception is the maintained ``pytest-httpx`` ``httpx_mock`` fixture on
the real ``BandLink`` REST client, matching ``test_link_credentials.py``.
``max_retries=0`` isolates the decode/chaining bug from Fern 429 retries.
"""

from __future__ import annotations

import traceback
from json.decoder import JSONDecodeError

import pytest
from band_rest.core.api_error import ApiError
from pytest_httpx import HTTPXMock

from band.platform.link import BandLink


async def test_empty_429_raises_api_error_without_json_decode_chain(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(status_code=429, content=b"")

    link = BandLink(agent_id="agent-1", api_key="test-key")
    with pytest.raises(ApiError) as exc_info:
        await link.rest.agent_api_identity.get_agent_me(
            request_options={"max_retries": 0},
        )

    error = exc_info.value
    formatted = "".join(traceback.format_exception(error))
    assert error.status_code == 429
    assert not isinstance(error.__cause__, JSONDecodeError)
    assert error.__suppress_context__ or not isinstance(
        error.__context__, JSONDecodeError
    )
    assert "JSONDecodeError" not in formatted
