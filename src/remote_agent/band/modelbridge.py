"""Bounded structured model command, configured by the operator, with no personal CLI dependency."""

from __future__ import annotations

from remote_agent.band.configuration import credential, model_config
from remote_agent.commands import CommandError, capture, result_from_capture
from remote_agent.environment import minimal_environment
from remote_agent.types import JsonObject, SourcePath


def invoke(
    root: SourcePath,
    prompt: str,
    schema: JsonObject,
    *,
    configuration: str | None = None,
    timeout: float = 90,
    timeout_limit: int = 180,
) -> JsonObject:
    # The caller's SQLite journals retain requests/results with their own
    # retention and ephemeral policy. Do not write a parallel raw prompt log.
    del root
    if configuration is None:
        raise ValueError("Structured model configuration is required")
    if type(timeout_limit) is not int or not 1 <= timeout_limit <= 900:
        raise ValueError(
            "Model timeout limit must be an integer from 1 through 900 seconds"
        )
    policy = model_config(configuration)
    environment = minimal_environment()
    environment.update(
        {name: credential(path) for name, path in policy["credentials"].items()}
    )
    request = {
        "profile": "band-contract/structured-model-request/1",
        "prompt": prompt,
        "schema": schema,
    }
    execution = capture(
        policy["command"],
        request,
        timeout=min(timeout, timeout_limit),
        max_bytes=524288,
        max_input_bytes=1048576,
        env=environment,
    )
    if execution.get("failure") == "Callback timeout":
        raise ValueError(
            "Structured model deadline exceeded before a complete result was available"
        )
    try:
        result = result_from_capture(execution)
        # Installed only for the optional Band host; the core runtime remains
        # dependency-free. Schema errors never echo model output or secrets.
        from jsonschema import Draft202012Validator  # noqa: PLC0415 - optional contracts dependency

        Draft202012Validator(schema).validate(result)
        return result
    except (CommandError, ValueError, RecursionError):
        raise ValueError("Structured model result unavailable or invalid") from None
    except ImportError:
        raise ValueError("Install the hash-locked Band host dependencies") from None
    except Exception:  # noqa: BLE001 -- Schema-library failures must not echo model output or secrets.
        raise ValueError("Structured model result did not match its schema") from None
