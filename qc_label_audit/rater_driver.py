#!/usr/bin/env python3
"""rater_driver.py — two independent cross-family LLM raters label each fire's window.

Escapes the agent-self-labeling trap (plan constraint; Huang et al. ICLR 2024): the
orchestrating agent NEVER assigns a label — two external model families do, and disagreements
go to a guarded third family (in verdict.py). Raters see ONLY the neutral window + the gate's
concern CODES — never the gate's own justification prose (garbage-guard a: prevents echo).

Rater A = codex-ask (OpenAI family); Rater B = openrouter-ask --model deepseek/... (DeepSeek).
Output parsing: the wrappers add banner/token-count noise, so we take the LAST `\\b(TP|FP)\\b`
token in stdout (verified: codex emits "tokens used\\n14,265\\nTP"; deepseek "→ Model: ...\\n\\nTP").
Unparseable/empty → one retry with a stricter reminder → "uncertain". A whole family failing
on every fire aborts LOUD (no partial corpus silently scored).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ARTIFACT_DIR = Path.home() / ".claude/logs/fp-gate"
_TOKEN_RE = re.compile(r"\b(TP|FP)\b", re.I)
CALL_TIMEOUT = 90

RATERS = {
    "codex": ["codex-ask"],
    "deepseek": ["openrouter-ask", "--model", "deepseek/deepseek-v3.2"],
}

_A13_PROMPT = (
    "Below is a transcript window that begins where an automated gate demanded a "
    "'## Anomaly Analysis' section (triggered by a prior blocked action: {tag}). "
    "Read ONLY what is shown. Answer with EXACTLY ONE TOKEN. Answer TP if, within this "
    "window, the agent BOTH (a) wrote an '## Anomaly Analysis' section AND (b) completed or "
    "made concrete progress on the action that was blocked. Answer FP if instead the agent "
    "looped, abandoned the action, switched topic, or the prior block was plainly spurious "
    "(nothing real to analyze). No other output.\n\n---\n{window}"
)
_B123_PROMPT = (
    "Below is the closing portion of an agent session. An automated closure-quality gate "
    "flagged it inadequate, naming these concerns: {tag}. (OUT:n = n unreconciled "
    "contradictions; AN(...absent) = a required closure element missing; AN(...dishonest) = a "
    "dishonest closure claim.) Read ONLY what is shown. Answer with EXACTLY ONE TOKEN. Answer "
    "TP if at least one named concern is a REAL, material defect you can point to in this text. "
    "Answer FP if every named concern is spurious. No other output.\n\n---\n{window}"
)


def _tag(gate: str, meta: dict) -> str:
    if gate == "A13":
        return ", ".join(meta.get("block_ids") or []) or "(unknown prior block)"
    return ", ".join(meta.get("concerns") or []) or "(unknown concerns)"


def _parse(stdout: str) -> str | None:
    matches = _TOKEN_RE.findall(stdout or "")
    return matches[-1].upper() if matches else None


def _call(cmd: list[str], prompt: str) -> str:
    """Return TP/FP/uncertain. One retry with a stricter nudge on unparseable output."""
    for attempt in (0, 1):
        p = prompt if attempt == 0 else (
            "Your previous answer was not a single token. " + prompt)
        try:
            res = subprocess.run(cmd + [p], capture_output=True, text=True, timeout=CALL_TIMEOUT)
        except subprocess.TimeoutExpired:
            return "uncertain"
        label = _parse(res.stdout)
        if label:
            return label
    return "uncertain"


def run(gate: str, limit: int = 0) -> dict:
    art = json.loads((ARTIFACT_DIR / f"{gate}.json").read_text())
    tmpl = _A13_PROMPT if gate == "A13" else _B123_PROMPT
    rows = []
    fam_ok = {name: 0 for name in RATERS}
    fires = art.get("fires", [])
    if limit:
        fires = fires[:limit]
    for i, f in enumerate(fires):
        prompt = tmpl.format(tag=_tag(gate, f.get("_meta", {})), window=f.get("excerpt", ""))
        row = {"source": f.get("source")}
        for name, cmd in RATERS.items():
            lab = _call(cmd, prompt)
            row[name] = lab
            if lab in ("TP", "FP"):
                fam_ok[name] += 1
        rows.append(row)
        print(f"  [{i+1}/{len(fires)}] {row.get('codex')}/{row.get('deepseek')}  {f.get('source')}",
              file=sys.stderr)
    # abort-loud: a family that never produced a usable label on a non-empty corpus is broken
    if fires:
        dead = [n for n, ok in fam_ok.items() if ok == 0]
        if dead:
            raise RuntimeError(f"rater family produced NO usable labels: {dead} — aborting (no "
                               f"partial corpus silently scored). fam_ok={fam_ok}")
    return {"gate": gate, "n": len(rows), "fam_usable": fam_ok, "rows": rows}


def main() -> int:
    ap = argparse.ArgumentParser(description="Two-family LLM rater driver for the QC label audit.")
    ap.add_argument("--gate", required=True, choices=["A13", "b123"])
    ap.add_argument("--limit", type=int, default=0, help="rate only the first N fires (E2E smoke)")
    ap.add_argument("--write", action="store_true", help=f"write {ARTIFACT_DIR}/<gate>.raters.json")
    args = ap.parse_args()
    out = run(args.gate, limit=args.limit)
    print(f"gate={out['gate']}  n={out['n']}  fam_usable={json.dumps(out['fam_usable'])}")
    if args.write:
        p = ARTIFACT_DIR / f"{args.gate}.raters.json"
        p.write_text(json.dumps(out, indent=2))
        print(f"Raters written: {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
