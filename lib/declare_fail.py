#!/usr/bin/env python3
"""declare_fail — shared reconciliation core for QC-enforcement adapters.

One exit-code + report contract, two input modes:

  * ``mapping``    — compare an EXPECTED mapping against an ACTUAL mapping.
                     Drift = keys expected-but-missing, keys actual-but-extra,
                     or values that mismatch. Used by the orphaned-test and
                     codegen-sync adapters.
  * ``violations`` — a pre-computed list of violation objects; a non-empty
                     list is a failure. Used by the testMatch mutual-exclusion
                     lint (which produces violations natively, not a mapping).

Exit codes (CLI):
  0  clean  — no drift / no violations
  1  fail   — drift or violations found
  2  error  — bad input, schema-validation failure, or tool error

Design invariant: **no repo-specific logic lives here.** Adapters build the
expected/actual mappings or the violations list (that is where all
framework/repo knowledge goes); this core only diffs, formats, and exit-codes.
That keeps the four adapters thin and the drift logic tested in exactly one
place — the fix for the "implicit adapter/core JSON contract drifts silently"
risk is the ``validate_input`` gate plus the ``schemas/`` definitions.

Library API (import ``lib.declare_fail``):
  diff_mappings(expected, actual, ignore_extra=False) -> DiffResult
  check_violations(violations)                        -> DiffResult
  validate_input(payload, mode)                       -> None   (raises ValueError)
  render_json(result)                                 -> str
  render_report(result, label=None)                   -> str

CLI:
  declare-fail --mode mapping     --expected E.json --actual A.json [--ignore-extra]
  declare-fail --mode violations  --violations V.json
  (add --validate to enforce the schema before diffing; --label NAME for the report)
  Read a path or "-" for stdin.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

EXIT_CLEAN = 0
EXIT_FAIL = 1
EXIT_ERROR = 2

MODE_MAPPING = "mapping"
MODE_VIOLATIONS = "violations"


@dataclass
class DiffResult:
    """Outcome of a reconciliation. ``ok`` is the single source of the verdict."""

    mode: str
    ok: bool
    # mapping mode
    missing: List[str] = field(default_factory=list)      # expected, absent from actual
    extra: List[str] = field(default_factory=list)        # present in actual, not expected
    mismatched: List[Dict[str, Any]] = field(default_factory=list)  # {key, expected, actual}
    # violations mode
    violations: List[Dict[str, Any]] = field(default_factory=list)

    def counts(self) -> Dict[str, int]:
        return {
            "missing": len(self.missing),
            "extra": len(self.extra),
            "mismatched": len(self.mismatched),
            "violations": len(self.violations),
        }


def diff_mappings(
    expected: Dict[str, Any],
    actual: Dict[str, Any],
    ignore_extra: bool = False,
) -> DiffResult:
    """Compare two mappings. Keys are the reconciliation identity.

    ``missing``    — keys in ``expected`` not in ``actual`` (the common drift:
                     e.g. a test file that no CI step reaches).
    ``extra``      — keys in ``actual`` not in ``expected`` (a step referencing
                     something that should not exist). Counts as drift unless
                     ``ignore_extra``.
    ``mismatched`` — keys in both whose values differ (e.g. committed vs freshly
                     generated content hash).
    """
    if not isinstance(expected, dict) or not isinstance(actual, dict):
        raise ValueError("mapping mode requires 'expected' and 'actual' to be objects")

    exp_keys, act_keys = set(expected), set(actual)
    missing = sorted(exp_keys - act_keys)
    extra = sorted(act_keys - exp_keys)
    mismatched = [
        {"key": k, "expected": expected[k], "actual": actual[k]}
        for k in sorted(exp_keys & act_keys)
        if expected[k] != actual[k]
    ]
    drift = bool(missing or mismatched or (extra and not ignore_extra))
    return DiffResult(
        mode=MODE_MAPPING,
        ok=not drift,
        missing=missing,
        extra=extra,
        mismatched=mismatched,
    )


def check_violations(violations: List[Dict[str, Any]]) -> DiffResult:
    """A non-empty violations list is a failure.

    Every item MUST be a dict carrying 'id' and 'message' (render_report prints
    them). This minimal shape is enforced here unconditionally — independent of
    the optional --validate flag — so a malformed payload becomes a tool error
    (ValueError -> exit 2), never an uncaught KeyError that exits 1 and hides
    inside the legitimate "violations found" code, corrupting the 0/1/2 contract.
    """
    if not isinstance(violations, list):
        raise ValueError("violations mode requires a JSON array")
    for i, item in enumerate(violations):
        if not isinstance(item, dict) or "id" not in item or "message" not in item:
            raise ValueError(f"violation[{i}] must be an object with 'id' and 'message'")
    return DiffResult(
        mode=MODE_VIOLATIONS,
        ok=len(violations) == 0,
        violations=list(violations),
    )


def validate_input(payload: Any, mode: str) -> None:
    """Structural gate run before diffing when --validate is set.

    Deliberately a focused hand-check of the two contract shapes rather than a
    general JSON-Schema engine, so the core stays dependency-free. The
    ``schemas/`` JSON-Schema files are the canonical human-readable contract;
    this function enforces the same shape. Raises ValueError on any mismatch.
    """
    if mode == MODE_MAPPING:
        if not isinstance(payload, dict):
            raise ValueError("mapping input must be a JSON object {key: value}")
        for k in payload:
            if not isinstance(k, str):
                raise ValueError(f"mapping keys must be strings; got {type(k).__name__}")
    elif mode == MODE_VIOLATIONS:
        if not isinstance(payload, list):
            raise ValueError("violations input must be a JSON array")
        for i, item in enumerate(payload):
            if not isinstance(item, dict):
                raise ValueError(f"violation[{i}] must be an object")
            if "id" not in item or "message" not in item:
                raise ValueError(f"violation[{i}] requires 'id' and 'message' fields")
    else:
        raise ValueError(f"unknown mode: {mode!r}")


def render_json(result: DiffResult) -> str:
    """Stable machine output (sorted keys) — safe to diff/store."""
    payload = asdict(result)
    payload["counts"] = result.counts()
    return json.dumps(payload, sort_keys=True, indent=2)


def render_report(result: DiffResult, label: Optional[str] = None) -> str:
    """Human report to stderr."""
    tag = f"[{label}] " if label else ""
    if result.ok:
        return f"{tag}OK — no drift ({result.mode} mode)."
    lines = [f"{tag}FAIL — {result.mode} drift:"]
    if result.mode == MODE_MAPPING:
        for k in result.missing:
            lines.append(f"  missing (expected, not found): {k}")
        for k in result.extra:
            lines.append(f"  extra (found, not expected):   {k}")
        for m in result.mismatched:
            lines.append(f"  mismatch: {m['key']}  expected={m['expected']!r} actual={m['actual']!r}")
    else:
        for v in result.violations:
            extra = {k: val for k, val in v.items() if k not in ("id", "message")}
            suffix = f"  {extra}" if extra else ""
            lines.append(f"  [{v['id']}] {v['message']}{suffix}")
    return "\n".join(lines)


def _load(path: str) -> Any:
    raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    return json.loads(raw)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="declare-fail",
        description="Shared reconciliation core: diff expected-vs-actual (mapping) "
        "or fail on a non-empty violations list. Exit 0=clean, 1=fail, 2=error.",
    )
    p.add_argument("--mode", required=True, choices=[MODE_MAPPING, MODE_VIOLATIONS])
    p.add_argument("--expected", help="mapping mode: path to expected JSON object ('-' = stdin)")
    p.add_argument("--actual", help="mapping mode: path to actual JSON object ('-' = stdin)")
    p.add_argument("--violations", help="violations mode: path to JSON array ('-' = stdin)")
    p.add_argument("--ignore-extra", action="store_true",
                   help="mapping mode: do not treat actual-only keys as drift")
    p.add_argument("--validate", action="store_true",
                   help="structurally validate input against the contract before diffing")
    p.add_argument("--label", help="label for the human report")
    args = p.parse_args(argv)

    try:
        if args.mode == MODE_MAPPING:
            if not args.expected or not args.actual:
                p.error("mapping mode requires --expected and --actual")
            expected = _load(args.expected)
            actual = _load(args.actual)
            if args.validate:
                validate_input(expected, MODE_MAPPING)
                validate_input(actual, MODE_MAPPING)
            result = diff_mappings(expected, actual, ignore_extra=args.ignore_extra)
        else:
            if not args.violations:
                p.error("violations mode requires --violations")
            violations = _load(args.violations)
            if args.validate:
                validate_input(violations, MODE_VIOLATIONS)
            result = check_violations(violations)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"declare-fail: error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    print(render_json(result))
    print(render_report(result, label=args.label), file=sys.stderr)
    return EXIT_CLEAN if result.ok else EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
