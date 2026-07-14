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

import re
from pathlib import Path

import conftest

_REPO = Path(__file__).resolve().parent
_CI = _REPO / ".github" / "workflows" / "ci.yml"


def test_every_collect_ignore_entry_is_run_by_ci() -> None:
    assert _CI.exists(), f"{_CI} is missing — the CI half of this contract cannot be checked."
    workflow = _CI.read_text(encoding="utf-8")
    for entry in conftest.collect_ignore:
        assert (_REPO / entry).exists(), (
            f"conftest.collect_ignore references {entry!r}, which does not exist. "
            "A stale entry silently excludes nothing and hides a rename."
        )
        assert _runs_in_ci(workflow, entry), (
            f"{entry!r} is excluded from pytest collection but no ci.yml `run:` command executes it. "
            "Excluded from the collector must never mean excluded from CI — "
            "add a `run:` step for it, or drop the collect_ignore entry."
        )


def _runs_in_ci(workflow: str, entry: str) -> bool:
    """Whether a `run:` COMMAND names entry — comments and near-misses excluded.

    A bare `entry in workflow` passes on the path appearing in a comment, a step
    name, or as a prefix of a longer path (foo.py.bak). That inert-text
    false-positive is the same one that makes grepping a workflow unsafe:
    quarterly-security-review.yml carries an `npm test` inside a github-script
    literal that executes nothing.

    Deliberately a tripwire, not proof of coverage. It does NOT detect a step
    neutered by `continue-on-error: true`, `if: false`, or a trailing `|| true`,
    and it only reads single-line `run:` commands — a `run: |` block would slip
    through. Catching those needs a real YAML parse, which needs PyYAML, which
    would break this repo's pytest-is-the-sole-dependency invariant. That trade
    is deliberate: this closes the common drift (entry added, step forgotten),
    not every path to a silent orphan.
    """
    word = re.compile(rf"(?<![\w./-]){re.escape(entry)}(?![\w./-])")
    for line in workflow.splitlines():
        if not re.match(r"\s*run:", line):
            continue
        command = line.split("#", 1)[0]  # a trailing comment is not an execution
        if word.search(command):
            return True
    return False
