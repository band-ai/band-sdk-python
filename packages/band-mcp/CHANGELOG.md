# Changelog

## [2.0.0](https://github.com/band-ai/band-sdk-python/compare/band-mcp-v1.3.2...band-mcp-v2.0.0) (2026-08-23)


### ⚠ BREAKING CHANGES

* band-mcp no longer accepts BAND_API_KEY. Set BAND_USER_KEY (human scope) and/or BAND_AGENT_KEY (agent scope) explicitly -- there is no unscoped credential or prefix-inference fallback any more.

### Features

* consolidate band-mcp into one SDK-owned MCP engine (INT-1096) ([#552](https://github.com/band-ai/band-sdk-python/issues/552)) ([649f271](https://github.com/band-ai/band-sdk-python/commit/649f271ee486d9a3b3316361822b78d2253b08c4))
