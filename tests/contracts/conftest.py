"""Keep portable coverage active and require native prerequisites in qualification."""

from __future__ import annotations

import pytest

from tests.contracts.requirements import CAPABILITIES, missing


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    strict = config.getoption("--contract-qualification")
    unavailable: dict[str, list[str]] = {}
    for item in items:
        reasons = []
        for marker in item.iter_markers():
            if marker.name not in CAPABILITIES:
                continue
            if (
                marker.kwargs
                or (marker.args and marker.name != "contract_engine")
                or len(marker.args) > 1
            ):
                raise pytest.UsageError(
                    f"Invalid contract prerequisite marker on {item.nodeid}: {marker}"
                )
            reason = missing(
                marker.name, engine=marker.args[0] if marker.args else "current"
            )
            if reason:
                reasons.append(reason)
        if reasons:
            explanation = " ".join(dict.fromkeys(reasons))
            if strict:
                unavailable.setdefault(explanation, []).append(item.nodeid)
            else:
                item.add_marker(pytest.mark.skip(reason=explanation))
    if unavailable:
        details = "\n".join(
            reason + "\n  " + "\n  ".join(nodeids)
            for reason, nodeids in unavailable.items()
        )
        raise pytest.UsageError(
            "Contract qualification prerequisites are missing:\n" + details
        )
