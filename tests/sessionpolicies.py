"""``SessionPolicy`` builders shared by the transport tests."""

from __future__ import annotations

from band_sdk_core import SessionPolicy

# Mirrors SessionPolicy.default(), which exposes no field getters to copy from.
DEFAULT_POLICY_FIELDS: dict[str, float] = {
    "base_delay_s": 1.0,
    "factor": 2.0,
    "max_delay_s": 30.0,
    "stable_reset_s": 60.0,
    "rapid_disconnect_uptime_s": 10.0,
    "rapid_window_s": 300.0,
    "rapid_first_min_delay_s": 1.0,
    "rapid_second_min_delay_s": 5.0,
    "rapid_cooldown_base_s": 10.0,
    "rapid_cooldown_step_s": 10.0,
    "rapid_cooldown_max_s": 60.0,
    "rapid_threshold": 10,
    "heartbeat_interval_s": 30.0,
    "dead_threshold_s": 60.0,
}


# Long after the last retry any policy built here could have scheduled.
BEYOND_ANY_RETRY_S = 2 * DEFAULT_POLICY_FIELDS["max_delay_s"]


def session_policy(**overrides: float) -> SessionPolicy:
    """The default policy with ``overrides`` applied."""
    unknown = overrides.keys() - DEFAULT_POLICY_FIELDS.keys()
    if unknown:
        raise ValueError(f"Unknown SessionPolicy fields: {sorted(unknown)}")
    return SessionPolicy({**DEFAULT_POLICY_FIELDS, **overrides})


def millisecond_backoff(**overrides: float) -> SessionPolicy:
    """Reconnects after a millisecond and never counts a drop as rapid, for
    tests that reconnect on the real clock."""
    return session_policy(
        base_delay_s=0.001,
        max_delay_s=0.001,
        rapid_disconnect_uptime_s=0.0,
        **overrides,
    )
