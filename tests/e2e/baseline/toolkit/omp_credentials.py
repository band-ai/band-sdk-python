"""Shared OMP credential lookup from baseline settings."""

from __future__ import annotations

from band.integrations.omp import (
    DEFAULT_OMP_ACP_COMMAND,
    DEFAULT_OMP_MODEL,
    omp_model_provider,
    omp_provider_api_key_env,
)
from tests.e2e.baseline.settings import BaselineSettings


def omp_model(settings: BaselineSettings) -> str:
    """The exact model the baseline passes to OMP's documented ``--model`` flag."""
    return settings.backends.omp_model.strip() or DEFAULT_OMP_MODEL


def omp_command(settings: BaselineSettings) -> tuple[str, ...]:
    """The executable and arguments used by every baseline OMP probe."""
    override = settings.backends.omp_command.strip()
    return tuple(override.split()) if override else DEFAULT_OMP_ACP_COMMAND


def omp_provider_api_key(settings: BaselineSettings) -> str:
    """The provider API key ``OMP_MODEL`` needs, or ``""`` when unset/unsupported.

    ``Dep.OMP`` calls this to decide test availability, so a malformed or
    unsupported ``OMP_MODEL`` must fall through to ``""`` (an unavailable dep)
    rather than raise and crash that evaluation.
    """
    model = omp_model(settings)
    try:
        env_key = omp_provider_api_key_env(omp_model_provider(model))
    except ValueError:
        return ""
    creds = settings.llm_credentials
    match env_key:
        case "ANTHROPIC_API_KEY":
            return creds.anthropic_api_key
        case "OPENAI_API_KEY":
            return creds.openai_api_key
        case "GEMINI_API_KEY":
            return creds.gemini_api_key or creds.google_api_key
        case _:
            return ""
