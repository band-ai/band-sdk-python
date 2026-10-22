# Pinned text boundaries

Contract text and discovery description limits use Unicode 16.0.0 **extended**
grapheme clusters, following [UAX #29 revision 45](https://www.unicode.org/reports/tr29/tr29-45.html).
They do not use the host runtime's changing tables. In particular, GB9c joins
Indic conjuncts and later Unicode additions retain their Unicode 16 properties.

The upstream property files, official boundary test corpus and Unicode license
are retained here. `manifest.json` records each original URL and SHA-256. The
implementation compiles sorted property ranges from those exact files. Tests
verify the hashes and all 1,093 official boundary vectors on both supported
Elixir versions. No network access is needed to build or test these tables.

Changing the Unicode version is a contract compatibility change; update the
specification, identities, conformance corpus and migration policy together.
