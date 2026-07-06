#!/usr/bin/env python3
"""verdict.py — consensus → labels → friction_rate → PRE-REGISTERED decision → print.

The audit's terminal step. Reads the artifact + rater sidecar + deterministic anchor,
forms a consensus label per fire (agree → that label; disagree → guarded gemini tie-break),
writes consensus into the artifact and calls fp_measure.finalize() for bookkeeping, then
computes friction_rate = FP/(TP+FP) DIRECTLY over resolved fires (NOT via fp_measure's
confirmed_fp==0 promotion semantics, which are wrong for a rate audit — garbage-guard d),
and applies the pre-registered decision rule, PRINTING the verdict (§12: observe the printed
verdict, not exit 0).

Pre-registered rules (locked in the approved plan BEFORE labeling):
  A13 (BLOCKING, census n≈30): count-based — >14 FP → SUNSET-CANDIDATE; <5 FP → KEEP;
       5–14 FP → INSUFFICIENT-EVIDENCE (honest band at this n). Wilson CI reported.
  b123 (ADVISORY, exploratory): report friction_rate + CI + per-concern breakdown; NO
       sunset verdict (precision-of-flag, upstream-confounded, recency-biased).

  ⚠ DIRECTIONAL, NOT A MANDATE (DEC-320 / Dart qcL10RSMREjW, 2026-07-06): the A13 raw-count
       rule is a SINGLE-SOURCE FP threshold, which is INADMISSIBLE as a standalone decision
       basis. A printed "SUNSET-CANDIDATE" does NOT authorize action — a sunset/soften decision
       additionally requires (i) admissible reliability (a rubric-based κ bar + a diagnosed
       low-κ cause, not a bare Landis-Koch κ≥0.6 veto), (ii) a conservative (Wilson-lower) FP
       over the trigger, and (iii) unconfounded friction. A13's 2026-07 verdict (FP=18 but
       κ=0.275 and the corpus confounded with the already-fixed 2026-06-30 re-fire bug) was
       DEFERRED, not actioned, on exactly these grounds. For a §8/safety enforcer, "soften"
       means deepening the trigger — never an agent-settable bypass.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path.home() / "dev/infrastructure/tools"))
import agreement as ag  # noqa: E402
import rater_driver as rd  # noqa: E402  (reuse _parse token-extraction + _tag)

ARTIFACT_DIR = Path.home() / ".claude/logs/fp-gate"
_BINARY = ("TP", "FP")
TIEBREAK_TIMEOUT = 90
# Third family = xAI/Grok via openrouter. The approved plan named gemini-ask, but
# gemini-ask hangs 30+min under WSL2 (MEMORY.md) — operationally unusable for a
# sequential tie-break loop. Grok is an independent 3rd family (neither OpenAI=codex
# nor DeepSeek), live+fast (verified), so it honors the "guarded independent third
# rater" intent better than the named-but-broken tool.
_TIEBREAK_CMD = ["openrouter-ask", "--model", "x-ai/grok-4.3"]

A13_SUNSET_FP = 14   # > → SUNSET-CANDIDATE
A13_KEEP_FP = 5      # < → KEEP


def _third_family_tiebreak(prompt: str) -> str:
    """Guarded third-family (Grok) tie-break. Hard timeout → uncertain (never hangs the run)."""
    try:
        res = subprocess.run(_TIEBREAK_CMD + [prompt], capture_output=True, text=True,
                             timeout=TIEBREAK_TIMEOUT)
    except subprocess.TimeoutExpired:
        return "uncertain"
    return rd._parse(res.stdout) or "uncertain"


def _consensus(codex: str, deepseek: str, tie_prompt: str | None) -> str:
    if codex in _BINARY and codex == deepseek:
        return codex
    if codex in _BINARY and deepseek in _BINARY and codex != deepseek and tie_prompt:
        return _third_family_tiebreak(tie_prompt)
    # one or both uncertain → uncertain
    return "uncertain"


def run(gate: str, use_tiebreak: bool) -> dict:
    art_path = ARTIFACT_DIR / f"{gate}.json"
    art = json.loads(art_path.read_text())
    raters = json.loads((ARTIFACT_DIR / f"{gate}.raters.json").read_text())
    anchor_path = ARTIFACT_DIR / f"{gate}.anchor.json"
    anchor_rows = json.loads(anchor_path.read_text())["rows"] if anchor_path.exists() else []
    anchor_by_src = {r["source"]: r["anchor_label"] for r in anchor_rows}

    rater_by_src = {r["source"]: r for r in raters["rows"]}
    consensus_labels, codex_labels, deepseek_labels, anchor_labels = [], [], [], []
    for f in art["fires"]:
        src = f.get("source")
        rr = rater_by_src.get(src, {})
        cx, ds = rr.get("codex", "uncertain"), rr.get("deepseek", "uncertain")
        # tie-break prompt: reuse the rater window (kept minimal — only on genuine disagreement)
        tie_prompt = None
        if use_tiebreak and cx in _BINARY and ds in _BINARY and cx != ds:
            tag = rd._tag(gate, f.get("_meta") or {})
            tie_prompt = (f"Two reviewers disagree. Concern(s): {tag}. Answer EXACTLY ONE TOKEN "
                          f"TP or FP.\n\n---\n{f.get('excerpt','')}")
        con = _consensus(cx, ds, tie_prompt)
        f["label"] = con  # TP/FP, else "uncertain" — fp_measure.finalize treats it as unresolved
        f["rationale"] = f"codex={cx} deepseek={ds} consensus={con}"
        consensus_labels.append(con)
        codex_labels.append(cx)
        deepseek_labels.append(ds)
        anchor_labels.append(anchor_by_src.get(src, "NA"))

    art["fires_labeled"] = sum(1 for l in consensus_labels if l in _BINARY)
    art_path.write_text(json.dumps(art, indent=2))

    # fp_measure.finalize() for bookkeeping (independent of the friction-rate verdict)
    finalize_note = _finalize_bookkeeping(gate)

    fp = sum(1 for l in consensus_labels if l == "FP")
    tp = sum(1 for l in consensus_labels if l == "TP")
    resolved = fp + tp
    uncertain = sum(1 for l in consensus_labels if l not in _BINARY)
    friction_rate = (fp / resolved) if resolved else None
    ci = ag.wilson_ci(fp, resolved) if resolved else None
    pairs = list(zip(codex_labels, deepseek_labels))
    return {
        "gate": gate, "n": len(consensus_labels), "tp": tp, "fp": fp,
        "resolved": resolved, "uncertain": uncertain,
        "friction_rate": friction_rate, "wilson_ci_95": ci,
        "pct_agreement": ag.pct_agreement(pairs), "cohen_kappa": ag.cohen_kappa(pairs),
        "llm_vs_anchor": ag.concordance(consensus_labels, anchor_labels),
        "finalize": finalize_note,
        "per_concern": _b123_breakdown(art, consensus_labels) if gate == "b123" else None,
    }


def _finalize_bookkeeping(gate: str) -> str:
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "fp_measure", Path.home() / "dev/infrastructure/tools/fp_measure.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        s = m.finalize(gate)
        return f"confirmed_fp={s['confirmed_fp']} decision={s['decision']}"
    except Exception as e:  # noqa: BLE001 — bookkeeping only; never blocks the verdict
        return f"(finalize skipped: {type(e).__name__}: {e})"


def _b123_breakdown(art: dict, labels: list[str]) -> dict:
    out: dict[str, dict] = {}
    for f, lab in zip(art["fires"], labels):
        strat = (f.get("_meta") or {}).get("stratum", "?")
        d = out.setdefault(strat, {"TP": 0, "FP": 0, "uncertain": 0})
        d[lab if lab in _BINARY else "uncertain"] += 1
    return out


def _print_verdict(r: dict) -> None:
    g = r["gate"]
    print("=" * 64)
    print(f"QC LABEL-AUDIT VERDICT — {g}")
    print("=" * 64)
    print(f"fires={r['n']}  resolved={r['resolved']}  TP={r['tp']}  FP={r['fp']}  "
          f"uncertain={r['uncertain']}")
    fr = r["friction_rate"]
    ci = r["wilson_ci_95"]
    ci_s = f"[{ci[0]:.2f}, {ci[1]:.2f}]" if ci else "n/a"
    print(f"friction_rate = {fr:.3f}" if fr is not None else "friction_rate = n/a",
          f"  Wilson95 {ci_s}")
    pa, kap = r["pct_agreement"], r["cohen_kappa"]
    print(f"inter-rater: %agreement={pa:.2f}" if pa is not None else "inter-rater: %agreement=n/a",
          f" cohen_kappa={kap:.2f}" if kap is not None else " cohen_kappa=undefined",
          f" (n={r['resolved']} — kappa UNSTABLE at n<40)" if g == "A13" else "")
    c = r["llm_vs_anchor"]
    print(f"LLM-vs-deterministic-anchor: {c['concordant']}/{c['n_checkable']} concordant"
          + (f" ({c['rate']:.2f})" if c["rate"] is not None else " (no mechanical anchor)"))
    if r["uncertain"] and r["n"] and r["uncertain"] / r["n"] > 0.25:
        print(f"⚠ LOW CONFIDENCE: uncertain/{r['n']} = {r['uncertain']/r['n']:.2f} > 0.25")

    if g == "A13":
        fp = r["fp"]
        if r["resolved"] == 0:
            verdict = "INSUFFICIENT-CORPUS (no resolved fires)"
        elif fp > A13_SUNSET_FP:
            verdict = f"SUNSET-CANDIDATE (FP={fp} > {A13_SUNSET_FP})"
        elif fp < A13_KEEP_FP:
            verdict = f"KEEP (FP={fp} < {A13_KEEP_FP})"
        else:
            verdict = f"INSUFFICIENT-EVIDENCE (FP={fp} in [{A13_KEEP_FP},{A13_SUNSET_FP}] — honest band at n={r['n']})"
        print(f"\n>>> A13 (blocking) pre-registered verdict: {verdict}")
    else:
        print("\n>>> b123 EXPLORATORY — NO sunset verdict (precision-of-flag; upstream-"
              "confounded; recency-biased). Per-concern breakdown:")
        for strat, d in (r["per_concern"] or {}).items():
            print(f"    {strat}: TP={d['TP']} FP={d['FP']} uncertain={d['uncertain']}")
    print(f"\nfp_measure bookkeeping: {r['finalize']}")
    print("=" * 64)


def main() -> int:
    ap = argparse.ArgumentParser(description="Verdict step for the QC label audit.")
    ap.add_argument("--gate", required=True, choices=["A13", "b123"])
    ap.add_argument("--no-tiebreak", action="store_true", help="skip gemini tie-break (disagreements → uncertain)")
    args = ap.parse_args()
    r = run(args.gate, use_tiebreak=not args.no_tiebreak)
    _print_verdict(r)
    (ARTIFACT_DIR / f"{args.gate}.verdict.json").write_text(json.dumps(r, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
