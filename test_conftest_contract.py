#!/usr/bin/env python3
"""Guards the conftest.py <-> ci.yml contract.

`collect_ignore` removes a file from pytest discovery, so the only thing that
still runs it is an explicit CI step. Nothing couples the two: adding an entry
and forgetting the step exits 0 and green, and the file becomes a silent
orphan — the drift class this repo's tooling exists to catch, reintroduced by
the exclusion list itself.

This asserts the coupling, so the list cannot drift from the workflow.
"""
from __future__ import annotations

from pathlib import Path

import conftest

_REPO = Path(__file__).resolve().parent
_CI = _REPO / ".github" / "workflows" / "ci.yml"


def test_every_collect_ignore_entry_is_run_by_ci() -> None:
    workflow = _CI.read_text(encoding="utf-8")
    for entry in conftest.collect_ignore:
        assert (_REPO / entry).exists(), (
            f"conftest.collect_ignore references {entry!r}, which does not exist. "
            "A stale entry silently excludes nothing and hides a rename."
        )
        assert entry in workflow, (
            f"{entry!r} is excluded from pytest collection but no ci.yml step runs it. "
            "Excluded from the collector must never mean excluded from CI — "
            "add a `run:` step for it, or drop the collect_ignore entry."
        )
