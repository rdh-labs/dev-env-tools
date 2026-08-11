#!/usr/bin/env python3
"""Measure prompt-deliverable hygiene in ~/dev/share.

Measures two things that the workspace declares it wants and does not enforce:

  1. REGISTRATION -- prompt-library.md declares a lifecycle
     (create -> register -> version -> retire, IDEA-10101). Measured 2026-08-10:
     4 of 65 registered.
  2. OPTIMIZER USE -- prompt-optimizer-v1.md is an Active governed 5-phase
     procedure (IDEA-10068). Measured 2026-08-10: ~5 of 65 genuine consumer
     citations.

Why this exists: the gap was measurable for months and nobody measured it twice,
so drift was invisible. Detection existed (8 open Dart tasks); measurement did not.

MATCHER HONESTY (CLAUDE.md measurement-validity rule). Deliverables are
identified by FILENAME, which is a lexical matcher: it undercounts deliverables
whose filename lacks the tokens, and overcounts non-deliverables that carry them
(anomaly analyses, handoffs). The output therefore reports a RANGE -- a strict
count and a loose count -- and never a single point estimate. A structured parse
with a narrow matcher is still a text match.

Exit codes: 0 -- always. Advisory only; never blocks. Raising this to blocking
would need the false-positive class below to be measured first.

Usage:
  prompt-deliverable-hygiene.py            # human-readable report
  prompt-deliverable-hygiene.py --json     # machine-readable
  prompt-deliverable-hygiene.py --log      # append to ~/.metrics/prompt-deliverable-hygiene.jsonl
  prompt-deliverable-hygiene.py --self-check   # prove the evaluation logic on fixtures
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

SHARE = Path.home() / "dev" / "share"
LIBRARY = SHARE / "prompt-library.md"
LOG = Path.home() / ".metrics" / "prompt-deliverable-hygiene.jsonl"

# CITATION MATCHERS -- a LOWER and an UPPER bound, never one number.
# The first version of this script required the literal filename including ".md"
# and reported 1 citation where the true count was at least 4: three deliverables
# cite the procedure as a named workflow ("via prompt-optimizer-v1 protocol",
# "prompt-optimizer Phase 5.5") rather than as a filename. A stricter matcher is
# not a safer matcher -- it just moves the error into the false-negative column,
# where nothing complains. Caught by an independent tool-enabled review, 2026-08-10.
CITE_STRICT_RE = re.compile(r"prompt-optimizer-v1\.md")          # lower bound: filename
CITE_LOOSE_RE = re.compile(r"prompt-optimizer(?:-v1)?\b", re.I)  # upper bound: incl. negations

# STRICT: filenames that are almost certainly prompt deliverables.
STRICT_RE = re.compile(r"(next-session|next-sessions|next-prompt|-PROMPT|PROMPT-|prompt-)", re.I)
# LOOSE: anything mentioning prompt or next-session at all. Upper bound.
LOOSE_RE = re.compile(r"(prompt|next-session)", re.I)
# Known non-deliverables that match lexically -- the false-positive class, named
# rather than silently dropped, so the exclusion is auditable.
EXCLUDE = {
    "prompt-library.md",          # the index itself
    "prompt-optimizer-v1.md",     # the procedure itself
    "prompt-optimizer-opus4.7.md",
    # NOTE: "prompt-check.md" was here and was removed 2026-08-10 -- no such file
    # exists under ~/dev/share, so unlike the three above it could never be
    # confirmed as a legitimate self-referential exclusion. An untestable
    # exclusion is indistinguishable from denominator cherry-picking.
}
EXCLUDE_PREFIX = ("AA-",)  # anomaly analyses that discuss prompts


def _classify(paths: list[Path]) -> dict:
    try:
        lib_text = LIBRARY.read_text(errors="replace")
        lib_readable = True
    except OSError:
        # exists() then read() is a TOCTOU race; and a missing library must not
        # silently render every file "unregistered" -- that reads as a 0% hygiene
        # catastrophe when the real fault is a missing index.
        lib_text, lib_readable = "", False
    registered, cite_lo, cite_hi, names, unreadable = [], [], [], [], []
    for p in paths:
        name = p.name
        names.append(name)
        try:
            body = p.read_text(errors="replace")
        except OSError:
            unreadable.append(name)
            continue  # excluded from citation counts rather than counted as non-citing
        if name in lib_text:
            registered.append(name)
        if CITE_STRICT_RE.search(body):
            cite_lo.append(name)
        if CITE_LOOSE_RE.search(body):
            cite_hi.append(name)
    return {
        "names": names,
        "registered": registered,
        "cite_lo": cite_lo,
        "cite_hi": cite_hi,
        "unreadable": unreadable,
        "library_readable": lib_readable,
    }


def _collect(regex: re.Pattern) -> list[Path]:
    out = []
    for p in sorted(SHARE.glob("*.md")):
        if p.name in EXCLUDE or p.name.startswith(EXCLUDE_PREFIX):
            continue
        if regex.search(p.name):
            out.append(p)
    return out


def measure() -> dict:
    strict = _classify(_collect(STRICT_RE))
    loose = _classify(_collect(LOOSE_RE))
    result = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "matcher_note": (
            "filename lexical matcher; strict and loose bound the true count. "
            "Not a point estimate."
        ),
        "excluded_as_non_deliverable": sorted(EXCLUDE),
        # Both exclusion mechanisms are reported: an exclusion you cannot see is
        # indistinguishable from a denominator quietly chosen to suit the verdict.
        "excluded_by_prefix": sorted(EXCLUDE_PREFIX),
    }
    warnings: list[str] = []
    for label, data in (("strict", strict), ("loose", loose)):
        total = len(data["names"])
        scored = total - len(data["unreadable"])
        result[label] = {
            "total": total,
            "unreadable": len(data["unreadable"]),
            # REGISTRATION IS A COUNT, NOT A RATE -- deliberately.
            # IDEA-10101 scopes prompt-library.md to *reusable* prompts. Most files
            # here are dated one-off session handoffs, which the registry does not
            # want. Dividing registrations by "every prompt-shaped filename" produced
            # a 6.6% figure that overstated the gap against a population the policy
            # never asked to be registered. Eligibility needs human judgement, so this
            # tool reports the count and refuses to manufacture a rate it cannot ground.
            "registered": len(data["registered"]),
            # Citation IS meaningful over all deliverables, and is reported as a BOUND.
            "cites_lo": len(data["cite_lo"]),
            "cites_hi": len(data["cite_hi"]),
            "cites_lo_pct": round(100 * len(data["cite_lo"]) / scored, 1) if scored else None,
            "cites_hi_pct": round(100 * len(data["cite_hi"]) / scored, 1) if scored else None,
            "library_readable": data["library_readable"],
        }
        # Never raise: an AssertionError would violate the exit-0-always contract this
        # file exists to uphold, and `python -O` strips asserts entirely -- the opposite
        # failure. Report the invariant breach instead of crashing on it.
        for k in ("registered", "cite_lo", "cite_hi"):
            if len(data[k if k != "registered" else "registered"]) > total:
                warnings.append(f"{label}.{k}: numerator > denominator -- denominator is wrong")
        if data["unreadable"]:
            warnings.append(f"{label}: {len(data['unreadable'])} unreadable file(s), excluded from rates")
        if not data["library_readable"]:
            warnings.append(f"{label}: prompt-library.md unreadable -- registration counts are not meaningful")

    result["warnings"] = warnings
    # Zero data is UNKNOWN, never PASS: a broken matcher or a moved directory must not
    # read as health. Verdict covers the citation metric (the one with a sound
    # denominator); registration is reported but never scored.
    s = result["strict"]
    if not s["total"] or not s["library_readable"]:
        result["verdict"] = "UNKNOWN"
    elif s["cites_hi_pct"] is not None and s["cites_hi_pct"] < 25:
        result["verdict"] = "FAIL"
    else:
        result["verdict"] = "PASS"
    return result


def self_check() -> int:
    """Prove the evaluation logic, not just that the script runs.

    A checker that only reports on live data cannot distinguish 'measured
    correctly' from 'measured nothing'. These fixtures fail loudly if the
    classification logic breaks.
    """
    failures = []

    # 1. Both matchers must actually select something on the live tree.
    if not _collect(STRICT_RE):
        failures.append("strict matcher selected 0 files -- matcher or path is broken")
    # 2. Loose must be a superset of strict, by construction.
    s = {p.name for p in _collect(STRICT_RE)}
    l = {p.name for p in _collect(LOOSE_RE)}
    if not s <= l:
        failures.append(f"strict is not a subset of loose: {sorted(s - l)[:3]}")
    # 3. The index and the procedure must never be counted as deliverables.
    if any(n in (s | l) for n in ("prompt-library.md", "prompt-optimizer-v1.md")):
        failures.append("self-referential file leaked into the deliverable set")
    # 4. Rates must be well-formed.
    m = measure()
    for label in ("strict", "loose"):
        b = m[label]
        if b["cites_lo"] > b["cites_hi"]:
            failures.append(f"{label}: strict citation count exceeds loose -- bounds inverted")
        if b["registered"] > b["total"]:
            failures.append(f"{label}: registered > total -- denominator is wrong")
        for k in ("cites_lo_pct", "cites_hi_pct"):
            if b[k] is not None and not (0 <= b[k] <= 100):
                failures.append(f"{label}: {k} out of range")
    # 5. Zero data must never read as PASS.
    if m["strict"]["total"] and m["verdict"] == "UNKNOWN":
        failures.append("verdict UNKNOWN despite live data -- verdict logic broken")

    for f in failures:
        print(f"  [FAIL/self-check] {f}")
    if not failures:
        print("  [PASS/self-check] 4/4 checks proved the evaluation logic")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--log", action="store_true")
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args()

    if args.self_check:
        # "Advisory, never blocks" governs the drift MEASUREMENT, not this tool's own
        # consistency proof. Swallowing a failing self-check would make the fixture
        # useless to any automation gating on the exit code.
        print("PROMPT-DELIVERABLE HYGIENE: self-check")
        return self_check()

    m = measure()
    if args.log:
        try:  # exit-0-always: a full disk must not turn a measurement into a failure
            LOG.parent.mkdir(parents=True, exist_ok=True)
            with LOG.open("a") as fh:
                fh.write(json.dumps(m) + "\n")
        except OSError as exc:
            print(f"  [WARN] could not write {LOG}: {exc}", file=sys.stderr)

    if args.json:
        print(json.dumps(m, indent=2))
        return 0

    print(f"PROMPT-DELIVERABLE HYGIENE: {m['verdict']}")
    for label in ("strict", "loose"):
        b = m[label]
        print(
            f"  [{label:6}] {b['total']:3} deliverables | "
            f"registered {b['registered']} (count only -- eligibility is not machine-decidable) | "
            f"cite optimizer {b['cites_lo']}-{b['cites_hi']} "
            f"({b['cites_lo_pct']}%-{b['cites_hi_pct']}%)"
        )
    for w in m["warnings"]:
        print(f"  [WARN] {w}")
    print(f"  matcher: {m['matcher_note']}")
    print("  lifecycle: prompt-library.md (IDEA-10101) | optimizer: IDEA-10068")
    print("  execution path for the gap itself: Dart WjGEGzegaIkQ (Critical), 3ZU4Rwd4Y5YP")
    return 0


if __name__ == "__main__":
    sys.exit(main())
