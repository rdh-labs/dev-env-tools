#!/usr/bin/env python3
"""Measure how much of a session's QC the USER performed, from the session transcript.

WHY THIS EXISTS. On 2026-08-11 a session was measured: 109 user messages, of which 31 demanded
the gaps #1/#2/#3 schema, 22 declared "THIS IS AN ANOMALY", 12 quoted the agent's own Open:/You:
line back at it, 10 asked "anything else under ss11", and 5 said "you have done it AGAIN". The
agent self-detected roughly 2 of ~14 anomalies. The user WAS the enforcement mechanism.

That had never been recorded as data. It was known the way a mood is known.

WHY A MEASUREMENT AND NOT A GATE — this is the load-bearing design decision. Dart
`O7t4WAplaNNk` carries a 2026-07-05 directive: CONSOLIDATE-AND-NET-SUBTRACT, do not add
mechanism #119, citing Huang et al. ICLR 2024 (arXiv:2310.01798) that intrinsic self-correction
— correction without external feedback — does not work. A gate here would be one more internal
self-check, which is precisely the intervention the literature says fails. So this instrument
does not block, warn, or advise. It COUNTS the external signal, so that any future claim of
"the agent self-gates better now" becomes falsifiable instead of felt.

WHAT IT CANNOT DO, stated because a measurement that oversells itself is worse than none:
- It counts USER PROMPTS, not agent violations. One prompt can cover several; several can
  cover one. It is a proxy, and the proxy's direction is meaningful while its magnitude is not.
- Patterns are literal-string matches over the user's own words. A user who phrases a
  correction differently is invisible to it. That is the same form-not-substance limit this
  session catalogued repeatedly, and it applies here too.
- A LOW score can mean the agent improved, OR that the user gave up. The instrument cannot
  distinguish those, and the second is the outcome that matters most. Read it alongside
  session length and whether work actually shipped.

Exit 0 always — it reports, it does not judge.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PROJECTS = Path.home() / ".claude" / "projects"

# The user's own recurring phrasings. Deliberately literal: an inferred "sentiment" score would
# be unfalsifiable, which is the defect this whole exercise is about.
PATTERNS = {
    "declared_anomaly":      re.compile(r"THIS IS AN ANOMALY", re.I),
    "demanded_gaps_schema":  re.compile(r"gaps? #1|gaps \(gaps #1\)", re.I),
    "quoted_my_tail_back":   re.compile(r'"Open[:—]|"You:|"Open —|Open / RISKS', re.I),
    "asked_anything_else":   re.compile(r"anything else that we can/should complete", re.I),
    "you_did_it_again":      re.compile(r"done it AGAIN", re.I),
    "asked_did_you_run_qc":  re.compile(r"did you run|have you run|are you satisfied", re.I),
}


def load_user_messages(path: Path) -> list[str]:
    out = []
    for line in path.read_text(errors="replace").splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("type") != "user":
            continue
        c = d.get("message", {}).get("content", [])
        txt = c if isinstance(c, str) else "".join(
            b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
        # Tool results and system reminders are not the user speaking.
        if txt.strip() and "<system-reminder>" not in txt[:200]:
            out.append(txt)
    return out


def measure(messages: list[str]) -> dict:
    counts = {k: sum(1 for m in messages if rx.search(m)) for k, rx in PATTERNS.items()}
    total = len(messages)
    # A message may match several patterns; count DISTINCT corrective messages so the rate
    # cannot exceed 1.0. A rate above 1.0 would mean the denominator is wrong.
    corrective = sum(1 for m in messages if any(rx.search(m) for rx in PATTERNS.values()))
    return {"user_messages": total, "corrective_messages": corrective,
            "correction_rate": round(corrective / total, 3) if total else None,
            "by_pattern": counts}


def self_check() -> int:
    ok = []
    r = measure(["THIS IS AN ANOMALY and gaps #1 too", "hello", "done it AGAIN"])
    ok.append(("a message matching TWO patterns counts ONCE as corrective",
               r["corrective_messages"] == 2))
    ok.append(("rate can never exceed 1.0", r["correction_rate"] <= 1.0))
    ok.append(("per-pattern counts still count both", r["by_pattern"]["declared_anomaly"] == 1
               and r["by_pattern"]["demanded_gaps_schema"] == 1))
    ok.append(("an empty session yields None, not 0.0 (no data is not a good score)",
               measure([])["correction_rate"] is None))
    ok.append(("a clean session scores 0.0", measure(["thanks", "ok"])["correction_rate"] == 0.0))
    bad = [m for m, good in ok if not good]
    for m in bad:
        print(f"  [FAIL/self-check] {m}")
    if not bad:
        print(f"  [PASS/self-check] {len(ok)}/{len(ok)} checks proved the counting logic")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--session", help="session uuid; default = every transcript found")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args()

    if args.self_check:
        print("USER CORRECTION RATE: self-check")
        return self_check()

    paths = sorted(PROJECTS.rglob(f"{args.session}.jsonl")) if args.session \
        else sorted(PROJECTS.rglob("*.jsonl"))
    if not paths:
        print("no transcripts found — cannot measure, which is not the same as a good score")
        return 0

    rows = []
    for p in paths:
        msgs = load_user_messages(p)
        if len(msgs) < 5:          # too short to mean anything; excluded and SAID so
            continue
        r = measure(msgs)
        # Full id for machines, short label for humans. The 8-char form is AMBIGUOUS:
        # 4 of 1962 workspace sessions share an 8-char prefix, and one pair differs by a
        # SINGLE hex digit (ANOMALY-REGISTER 141). Display truncation is fine; emitting a
        # truncated id into --json invites a downstream join that silently merges sessions.
        r["session_id"] = p.stem
        r["session"] = p.stem[:8]      # label only — never join on this
        rows.append(r)

    rows.sort(key=lambda r: r["correction_rate"] or 0, reverse=True)
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0

    print(f"USER CORRECTION RATE — {len(rows)} session(s) with >=5 user messages")
    print(f"  (shorter sessions excluded: too few messages for the rate to mean anything)\n")
    print(f"  {'session':10} {'msgs':>5} {'corrective':>11} {'rate':>6}")
    for r in rows[:15]:
        print(f"  {r['session']:10} {r['user_messages']:5d} {r['corrective_messages']:11d} "
              f"{r['correction_rate']:6.2f}")
    if rows:
        worst = rows[0]
        print(f"\n  highest: {worst['session']} at {worst['correction_rate']:.0%} — "
              f"{worst['corrective_messages']} of {worst['user_messages']} messages were corrections")
        for k, v in sorted(worst["by_pattern"].items(), key=lambda kv: -kv[1]):
            if v:
                print(f"    {v:4d}  {k}")
    print("\n  A FALLING rate can mean the agent improved OR that the user stopped correcting.")
    print("  This instrument cannot tell those apart. The second is the worse outcome.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
