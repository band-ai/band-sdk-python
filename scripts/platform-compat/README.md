# Platform compatibility smoke

Run `scripts/platform-compat/run.sh` inside the same CI container as the ephemeral
platform. Requires Bash, Python 3, uv, package-download access and explicit
`BAND_REST_URL`, `BAND_BASE_URL`, `BAND_WS_URL`, `BAND_API_KEY_USER`, `BAND_API_KEY`,
`TEST_AGENT_ID`, and `RESULTS_DIR`. Credentials must be disposable seeded test
identities. Only loopback HTTP/WS endpoints are accepted; there is no prod default.

The wrapper creates a fresh Python 3.12 environment and installs the public
`band-sdk==3.2.1` wheel. `BAND_SDK_VERSION` selects another published version.
The SDK checkout supplies the harness and existing `UserOps` helper, never the
installed `band` package. No release-baseline code runs.

`sdk-room-roundtrip` creates a room, adds the seeded agent, starts a deterministic
SDK adapter, sends a uniquely identified user probe, and verifies that the SDK
receives it and persists the matching agent reply. The scenario deadline is 90
seconds; each cleanup operation has another 15 seconds. It deletes its room and
stops the agent even on failure. It does not delete the externally seeded identities.

This proves runtime messaging only, without model calls or published quickstart
coverage. The platform workflow separately records its commit, billing-disabled
deployment mode, stack cleanup and harness commit.

`results.json` contains a `scenarios` array and `junit.xml` contains one testcase.
Setup and timeouts report `incomplete`, runtime errors report `fail`, and any
nonpass exits nonzero. A fallback report is written before installing packages so
installation or import failures cannot silently omit the result. Cleanup failures
prevent a pass. Result reasons omit raw API error bodies and credentials.
