#!/usr/bin/env python3
"""Assert the enforcement-registration-cron.sh mirror agrees with notify_ledger.classify_row.

WHY THIS EXISTS. `enforcement-registration-cron.sh` deliberately MIRRORS the canonical
classifier rather than importing it, so that an unattended cron in ~/bin acquires no runtime
dependency on ~/dev/infrastructure. That choice is defensible. What is not defensible is
mirroring without a mechanism that detects divergence: the first mirror copied only the branch
ORDER and omitted truthiness normalisation, so it disagreed with canonical on 6 of 12 probe
shapes ({"success": 1} -> unknown vs delivered) and raised AttributeError on a non-dict row.
It shipped under a comment reading "Keep the two in step" — a behavioural norm guarding a
machine invariant, which is the weakest enforcement class this workspace has.

Duplication is legitimate here. UNVERIFIED duplication is not. This is the verification.

Usage:  python3 notify_ledger_parity.py [path-to-cron-script]
        MIRROR_UNDER_TEST=<path>  overrides the subject (used by the mutation control).
Exit 0 = full agreement. Exit 1 = divergence (printed). Exit 2 = could not extract the mirror,
which is a FAILURE state per DEC-326, never a pass — a parity check that cannot find its
subject has not verified anything.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from notify_ledger import classify_row  # noqa: E402

DEFAULT_MIRROR = Path.home() / "bin" / "enforcement-registration-cron.sh"

# The verdict word each classification prints, mapped back to the canonical state name.
VERDICT_TO_STATE = {
    "OK": "delivered",
    "FAILED": "failed",
    "WITHHELD_ONLY": "withheld",
    "DRY_RUN_ONLY": "rehearsed",
    "UNKNOWN": "unknown",
}

# Shapes chosen to exercise NORMALISATION, not just branch order — order was never the part
# that differed. Every non-bool `success` here was a live divergence in the first mirror.
SHAPES = [
    {"success": True},
    {"success": False},
    {"success": "False"},          # one real row on the live ledger carries this
    {"success": "true"},
    {"success": 1},
    {"success": 0},
    {"success": "yes"},
    {"success": "no"},
    {"success": "T"},
    {"success": 1.0},
    {"success": "1"},
    {"success": "maybe"},          # unrecognisable -> unknown, never folded into a neighbour
    {"success": None},
    {"success": True, "suppressed": True},
    {"success": False, "suppressed": True},
    {"success": True, "dry_run": True},
    {"success": True, "dry_run": True, "suppressed": True},
    {},
]


def extract_mirror(path):
    """Pull the embedded python classification block out of the bash heredoc."""
    try:
        lines = Path(path).read_text().splitlines()
    except OSError as exc:
        return None, f"cannot read {path}: {exc}"
    start = end = None
    for i, line in enumerate(lines):
        if start is None and line.rstrip().endswith("<<'PYEOF'"):
            start = i + 1
        elif start is not None and line.strip() == "PYEOF":
            end = i
            break
    if start is None or end is None:
        return None, f"no PYEOF heredoc found in {path}"
    return "\n".join(lines[start:end]), None


def main():
    path = os.environ.get("MIRROR_UNDER_TEST") or (
        sys.argv[1] if len(sys.argv) > 1 else DEFAULT_MIRROR)
    block, err = extract_mirror(path)
    if err:
        print(f"UNKNOWN: {err} — parity NOT verified")
        return 2

    with tempfile.TemporaryDirectory() as tmp:
        prog = Path(tmp) / "mirror.py"
        prog.write_text(block)
        ledger = Path(tmp) / "row.jsonl"
        disagreements = []
        for shape in SHAPES:
            ledger.write_text(json.dumps({**shape, "timestamp": "t"}) + "\n")
            try:
                out = subprocess.run([sys.executable, str(prog), str(ledger), "0"],
                                     capture_output=True, text=True, timeout=30)
            except subprocess.SubprocessError as exc:
                disagreements.append((shape, classify_row(shape), f"CRASH: {exc}"))
                continue
            verdict = (out.stdout or "").split(":")[0].strip()
            mirror = VERDICT_TO_STATE.get(verdict)
            if mirror is None:
                # A crash or an unmapped verdict is a divergence, not a skip.
                detail = (out.stderr or out.stdout or "").strip().splitlines()
                mirror = f"NO-VERDICT ({detail[-1][:60] if detail else 'empty'})"
            canonical = classify_row(shape)
            if mirror != canonical:
                disagreements.append((shape, canonical, mirror))

    if disagreements:
        print(f"DIVERGENCE: {len(disagreements)}/{len(SHAPES)} shapes disagree")
        for shape, canonical, mirror in disagreements:
            print(f"  {json.dumps(shape):<46} canonical={canonical:<10} mirror={mirror}")
        return 1
    print(f"PARITY: {len(SHAPES)}/{len(SHAPES)} shapes agree")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
