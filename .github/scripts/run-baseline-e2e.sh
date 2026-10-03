#!/usr/bin/env bash
# Run each selected baseline cell once and preserve its first-attempt scorecard.
set -uo pipefail

: "${FINAL:?FINAL scorecard path is required}"

pytest_args=(-p no:rerunfailures)
if [ "${BAND_E2E_FIRST_ATTEMPT_DIAGNOSTICS:-false}" = "true" ]; then
  pytest_args+=(--log-cli-level=WARNING)
fi
if [ -n "${BAND_E2E_TEST_SELECTOR:-}" ]; then
  if [ "${BAND_E2E_FIRST_ATTEMPT_DIAGNOSTICS:-false}" != "true" ]; then
    echo "BAND_E2E_TEST_SELECTOR requires first-attempt diagnostics." >&2
    exit 2
  fi
  pytest_args+=(-k "$BAND_E2E_TEST_SELECTOR")
fi

BAND_E2E_SCORECARD_JSON="$FINAL" uv run pytest tests/e2e/baseline/ -v -s --no-cov "${pytest_args[@]}"
code=$?
if [ ! -f "$FINAL" ]; then
  echo "::error::baseline pytest produced no scorecard at $FINAL" >&2
  exit 1
fi
exit "$code"
