#!/usr/bin/env python3
"""vendor-drift-guard — fail if a vendored copy has drifted from canonical.

Part of the D3(a) "vendor + local drift-guard" mechanism (register
anomaly-register-cm-qc-2026-07-13.md, Dart eQvz4xu5tIcA): each target repo
vendors adapter scripts into its own `scripts/` so its CI needs no cross-repo
access to the private canonical `tools/` repo. This guard runs at LOCAL
pre-commit — where canonical `tools/` IS on disk — and fails if any vendored
copy no longer byte-matches its canonical source.

Accepted limitation (surfaced by /reflexion:critique): it CANNOT run in CI,
because CI cannot reach the private cross-owner canonical repo. It protects
anyone who commits with the pre-commit hook active; a bypassed hook can still
land a stale copy. That residual is the price of the only auth-free CI path.

Reuses the shared reconciliation core: expected = {name: sha256(canonical)},
actual = {name: sha256(vendored)}, via declare_fail.diff_mappings.

Manifest (JSON): { "<vendored path rel to --vendor-root>": "<canonical path rel to --canonical-root>" }

Exit: 0 in sync · 1 drift · 2 error (missing file, unreadable manifest).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

from declare_fail import diff_mappings, render_json, render_report  # noqa: E402

EXIT_CLEAN = 0
EXIT_DRIFT = 1
EXIT_ERROR = 2


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="vendor-drift-guard",
        description="Fail if any vendored copy has drifted from its canonical source.",
    )
    p.add_argument("--manifest", required=True, help="JSON map: vendored-path -> canonical-path")
    p.add_argument("--vendor-root", default=".", help="root for vendored paths (default: cwd)")
    p.add_argument("--canonical-root", default=".", help="root for canonical paths (default: cwd)")
    p.add_argument("--json", action="store_true", help="print the diff as JSON")
    args = p.parse_args(argv)

    vendor_root = Path(args.vendor_root)
    canonical_root = Path(args.canonical_root)

    try:
        manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or not manifest:
            raise ValueError("manifest must be a non-empty JSON object")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"vendor-drift-guard: error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    expected: dict[str, str] = {}  # canonical hashes
    actual: dict[str, str] = {}    # vendored hashes
    try:
        for vendored_rel, canonical_rel in manifest.items():
            canonical = canonical_root / canonical_rel
            vendored = vendor_root / vendored_rel
            # A missing file on either side is a hard error, not silent drift.
            expected[vendored_rel] = sha256(canonical)
            actual[vendored_rel] = sha256(vendored)
    except OSError as exc:
        print(f"vendor-drift-guard: error: missing/unreadable file: {exc}", file=sys.stderr)
        return EXIT_ERROR

    result = diff_mappings(expected, actual)
    if args.json:
        print(render_json(result))
    print(render_report(result, label="vendor-drift"), file=sys.stderr)
    return EXIT_CLEAN if result.ok else EXIT_DRIFT


if __name__ == "__main__":
    sys.exit(main())
