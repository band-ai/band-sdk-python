"""Live write+read probe of the memory path for the QA Parlant agent.

Stores ONE clearly-marked test memory, then immediately (a) lists to see if it
shows up and (b) get-by-the-returned-id, to disambiguate:
  - "store never persisted"            -> store errors, or get-by-id 404s, vs
  - "store works but list is broken"   -> store OK + get-by-id OK, but the new
                                          record is absent from list.

This is the script that proved the QA memory PARTIALs are a LIST-visibility bug:
store returns a valid id, get-by-id and archive on that id both succeed, yet the
freshly written memory never appears in list_agent_memories. See scripts/README.md.

WRITES: creates one test memory (marked "QA-VERIFY…"), then archives it. The
archive may itself fail harmlessly; the leftover is clearly labelled.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import yaml

from thenvoi.client.rest import AsyncRestClient, MemoryCreateRequest

CONFIG = Path(__file__).resolve().parents[1] / "adapters/parlant/agent_config.yaml"
CONFIG_KEY = "parlant_full_test"
HOST = os.environ.get("THENVOI_REST_URL", "https://app.thenvoi.com")
MARKER = "QA-VERIFY favorite color teal"


def _short(v: object, n: int = 180) -> str:
    s = str(v)
    return s if len(s) <= n else s[: n - 1] + "…"


def _err(exc: Exception) -> str:
    return (
        f"{type(exc).__name__} status={getattr(exc, 'status_code', None)} "
        f"body={_short(getattr(exc, 'body', None))}"
    )


async def main() -> None:
    with open(CONFIG) as fh:
        entry = yaml.safe_load(fh)[CONFIG_KEY]
    rest = AsyncRestClient(api_key=entry["api_key"], base_url=HOST)
    print(f"agent_id={entry['agent_id']}  host={HOST}")

    print("\n[1] count BEFORE store")
    before = getattr(await rest.agent_api_memories.list_agent_memories(page_size=50), "data", []) or []
    print(f"    {len(before)} record(s)")

    print("\n[2] store_memory(...)")
    new_id = None
    try:
        resp = await rest.agent_api_memories.create_agent_memory(
            memory=MemoryCreateRequest(
                content=MARKER,
                system="long_term",
                type="semantic",
                segment="user",
                thought="QA verification of memory persistence",
                scope="subject",
            )
        )
        rec = getattr(resp, "data", None)
        new_id = getattr(rec, "id", None)
        print(f"    -> OK  returned id={new_id}  status={getattr(rec, 'status', '?')}")
    except Exception as exc:  # noqa: BLE001
        print(f"    -> {_err(exc)}")

    print("\n[3] count AFTER store (did it persist + show in list?)")
    after = getattr(await rest.agent_api_memories.list_agent_memories(page_size=50), "data", []) or []
    print(f"    {len(after)} record(s)")
    persisted = any(MARKER.lower() in str(getattr(m, "content", "")).lower() for m in after)
    print(f"    marker visible in list? {persisted}")
    for m in after:
        flag = "  <-- our test write" if MARKER.lower() in str(getattr(m, "content", "")).lower() else ""
        print(f"      • {getattr(m, 'id', None)} [{getattr(m,'status','?')}] {_short(getattr(m,'content',''),60)}{flag}")

    if new_id:
        print(f"\n[4] get_agent_memory(id={new_id})  (the id store just returned)")
        try:
            got = await rest.agent_api_memories.get_agent_memory(id=new_id)
            print(f"    -> OK  content={_short(getattr(getattr(got,'data',None),'content',None),60)}")
        except Exception as exc:  # noqa: BLE001
            print(f"    -> {_err(exc)}")

        print(f"\n[5] cleanup: archive_agent_memory(id={new_id})")
        try:
            await rest.agent_api_memories.archive_agent_memory(id=new_id)
            print("    -> archived OK")
        except Exception as exc:  # noqa: BLE001
            print(f"    -> {_err(exc)}  (leaving test memory; harmless, clearly marked)")


if __name__ == "__main__":
    asyncio.run(main())
