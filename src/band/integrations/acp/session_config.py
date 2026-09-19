"""Apply a remote ACP session's advertised select configuration."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TypeAlias

from acp.schema import (
    SessionConfigOptionBoolean,
    SessionConfigOptionSelect,
    SessionConfigSelectGroup,
    SessionConfigSelectOption,
    SetSessionConfigOptionResponse,
)

SessionConfigOption: TypeAlias = SessionConfigOptionSelect | SessionConfigOptionBoolean
SessionConfigSelections: TypeAlias = Mapping[str, str | None]
SessionConfigSetter: TypeAlias = Callable[
    [str, str, str], Awaitable[SetSessionConfigOptionResponse | None]
]
SessionConfigResolver: TypeAlias = Callable[
    ["ACPConfigRequest"], Awaitable[SessionConfigSelections | None]
]

SESSION_CONFIG_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class ACPConfigRequest:
    """The live configuration catalog advertised for one ACP session."""

    room_id: str
    session_id: str
    config_options: tuple[SessionConfigOption, ...]


class ACPConfigError(RuntimeError):
    """A requested ACP session configuration could not be applied."""

    def __init__(
        self,
        *,
        session_id: str,
        option_id: str,
        selected_value: str,
        message: str,
    ) -> None:
        super().__init__(message)
        self.session_id = session_id
        self.option_id = option_id
        self.selected_value = selected_value


def flatten_select_options(
    options: Sequence[SessionConfigSelectOption | SessionConfigSelectGroup],
) -> tuple[SessionConfigSelectOption, ...]:
    """Flatten an ACP select option's direct values or named groups."""
    flattened: list[SessionConfigSelectOption] = []
    for entry in options:
        if isinstance(entry, SessionConfigSelectGroup):
            flattened.extend(entry.options)
        else:
            flattened.append(entry)
    return tuple(flattened)


async def apply_session_config_selections(
    *,
    session_id: str,
    config_options: Sequence[SessionConfigOption],
    selections: SessionConfigSelections,
    set_option: SessionConfigSetter,
) -> tuple[SessionConfigOption, ...]:
    """Apply selections in order, revalidating against each returned catalog."""
    catalog = tuple(config_options)
    if not isinstance(selections, Mapping):
        raise ACPConfigError(
            session_id=session_id,
            option_id="resolver",
            selected_value="",
            message="ACP session configuration resolver must return a mapping.",
        )

    for option_id, selected_value in selections.items():
        if selected_value is None:
            continue

        if not isinstance(selected_value, str):
            raise ACPConfigError(
                session_id=session_id,
                option_id=option_id,
                selected_value=str(selected_value),
                message=f'ACP config value for option "{option_id}" must be a string.',
            )

        option = next((entry for entry in catalog if entry.id == option_id), None)
        if not isinstance(option, SessionConfigOptionSelect):
            raise ACPConfigError(
                session_id=session_id,
                option_id=option_id,
                selected_value=selected_value,
                message=f'ACP config option "{option_id}" is not available.',
            )

        if selected_value == option.current_value:
            continue

        available_values = {
            entry.value for entry in flatten_select_options(option.options)
        }
        if selected_value not in available_values:
            raise ACPConfigError(
                session_id=session_id,
                option_id=option_id,
                selected_value=selected_value,
                message=(
                    f'ACP config value "{selected_value}" is not advertised '
                    f'for option "{option_id}".'
                ),
            )

        try:
            response = await asyncio.wait_for(
                set_option(session_id, option_id, selected_value),
                timeout=SESSION_CONFIG_TIMEOUT_SECONDS,
            )
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except TimeoutError as error:
            raise ACPConfigError(
                session_id=session_id,
                option_id=option_id,
                selected_value=selected_value,
                message=(
                    f'ACP config option "{option_id}" did not respond within '
                    f"{SESSION_CONFIG_TIMEOUT_SECONDS} seconds."
                ),
            ) from error
        except Exception as error:
            raise ACPConfigError(
                session_id=session_id,
                option_id=option_id,
                selected_value=selected_value,
                message=str(error),
            ) from error

        if response is None or not isinstance(response.config_options, list):
            raise ACPConfigError(
                session_id=session_id,
                option_id=option_id,
                selected_value=selected_value,
                message=(
                    f'ACP config option "{option_id}" did not return a refreshed '
                    "catalog."
                ),
            )
        catalog = tuple(response.config_options)

    return catalog
