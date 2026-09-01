from __future__ import annotations

from band_sdk_core import SessionPolicy


def fast_session_policy(
    *, heartbeat_interval_s: float, dead_threshold_s: float
) -> SessionPolicy:
    """A SessionPolicy with real reconnect-backoff defaults (mirroring
    SessionPolicy.default()) but a fast heartbeat/dead-threshold pair, so
    watchdog/reconnect tests run in real fractional seconds instead of
    production's 30s/60s."""
    return SessionPolicy(
        {
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
            "heartbeat_interval_s": heartbeat_interval_s,
            "dead_threshold_s": dead_threshold_s,
        }
    )
