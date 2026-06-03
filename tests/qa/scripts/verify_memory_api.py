"""Read-only probe: does the QA Parlant agent REALLY have its memories
persisted server-side, and does read-by-id work?

Lists the agent's memories under every filter angle (status / scope / segment /
content_query), then re-fetches each by id, so we can tell apart:
  - store never persisted (nothing comes back under any filter), vs
  - list doesn't surface the agent's own memories (list empty but get-by-id works), vs
  - read-by-id is broken (record listed but get-by-id 403/404s).

Background: the QA E/I scenarios reported memory PARTIALs. Running this against
the agent's own API key showed the data the agent "stored" is NOT returned by
``list_agent_memories`` (a platform list-visibility/scoping issue), while the
one record that IS listed is an org/guideline memory the agent doesn't own and
so 403s on get-by-id. See scripts/README.md.

Read-only: lists and gets only — never writes.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import yaml

from thenvoi.client.rest import AsyncRestClient

CONFIG = Path(__file__).resolve().parents[1] / "adapters/parlant/agent_config.yaml"
CONFIG_KEY = "parlant_full_test"
NEEDLES = ["quinn", "teal", "green", "alpha", "bravo", "charlie"]
HOSTS = [
    os.environ.get("THENVOI_REST_URL"),
    "https://app.thenvoi.com",
    "https://app.band.ai",
]


def _short(v: object, n: int = 90) -> str:
    s = str(v)
    return s if len(s) <= n else s[: n - 1] + "…"


def _err(exc: Exception) -> str:
    sc = getattr(exc, "status_code", None)
    body = getattr(exc, "body", None)
    return f"{type(exc).__name__} status={sc} body={_short(body, 160)}"


async def _list(rest: AsyncRestClient, **filters: object) -> list:
    label = ", ".join(f"{k}={v}" for k, v in filters.items()) or "(no filters)"
    try:
        resp = await rest.agent_api_memories.list_agent_memories(
            page_size=50, **filters
        )
        data = getattr(resp, "data", None) or []
        print(f"  list[{label}] -> {len(data)} record(s)")
        return list(data)
    except Exception as exc:  # noqa: BLE001
        print(f"  list[{label}] -> {_err(exc)}")
        return []


async def probe(base_url: str, api_key: str) -> bool:
    print(f"\n===== HOST: {base_url} =====")
    rest = AsyncRestClient(api_key=api_key, base_url=base_url)

    # Try every angle the E/I secrets could be hiding under.
    all_records: dict[str, object] = {}
    variants: list[dict[str, object]] = [
        {},
        {"status": "active"},
        {"status": "superseded"},
        {"status": "archived"},
        {"scope": "subject"},
        {"scope": "organization"},
        {"segment": "user"},
        {"content_query": "Quinn"},
        {"content_query": "ALPHA"},
    ]
    for v in variants:
        for m in await _list(rest, **v):
            mid = getattr(m, "id", None)
            if mid:
                all_records[mid] = m

    print(f"\n  UNIQUE memory records across all filters: {len(all_records)}")
    if not all_records:
        return False

    for m in all_records.values():
        mid = getattr(m, "id", None)
        content = getattr(m, "content", "")
        status = getattr(m, "status", "?")
        system = getattr(m, "system", "?")
        seg = getattr(m, "segment", "?")
        hit = "  <-- MATCHES E/I SECRET" if any(
            n in str(content).lower() for n in NEEDLES
        ) else ""
        print(f"\n  • id={mid} [{status}/{system}/{seg}]{hit}")
        print(f"    content: {_short(content, 140)}")
        try:
            got = await rest.agent_api_memories.get_agent_memory(id=mid)
            gc = getattr(getattr(got, "data", None), "content", None)
            print(f"    get_agent_memory(id) -> OK  content={_short(gc, 80)}")
        except Exception as exc:  # noqa: BLE001
            print(f"    get_agent_memory(id) -> {_err(exc)}")

    secrets_present = any(
        any(n in str(getattr(m, "content", "")).lower() for n in NEEDLES)
        for m in all_records.values()
    )
    print(f"\n  >>> E/I secrets persisted server-side? {secrets_present}")
    return True


async def main() -> None:
    with open(CONFIG) as fh:
        cfg = yaml.safe_load(fh)
    entry = cfg[CONFIG_KEY]
    api_key = entry["api_key"]
    print(f"agent_id={entry['agent_id']}  config_key={CONFIG_KEY}")

    seen: set[str] = set()
    for host in HOSTS:
        if not host or host in seen:
            continue
        seen.add(host)
        found = await probe(host, api_key)
        if found:
            break


if __name__ == "__main__":
    asyncio.run(main())
