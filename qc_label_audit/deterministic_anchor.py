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
from fp_measure import evidence_gate as _eg  # noqa: E402 — same rule: import A101's live regexes
_SAY_WORD_RE = _eg._A101_SAY_THE_WORD_RE
_REC_RE = _eg._A101_RECOMMENDATION_RE
_AUTH_RE = _eg._A101_AUTH_REQUIRED_RE

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


def _anchor_a101(fire: dict) -> dict:
    """Mechanical anchor for A101 (say-the-word deferral).

    WHAT IS *NOT* CHECKABLE HERE, and why. A101's three exclusions (recommendation present,
    genuine choice offered, action genuinely requires the user) are ALREADY applied by the
    predicate before it fires — a fire means the SELECTED `You:` value matched none of them.
    Re-running those regexes therefore proves nothing about the label; it would only restate
    the fire condition. Reporting them as an anchor LABEL would be a rubber stamp.

    WHAT *IS* checkable: EVIDENCE INTEGRITY. a101's evidence_kind is "scanned-region" — the
    stored spans are every `You:` value in the tail the predicate examined, so the value that
    actually fired MUST be among them, and it must match the gate's own trigger regex. If no
    span matches, the artifact cannot support its own fire and the LLM raters are judging text
    that does not contain the trigger. That is a mechanical FP, and it is exactly the class of
    defect this session found in the artifacts (head excerpts that could not contain the match).

    Everything else is left NA — the ambiguous middle belongs to the raters, per this module's
    stated design.
    """
    spans = [m for m in (fire.get("matched") or []) if isinstance(m, str)]
    text = fire.get("excerpt", "")
    hay = spans or [text]
    trigger = any(_SAY_WORD_RE.search(x) for x in hay)
    facts = {
        "n_spans": len(spans),
        "trigger_in_evidence": trigger,
        # Reported as FACTS only — never as the label, for the reason in the docstring.
        "recommendation_somewhere": any(_REC_RE.search(x) for x in hay),
        "auth_required_somewhere": any(_AUTH_RE.search(x) for x in hay),
    }
    # The ONLY mechanically decidable extreme: the evidence does not contain the trigger.
    return {"anchor_label": ("FP" if not trigger else "NA"), "facts": facts}


def run(gate: str) -> dict:
    path = ARTIFACT_DIR / f"{gate}.json"
    art = json.loads(path.read_text())
    fn = {"A13": _anchor_a13, "a101": _anchor_a101}.get(gate, _anchor_b123)
    rows = [{"source": f.get("source"), **fn(f)} for f in art.get("fires", [])]
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["anchor_label"]] = counts.get(r["anchor_label"], 0) + 1
    return {"gate": gate, "n": len(rows), "label_counts": counts, "rows": rows}


def main() -> int:
    ap = argparse.ArgumentParser(description="Deterministic mechanical anchor for the QC label audit.")
    ap.add_argument("--gate", required=True, choices=["A13", "b123", "a101"])
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
