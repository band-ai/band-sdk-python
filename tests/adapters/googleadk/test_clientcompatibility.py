"""Construct native Google clients with compatible headers and backend routing."""

from __future__ import annotations

from typing import Any

import pytest
from google.auth.exceptions import DefaultCredentialsError
from pytest_httpx import HTTPXMock

from band.core.protocols import GENERIC_PROVIDER_FAILURE_MESSAGE
from band.runtime.tools import BandTool
from band.testing import failure_reports
from tests.adapters.genaikit import text_reply, tool_call
from tests.adapters.googleadk.bridge import GoogleADKAdapter, GoogleADKAdapterConfig
from tests.adapters.googleadk.clientkit import (
    AUTH_FAILURE,
    ENDPOINT_MODEL,
    GEMINI_MODEL,
    PUBLISHER_MODEL,
    TRACKING_HEADERS,
    tracking_headers,
)
from tests.adapters.googleadk.httppeer import EFFECT_ARGS, RecoveryTools
from tests.framework_conformance.turnprobes import turn_input


@pytest.mark.usefixtures("google_environment")
class TestClientCompatibility:
    @pytest.mark.parametrize(
        "header_shape",
        [property(tracking_headers), tracking_headers],
        ids=["property", "method"],
        indirect=True,
    )
    async def test_tracking_headers_reach_native_http_request(
        self, native_model: Any, header_shape: None, httpx_mock: HTTPXMock
    ) -> None:
        httpx_mock.add_response(json=text_reply("Hello").model_dump(mode="json"))
        client = native_model.api_client
        await client.aio.models.generate_content(model=GEMINI_MODEL, contents="Hello")
        request = httpx_mock.get_request()
        assert request is not None
        for key, value in TRACKING_HEADERS.items():
            assert value in request.headers[key]

    async def test_native_runner_completes_a_tool_and_reply_turn(
        self, httpx_mock: HTTPXMock, closed_google_clients: list[Any]
    ) -> None:
        httpx_mock.add_response(
            json=tool_call(BandTool.SEND_MESSAGE, EFFECT_ARGS).model_dump(mode="json")
        )
        httpx_mock.add_response(json=text_reply("Done").model_dump(mode="json"))
        adapter = GoogleADKAdapter()
        await adapter.on_started("TestBot", "Test bot")
        tools = RecoveryTools()
        await adapter.on_event(turn_input(tools))
        assert tools.chat == [EFFECT_ARGS["content"]]
        assert failure_reports(tools) == []
        assert len(httpx_mock.get_requests()) == 2
        assert closed_google_clients
        assert all(client.is_closed for client in closed_google_clients)

    @pytest.mark.parametrize(
        ("native_model", "backend_env", "vertexai", "host", "path"),
        [
            *[
                pytest.param(
                    model,
                    env,
                    True,
                    "us-central1-aiplatform.googleapis.com",
                    f"/v1beta1/{model}:generateContent",
                    id=f"{name}-{env}",
                )
                for model, name in [
                    (PUBLISHER_MODEL, "publisher"),
                    (ENDPOINT_MODEL, "endpoint"),
                ]
                for env in [None, "false", "true"]
            ],
            pytest.param(
                GEMINI_MODEL,
                None,
                False,
                "generativelanguage.googleapis.com",
                f"/v1beta/models/{GEMINI_MODEL}:generateContent",
                id="ordinary-unset",
            ),
            pytest.param(
                GEMINI_MODEL,
                "false",
                False,
                "generativelanguage.googleapis.com",
                f"/v1beta/models/{GEMINI_MODEL}:generateContent",
                id="ordinary-false",
            ),
            pytest.param(
                GEMINI_MODEL,
                "true",
                True,
                "us-central1-aiplatform.googleapis.com",
                f"/v1beta1/projects/offline/locations/us-central1/publishers/google/models/{GEMINI_MODEL}:generateContent",
                id="ordinary-true",
            ),
        ],
        indirect=["native_model", "backend_env"],
    )
    async def test_native_backend_and_destination_follow_model_and_environment(
        self,
        native_model: Any,
        backend_env: None,
        vertexai: bool,
        host: str,
        path: str,
        httpx_mock: HTTPXMock,
    ) -> None:
        httpx_mock.add_response(json=text_reply("Hello").model_dump(mode="json"))
        client = native_model.api_client
        assert bool(client.vertexai) is vertexai
        await client.aio.models.generate_content(
            model=native_model.model, contents="Hello"
        )
        request = httpx_mock.get_request()
        assert request is not None
        assert (request.url.host, request.url.path) == (host, path)
        transport = native_model.httpx_client
        await native_model.aclose()
        assert transport.is_closed

    @pytest.mark.usefixtures("missing_google_credentials")
    async def test_missing_vertex_credentials_fail_without_fallback_and_close_transport(
        self, closed_google_clients: list[Any], httpx_mock: HTTPXMock
    ) -> None:
        adapter = GoogleADKAdapter(GoogleADKAdapterConfig(model=PUBLISHER_MODEL))
        await adapter.on_started("TestBot", "Test bot")
        tools = RecoveryTools()
        with pytest.raises(DefaultCredentialsError, match=AUTH_FAILURE):
            await adapter.on_event(turn_input(tools))
        assert httpx_mock.get_requests() == []
        assert failure_reports(tools) == [
            ("google_adk", GENERIC_PROVIDER_FAILURE_MESSAGE)
        ]
        assert closed_google_clients
        assert all(client.is_closed for client in closed_google_clients)
