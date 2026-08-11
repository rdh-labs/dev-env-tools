#!/usr/bin/env python3
"""Measure prompt-deliverable hygiene in ~/dev/share.

Measures two things that the workspace declares it wants and does not enforce:

  1. REGISTRATION -- prompt-library.md declares a lifecycle
     (create -> register -> version -> retire, IDEA-10101). Measured 2026-08-10:
     4 of 65 registered.
  2. OPTIMIZER USE -- prompt-optimizer-v1.md is an Active governed 5-phase
     procedure (IDEA-10068).

WHAT THE NUMBERS ARE, PRECISELY -- the metric names are shorthand and overstate:
  "registered" = the filename appears ANYWHERE in prompt-library.md. Not lifecycle
     state, not a structured entry, not versioning or retirement.
  "cites optimizer" = a regex hit in the file body. Not intent, not genuine use; the
     upper bound deliberately includes negations ("did not use prompt-optimizer").
  scope = TOP-LEVEL *.md in ~/dev/share only. No recursion, no other extensions.
Neither number is evidence the procedure was followed -- only that it was mentioned.

Why this exists: the gap was measurable for months and nobody measured it twice,
so drift was invisible. Detection existed (8 open Dart tasks); measurement did not.

MATCHER HONESTY (CLAUDE.md measurement-validity rule). Deliverables are
identified by FILENAME, which is a lexical matcher: it undercounts deliverables
whose filename lacks the tokens, and overcounts non-deliverables that carry them
(anomaly analyses, handoffs). The output therefore reports a RANGE -- a strict
count and a loose count -- and never a single point estimate. A structured parse
with a narrow matcher is still a text match.

Exit codes: the MEASUREMENT paths (default, --json, --log) return 0 always -- advisory
only, never blocking. `--self-check` is the exception and returns 1 on fixture failure:
it is a test of this tool's own logic, not a drift measurement, and swallowing its
result would make it useless to any caller gating on the exit code. Raising the
measurement to blocking would need the false-positive class below to be measured first.

KNOWN LIMIT -- the corpus is not the event. This scans files in ~/dev/share, but most
prompts are emitted through the terminal / `Next:` channel and never become files here
(~475 close.json artifacts carry a next-prompt field over the same period). So this
measures a residue, and an agent that emits an unoptimized prompt without writing a file
IMPROVES the number. Treat a good reading as weak evidence; the load-bearing check would
observe the emission itself. Do not promote this to a gate without fixing that.

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

# CITATION MATCHERS -- a LOWER and an UPPER bound, never one number. Deliverables cite
# the procedure both as a filename and as a bare workflow name ("via prompt-optimizer-v1
# protocol", "prompt-optimizer Phase 5.5"), so a filename-only matcher understates by
# several times. A stricter matcher is not a safer matcher: it moves the error into the
# false-negative column, where nothing complains.
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


def _read_corpus() -> tuple[dict[str, str], list[str], str, bool]:
    """Read every candidate body once. -> (bodies, unreadable, lib_text, lib_readable).

    Separated from classification so that _classify below is PURE and can be driven
    by fixtures. A self-check that can only observe the live tree cannot tell working
    logic apart from a corpus that happens not to expose the bug.
    """
    try:
        lib_text = LIBRARY.read_text(errors="replace")
        lib_readable = True
    except OSError:
        # exists()-then-read() is a TOCTOU race, and a missing library must not
        # silently render every file "unregistered" -- that reads as a 0% hygiene
        # catastrophe when the real fault is a missing index.
        lib_text, lib_readable = "", False
    bodies: dict[str, str] = {}
    unreadable: list[str] = []
    for p in sorted(SHARE.glob("*.md")):
        try:
            bodies[p.name] = p.read_text(errors="replace")
        except OSError:
            unreadable.append(p.name)
    return bodies, unreadable, lib_text, lib_readable


def _classify(names: list[str], bodies: dict[str, str], lib_text: str) -> dict:
    """Pure. No I/O -- every input is passed in, so fixtures can exercise it."""
    registered, cite_lo, cite_hi, unreadable = [], [], [], []
    for name in names:
        body = bodies.get(name)
        if body is None:
            unreadable.append(name)
            continue  # excluded from citation rates, never counted as non-citing
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
    }


def _collect(regex: re.Pattern, all_names: list[str]) -> list[str]:
    return [
        n for n in all_names
        if n not in EXCLUDE and not n.startswith(EXCLUDE_PREFIX) and regex.search(n)
    ]


def measure() -> dict:
    bodies, unreadable_all, lib_text, lib_readable = _read_corpus()
    all_names = sorted(set(bodies) | set(unreadable_all))
    strict = _classify(_collect(STRICT_RE, all_names), bodies, lib_text)
    loose = _classify(_collect(LOOSE_RE, all_names), bodies, lib_text)
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
            # REGISTRATION IS A COUNT, NOT A RATE -- deliberately. IDEA-10101 scopes
            # prompt-library.md to *reusable* prompts, but most files matched here are
            # dated one-off handoffs the registry never wanted. Eligibility needs human
            # judgement, so any percentage would be against the wrong population.
            "registered": len(data["registered"]),
            # Citation IS meaningful over all deliverables, and is reported as a BOUND.
            "cites_lo": len(data["cite_lo"]),
            "cites_hi": len(data["cite_hi"]),
            "cites_lo_pct": round(100 * len(data["cite_lo"]) / scored, 1) if scored else None,
            "cites_hi_pct": round(100 * len(data["cite_hi"]) / scored, 1) if scored else None,
            "library_readable": lib_readable,
        }
        # Warn, never raise: an AssertionError would violate the exit-0-always contract,
        # and `python -O` strips asserts entirely -- the opposite failure.
        if len(data["cite_lo"]) > len(data["cite_hi"]):
            warnings.append(f"{label}: strict citations exceed loose -- the bounds are inverted")
        if data["unreadable"]:
            warnings.append(f"{label}: {len(data['unreadable'])} unreadable file(s), excluded from rates")
        if not lib_readable:
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
    checks: list[tuple[str, bool]] = []  # (failure message, failed?) -- count is derived

    def check(msg: str, ok: bool) -> None:
        checks.append((msg, not ok))

    # --- FIXTURES: synthetic inputs with known answers. These are the checks that
    # actually prove the logic; the live-tree checks below only prove it ran.
    FIX_BODIES = {
        "a-prompt.md": "see prompt-optimizer-v1.md for the procedure",  # strict + loose
        "b-prompt.md": "optimized via prompt-optimizer-v1 protocol",     # loose only
        "c-prompt.md": "no reference at all",                            # neither
        "d-prompt.md": "prompt-optimizer mentioned",                     # loose only
    }
    FIX_LIB = "| Thing | `~/dev/share/a-prompt.md` | IDEA-1 |"
    fx = _classify(sorted(FIX_BODIES), FIX_BODIES, FIX_LIB)
    check(f"fixture: strict citations {fx['cite_lo']} != ['a-prompt.md']",
          fx["cite_lo"] == ["a-prompt.md"])
    check(f"fixture: loose citations {fx['cite_hi']} != a,b,d",
          fx["cite_hi"] == ["a-prompt.md", "b-prompt.md", "d-prompt.md"])
    check(f"fixture: registered {fx['registered']} != ['a-prompt.md']",
          fx["registered"] == ["a-prompt.md"])
    # A name absent from bodies must land in unreadable, never in a citation list.
    fx2 = _classify(["ghost.md"], {}, "")
    check("fixture: missing body not routed to unreadable",
          fx2["unreadable"] == ["ghost.md"] and not fx2["cite_hi"])
    # Strict must never exceed loose on any input -- the bound that makes the range honest.
    check("fixture: strict citations exceed loose", len(fx["cite_lo"]) <= len(fx["cite_hi"]))

    # --- LIVE TREE: these prove the matcher is pointed at something real.
    bodies, unreadable_all, lib_text, _ = _read_corpus()
    all_names = sorted(set(bodies) | set(unreadable_all))
    s = set(_collect(STRICT_RE, all_names))
    l = set(_collect(LOOSE_RE, all_names))
    check("live: strict matcher selected 0 files -- matcher or path is broken", bool(s))
    check(f"live: strict is not a subset of loose: {sorted(s - l)[:3]}", s <= l)
    check("live: self-referential file leaked into the deliverable set",
          not any(n in (s | l) for n in ("prompt-library.md", "prompt-optimizer-v1.md")))
    m = measure()
    for label in ("strict", "loose"):
        b = m[label]
        check(f"live: {label} registered > total -- denominator is wrong",
              b["registered"] <= b["total"])
        for k in ("cites_lo_pct", "cites_hi_pct"):
            check(f"live: {label} {k} out of range",
                  b[k] is None or 0 <= b[k] <= 100)
    check("live: verdict UNKNOWN despite live data -- verdict logic broken",
          not (m["strict"]["total"] and m["verdict"] == "UNKNOWN"))

    failures = [msg for msg, failed in checks if failed]
    for f in failures:
        print(f"  [FAIL/self-check] {f}")
    if not failures:
        print(f"  [PASS/self-check] {len(checks)}/{len(checks)} checks proved the evaluation logic")
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
