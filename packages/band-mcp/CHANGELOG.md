# Changelog

## [2.2.0](https://github.com/band-ai/band-sdk-python/compare/band-mcp-v2.1.0...band-mcp-v2.2.0) (2026-09-04)


### Features

* Capability.FILES support across every adapter ([#600](https://github.com/band-ai/band-sdk-python/issues/600)) ([2488008](https://github.com/band-ai/band-sdk-python/commit/2488008b03872b0c53c07fc227fbf06ad16c9ead))


### Bug Fixes

* bump band-mcp's band-sdk floor to 3.0.0 ([#582](https://github.com/band-ai/band-sdk-python/issues/582)) ([4dce083](https://github.com/band-ai/band-sdk-python/commit/4dce0830918f081dccccf5b7af4f2c05a2f16299))

## [2.1.0](https://github.com/band-ai/band-sdk-python/compare/band-mcp-v2.0.1...band-mcp-v2.1.0) (2026-08-26)


### ⚠ BREAKING CHANGES

* AgentTools.get_tool_schemas/get_anthropic_tool_schemas/ get_openai_tool_schemas, iter_tool_definitions, and the MCP engine's registration builders replace their separate include_memory/include_contacts booleans with a single capabilities: frozenset[Capability] | None parameter.

### Features

* add file transfer capability to the Python SDK (INT-1261) ([#573](https://github.com/band-ai/band-sdk-python/issues/573)) ([eb2a404](https://github.com/band-ai/band-sdk-python/commit/eb2a404c3220ab52f2cbd7c300e405f6f3d1149a))


### Miscellaneous Chores

* **band-mcp:** pin next release to 2.1.0 ([#581](https://github.com/band-ai/band-sdk-python/issues/581)) ([547615a](https://github.com/band-ai/band-sdk-python/commit/547615a767fceb90b3c2ef0dd495a9b778ca8717))

## [2.0.1](https://github.com/band-ai/band-sdk-python/compare/band-mcp-v2.0.0...band-mcp-v2.0.1) (2026-08-23)


### Bug Fixes

* require band-sdk&gt;=2.1.0 in band-mcp ([#564](https://github.com/band-ai/band-sdk-python/issues/564)) ([50f8d0a](https://github.com/band-ai/band-sdk-python/commit/50f8d0a4d27c3ad0d13cb6c1a8f4d602045614fa))

## [2.0.0](https://github.com/band-ai/band-sdk-python/compare/band-mcp-v1.3.2...band-mcp-v2.0.0) (2026-08-23)


### ⚠ BREAKING CHANGES

* band-mcp no longer accepts BAND_API_KEY. Set BAND_USER_KEY (human scope) and/or BAND_AGENT_KEY (agent scope) explicitly -- there is no unscoped credential or prefix-inference fallback any more.

### Features

* consolidate band-mcp into one SDK-owned MCP engine (INT-1096) ([#552](https://github.com/band-ai/band-sdk-python/issues/552)) ([649f271](https://github.com/band-ai/band-sdk-python/commit/649f271ee486d9a3b3316361822b78d2253b08c4))
