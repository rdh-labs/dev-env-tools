#!/usr/bin/env python3
"""deterministic_anchor.py — mechanical (no-LLM) cross-check on the LLM raters.

Added per the 3-family multi-check (plan D4): two cross-family LLM raters relocate the
gameable-proxy trap rather than escape it, so we pair them with a DETERMINISTIC anchor —
a mechanical check of facts that need no judgment — and report LLM-vs-anchor discordance.
High discordance INVALIDATES the LLM number (garbage-guard e).

Anchor semantics are deliberately COARSE — mechanical facts + a label only for the clear
extremes; the ambiguous middle is left to the LLM raters (anchor_label="NA").

A13 (block-until-`## Anomaly Analysis`):
  facts   = aa_present (literal `^## Anomaly Analysis` in the window, the gate's own regex),
            repeat_fires (from _meta; thrash signal).
  label   = TP if aa_present and repeat_fires==0 (clean: block acknowledged once, AA written)
            FP if not aa_present and repeat_fires>=2 (thrash loop, never acknowledged)
            NA otherwise (mechanically ambiguous → LLM decides).

b123 (closure-adequacy): the named concerns are only weakly mechanically checkable and the
gate is exploratory, so the anchor reports facts (has_reflexion/handoff/mem tokens) but
labels NA — LLM judgment dominates. Kept minimal by design.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / "dev/infrastructure/tools"))
from fp_measure import _A97_ANOMALY_RE as _AA_RE  # noqa: E402 — reuse the gate's fingerprinted regex, don't retype (desync risk)

ARTIFACT_DIR = Path.home() / ".claude/logs/fp-gate"
_MEM_RE = re.compile(r"mem_session_summary|mem_save", re.I)
_HANDOFF_RE = re.compile(r"\bhandoff\b|## Session Handoff", re.I)
_REFLEXION_RE = re.compile(r"reflexion|/critique", re.I)


def _anchor_a13(fire: dict) -> dict:
    text = fire.get("excerpt", "")
    meta = fire.get("_meta", {})
    aa = bool(_AA_RE.search(text))
    rf = meta.get("repeat_fires")
    rf = rf if isinstance(rf, int) else 0
    if aa and rf == 0:
        label = "TP"
    elif not aa and rf >= 2:
        label = "FP"
    else:
        label = "NA"
    return {"anchor_label": label, "facts": {"aa_present": aa, "repeat_fires": rf}}


def _anchor_b123(fire: dict) -> dict:
    text = fire.get("excerpt", "")
    return {"anchor_label": "NA",  # b123 concerns not reliably mechanical → LLM decides
            "facts": {"has_reflexion": bool(_REFLEXION_RE.search(text)),
                      "has_handoff": bool(_HANDOFF_RE.search(text)),
                      "has_mem": bool(_MEM_RE.search(text))}}


def run(gate: str) -> dict:
    path = ARTIFACT_DIR / f"{gate}.json"
    art = json.loads(path.read_text())
    fn = _anchor_a13 if gate == "A13" else _anchor_b123
    rows = [{"source": f.get("source"), **fn(f)} for f in art.get("fires", [])]
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["anchor_label"]] = counts.get(r["anchor_label"], 0) + 1
    return {"gate": gate, "n": len(rows), "label_counts": counts, "rows": rows}


def main() -> int:
    ap = argparse.ArgumentParser(description="Deterministic mechanical anchor for the QC label audit.")
    ap.add_argument("--gate", required=True, choices=["A13", "b123"])
    ap.add_argument("--write", action="store_true", help=f"write {ARTIFACT_DIR}/<gate>.anchor.json")
    args = ap.parse_args()
    out = run(args.gate)
    print(f"gate={out['gate']}  n={out['n']}  anchor_labels={json.dumps(out['label_counts'])}")
    if args.write:
        p = ARTIFACT_DIR / f"{args.gate}.anchor.json"
        p.write_text(json.dumps(out, indent=2))
        print(f"Anchor written: {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
