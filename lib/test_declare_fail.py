#!/usr/bin/env python3
"""Unit tests for the declare_fail reconciliation core.

Run: python3 lib/test_declare_fail.py   (exit 0 = all pass)
Covers both input modes, the exit-code contract, and the --validate gate —
the contract that the four adapters depend on.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from declare_fail import (  # noqa: E402
    diff_mappings,
    check_violations,
    validate_input,
    render_json,
    render_report,
    MODE_MAPPING,
    MODE_VIOLATIONS,
)

_failures = []


def check(name: str, cond: bool) -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")
    if not cond:
        _failures.append(name)


# --- mapping mode ---------------------------------------------------------
def test_mapping_clean():
    r = diff_mappings({"a": 1, "b": 2}, {"a": 1, "b": 2})
    check("mapping: identical -> ok", r.ok)
    check("mapping: identical -> no missing/extra/mismatch",
          not (r.missing or r.extra or r.mismatched))


def test_mapping_missing():
    r = diff_mappings({"a": 1, "b": 2}, {"a": 1})
    check("mapping: missing key -> fail", not r.ok)
    check("mapping: missing key reported", r.missing == ["b"])


def test_mapping_extra_strict_vs_ignore():
    strict = diff_mappings({"a": 1}, {"a": 1, "z": 9})
    check("mapping: extra key fails by default", not strict.ok and strict.extra == ["z"])
    relaxed = diff_mappings({"a": 1}, {"a": 1, "z": 9}, ignore_extra=True)
    check("mapping: extra key ok with ignore_extra", relaxed.ok)


def test_mapping_mismatch():
    r = diff_mappings({"f": "hash_committed"}, {"f": "hash_regenerated"})
    check("mapping: value mismatch -> fail", not r.ok)
    check("mapping: mismatch reported with both values",
          r.mismatched == [{"key": "f", "expected": "hash_committed", "actual": "hash_regenerated"}])


# --- violations mode ------------------------------------------------------
def test_violations_clean():
    r = check_violations([])
    check("violations: empty -> ok", r.ok)


def test_violations_fail():
    r = check_violations([{"id": "x", "message": "boom", "file": "a.ts"}])
    check("violations: non-empty -> fail", not r.ok)
    check("violations: item passed through", r.violations[0]["file"] == "a.ts")


def test_violations_malformed_raises():
    # Contract: a malformed item is a tool error (ValueError -> exit 2),
    # enforced even without --validate, so render_report never KeyErrors into
    # an exit-1 that masquerades as a legitimate "violations found".
    try:
        check_violations([{"message": "no id"}])
        check("violations: malformed item raises (unconditional contract)", False)
    except ValueError:
        check("violations: malformed item raises (unconditional contract)", True)


# --- validate gate --------------------------------------------------------
def test_validate_mapping():
    validate_input({"k": 1}, MODE_MAPPING)  # ok
    check("validate: good mapping passes", True)
    try:
        validate_input(["not", "a", "dict"], MODE_MAPPING)
        check("validate: list rejected in mapping mode", False)
    except ValueError:
        check("validate: list rejected in mapping mode", True)


def test_validate_violations():
    validate_input([{"id": "a", "message": "m"}], MODE_VIOLATIONS)  # ok
    check("validate: good violations pass", True)
    try:
        validate_input([{"message": "no id"}], MODE_VIOLATIONS)
        check("validate: missing 'id' rejected", False)
    except ValueError:
        check("validate: missing 'id' rejected", True)


# --- rendering ------------------------------------------------------------
def test_render_stable_json():
    import json
    r = diff_mappings({"a": 1}, {})
    parsed = json.loads(render_json(r))
    check("render_json: parses + carries counts", parsed["counts"]["missing"] == 1)
    check("render_json: ok flag present", parsed["ok"] is False)


def test_render_report_violation():
    r = check_violations([{"id": "dup", "message": "globbed twice"}])
    rpt = render_report(r, label="testMatch")
    check("render_report: labelled + names violation", "[testMatch]" in rpt and "dup" in rpt)


if __name__ == "__main__":
    for fn in [
        test_mapping_clean, test_mapping_missing, test_mapping_extra_strict_vs_ignore,
        test_mapping_mismatch, test_violations_clean, test_violations_fail,
        test_violations_malformed_raises,
        test_validate_mapping, test_validate_violations, test_render_stable_json,
        test_render_report_violation,
    ]:
        print(fn.__name__)
        fn()
    print()
    if _failures:
        print(f"{len(_failures)} FAILED: {_failures}")
        sys.exit(1)
    print("ALL PASS")
    sys.exit(0)
