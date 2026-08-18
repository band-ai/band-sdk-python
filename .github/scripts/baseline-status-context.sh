#!/usr/bin/env bash
# The commit-status context the nightly baseline posts and the release gate reads.
#
# Sourced by both sides (mark-commit-status.sh writes it, check-release-baseline.sh
# reads it) so the producer and the consumer cannot drift apart: a typo in either
# copy of a re-typed literal would fail *silently* — the gate would simply never find
# a status and block the release PR forever, with nothing to point at.
# shellcheck disable=SC2034  # consumed by the scripts that source this file
BASELINE_STATUS_CONTEXT="baseline-green"
