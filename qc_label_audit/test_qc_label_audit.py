#!/usr/bin/env python3
"""Self-contained tests for the QC label-audit harness (no pytest dependency).

Covers the pure, load-bearing logic that the live E2E can't re-assert cheaply:
timestamp/timezone parsing, fixture discrimination, the A13 block-id join anchor,
rater-output parsing (real wrapper noise), agreement math, and the pre-registered
verdict branches. Run: `python3 test_qc_label_audit.py` (exit 0 = all pass).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agreement as ag
import corpus_adapter as ca
import rater_driver as rd
import verdict as V

_fails = []


def check(name, cond):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")
    if not cond:
        _fails.append(name)


# ── timestamp / timezone (the bug the A13 join would have hit) ────────────────
z = ca._ts_epoch("2026-07-02T23:48:21.059Z")
plus = ca._ts_epoch("2026-07-02T23:48:21.059000+00:00")
check("Z and +00:00 parse to the same epoch", z is not None and abs(z - plus) < 1e-3)
check("naive ts parses (treated UTC)", ca._ts_epoch("2026-06-30T16:51:18.911315") is not None)
check("garbage ts → None", ca._ts_epoch("not-a-date") is None and ca._ts_epoch("") is None)

# ── fixture discrimination (UUID-shape, not substring) ────────────────────────
check("real UUID with 'e2e' substring is NOT a fixture",
      ca._is_test_fixture("5c52e2ec-eb6f-4e96-957c-a32cdee73b4d") is False)
check("non-UUID 'test-session-e2e-b123' IS a fixture", ca._is_test_fixture("test-session-e2e-b123"))
check("non-UUID 'base' IS a fixture", ca._is_test_fixture("base"))

# ── A13 block-id UTC anchor vs naive fallback ─────────────────────────────────
ep, fb = ca._a13_anchor_epoch({"block_ids": ["ship_sentinel_gate|2026-06-30T23:43:15Z"],
                               "timestamp": "2026-06-30T16:51:18.911315"})
check("A13 anchor uses block_id UTC (not fallback)", ep is not None and fb is False)
ep2, fb2 = ca._a13_anchor_epoch({"block_ids": [], "timestamp": "2026-06-30T16:51:18.911315"})
check("A13 anchor falls back to naive ts when no block_id, flagged", ep2 is not None and fb2 is True)

# ── rater-output parsing (real wrapper noise) ─────────────────────────────────
check("parse codex noise 'tokens used\\n14,265\\nTP' → TP", rd._parse("tokens used\n14,265\nTP") == "TP")
check("parse deepseek '→ Model: x\\n\\nFP' → FP", rd._parse("→ Model: deepseek\n\nFP") == "FP")
check("parse takes LAST token", rd._parse("thinking TP then actually FP") == "FP")
check("parse no token → None", rd._parse("I am not sure") is None)

# ── agreement math ────────────────────────────────────────────────────────────
check("pct_agreement 3/4", abs(ag.pct_agreement([("TP","TP"),("FP","FP"),("TP","TP"),("TP","FP")]) - 0.75) < 1e-9)
check("pct_agreement ignores uncertain pairs",
      ag.pct_agreement([("TP","TP"),("TP","uncertain")]) == 1.0)
check("kappa perfect agreement = 1.0",
      abs(ag.cohen_kappa([("TP","TP"),("FP","FP"),("TP","TP"),("FP","FP")]) - 1.0) < 1e-9)
check("kappa all-one-label → None (undefined)",
      ag.cohen_kappa([("TP","TP"),("TP","TP")]) is None)
lo, hi = ag.wilson_ci(2, 3)
check("wilson_ci(2,3) brackets 0.667", lo < 0.667 < hi and 0 <= lo and hi <= 1)
check("wilson_ci(0,0) → None", ag.wilson_ci(0, 0) is None)
con = ag.concordance(["TP","FP","TP"], ["TP","NA","FP"])
check("concordance ignores NA anchor, counts checkable", con["n_checkable"] == 2 and con["concordant"] == 1)

# ── verdict branches (pre-registered A13 rule) ────────────────────────────────
def _branch(fp):
    r = dict(gate="A13", n=30, tp=30-fp, fp=fp, resolved=30, uncertain=0,
             friction_rate=fp/30, wilson_ci_95=ag.wilson_ci(fp,30), pct_agreement=0.9,
             cohen_kappa=0.7, llm_vs_anchor={"n_checkable":0,"concordant":0,"rate":None},
             finalize="(t)", per_concern=None)
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        V._print_verdict(r)
    return buf.getvalue()

check("FP=20 → SUNSET-CANDIDATE", "SUNSET-CANDIDATE" in _branch(20))
check("FP=2 → KEEP", "KEEP" in _branch(2))
check("FP=8 → INSUFFICIENT-EVIDENCE", "INSUFFICIENT-EVIDENCE" in _branch(8))
check("FP=14 (boundary) → INSUFFICIENT (not sunset)", "INSUFFICIENT-EVIDENCE" in _branch(14))
check("FP=15 → SUNSET", "SUNSET-CANDIDATE" in _branch(15))

print(f"\n{'ALL PASS' if not _fails else 'FAILURES: ' + ', '.join(_fails)}")
sys.exit(1 if _fails else 0)
