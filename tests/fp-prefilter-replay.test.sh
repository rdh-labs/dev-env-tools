#!/usr/bin/env bash
# GUARDS: fp_measure.py
# Population replay for fp_measure prefilters (rules/testing.md). Thin wrapper so tests-for-staged finds a *-replay.test.sh.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$here/fp_prefilter_replay.py" "$@"
