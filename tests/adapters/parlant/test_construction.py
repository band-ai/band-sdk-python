"""ParlantAdapter construction and config validation.

Shared adapter behavior (defaults, custom kwargs, history_converter) lives in
tests/framework_conformance/test_adapter_conformance.py.
"""

from __future__ import annotations

import pytest

from band.adapters.parlant import ParlantAdapter, ParlantAdapterConfig


def test_borrowed_server_and_agent_are_exposed(mock_parlant_server, mock_parlant_agent):
    adapter = ParlantAdapter(
        server=mock_parlant_server, parlant_agent=mock_parlant_agent
    )

    assert adapter.server is mock_parlant_server
    assert adapter.parlant_agent is mock_parlant_agent


def test_owned_server_and_agent_are_unavailable_before_start():
    adapter = ParlantAdapter()

    with pytest.raises(RuntimeError, match="not running yet"):
        _ = adapter.server
    with pytest.raises(RuntimeError, match="not created yet"):
        _ = adapter.parlant_agent


@pytest.mark.parametrize(
    "config",
    [
        ParlantAdapterConfig(system_prompt="You are a custom assistant."),
        ParlantAdapterConfig(custom_section="Be helpful."),
    ],
)
def test_prompt_params_rejected_with_borrowed_agent(
    mock_parlant_server, mock_parlant_agent, config
):
    """system_prompt/custom_section only shape an adapter-created agent."""
    with pytest.raises(ValueError, match="parlant_agent"):
        ParlantAdapter(
            config, server=mock_parlant_server, parlant_agent=mock_parlant_agent
        )


def test_borrowed_agent_requires_its_server(mock_parlant_agent):
    with pytest.raises(ValueError, match="requires the server"):
        ParlantAdapter(parlant_agent=mock_parlant_agent)


@pytest.mark.parametrize(
    "owned_server_kwargs",
    [{"nlp_service": "svc"}, {"server_options": {"host": "127.0.0.1"}}],
)
def test_owned_server_options_rejected_with_borrowed_server(
    mock_parlant_server, owned_server_kwargs
):
    with pytest.raises(ValueError, match="caller-provided server"):
        ParlantAdapter(server=mock_parlant_server, **owned_server_kwargs)


@pytest.mark.parametrize("field", ["response_timeout", "response_poll"])
@pytest.mark.parametrize("value", [0, -1.0])
def test_config_rejects_non_positive_response_budget(field, value):
    with pytest.raises(ValueError, match="greater than 0"):
        ParlantAdapterConfig(**{field: value})
