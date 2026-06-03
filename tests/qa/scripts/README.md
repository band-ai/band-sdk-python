# QA diagnostic scripts

Small, standalone probes that talk to the platform's **memory API directly**,
using a QA agent's own API key. They exist to answer a question the scenario
harness can't on its own: *when a memory scenario comes back PARTIAL, is the
adapter at fault, or the platform?*

The scenario harness only sees what the agent says in chat ("I've stored that").
These scripts bypass the LLM and the adapter and call the REST client the SDK
uses (`band.client.rest.AsyncRestClient`), so a PARTIAL can be traced to a
specific endpoint.

## Why they were written

The Parlant E (memory lifecycle) and I (concurrent rooms) scenarios reported
PARTIALs. The draft root-cause was "store works but read-by-id 404s." Running
these probes against the agent's own key showed that was wrong:

- `store_memory` **works** — persists, returns a valid id, status `active`.
- `get` / `supersede` / `archive` **by id work** on a memory the agent owns
  (the live probe re-fetches and archives the exact id `store` returned).
- **`list_agent_memories` does NOT return the agent's own freshly-stored
  memories** — a just-written test memory and all E/I content are absent under
  every filter (status / scope / segment / content_query).
- The only record `list` does return is an unrelated org/`guideline` memory the
  agent doesn't own, which **403s** (`forbidden`) on get-by-id.

So the real issue is a **platform list-visibility / scoping** problem, not the
Parlant adapter. The LLM stores fine but its follow-up "list my memories / fetch
the one I stored" comes back empty, which breaks the store→list→get/supersede
workflow the scenarios exercise. (`app.band.ai` and `app.band.ai` resolve to
the same backend — identical data.)

## The scripts

| Script | Reads/Writes | What it tells you |
|---|---|---|
| `verify_memory_api.py` | **read-only** | Lists the agent's memories under 9 filter combinations and re-fetches each by id. Distinguishes "nothing stored" from "list can't see it" from "read-by-id broken". |
| `verify_memory_store.py` | **writes 1 test memory, then archives it** | Stores one clearly-marked `QA-VERIFY…` memory, then immediately re-lists and gets it by the returned id. Proves whether `store` persists and whether the new record shows up in `list`. |

## Running

Credentials come from the (gitignored) `tests/qa/adapters/parlant/agent_config.yaml`
under the `parlant_full_test` key. The config path resolves relative to the
script, so they run from anywhere. Host comes from `BAND_REST_URL` (falling
back to `https://app.band.ai`).

```bash
# read-only probe
BAND_REST_URL=https://app.band.ai .venv-parlant/bin/python tests/qa/scripts/verify_memory_api.py

# live write+read probe (creates + archives one test memory)
BAND_REST_URL=https://app.band.ai .venv-parlant/bin/python tests/qa/scripts/verify_memory_store.py
```

Any venv with the SDK installed works; `.venv-parlant` is just the one the QA
Parlant agent already uses. To target a different agent, change `CONFIG_KEY`.
