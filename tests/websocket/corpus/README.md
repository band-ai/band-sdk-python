# Vendored event-validation corpus

A snapshot of `band-sdk-core`'s event-validation corpus, vendored for
[INT-1236](https://linear.app/thenvoi/issue/INT-1236/integrate-band-sdk-core-event-validation-into-band-sdk-python)'s
migration proof (`tests/websocket/test_wire_corpus.py`).

Source: [`band-ai/band-sdk-core`](https://github.com/band-ai/band-sdk-core),
tag `band-sdk-core-py-v0.7.1`, commit `0b6b21240b8daf3284df2ec44de28cfa7beaa59c`
(`crates/core/tests/fixtures/corpus/`). See that repo's corpus `README.md` for
the file format, provenance, and how the `sdk.python`/`sdk.typescript`
contrast columns were captured.

**This snapshot is transient.** It exists to prove the `from_wire` migration
against real corpus data and is deleted once that proof lands and stays
green — ongoing corpus coverage lives in `band-sdk-core` itself, not here.
