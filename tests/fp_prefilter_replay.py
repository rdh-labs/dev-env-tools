#!/usr/bin/env python3
# GUARDS: fp_measure.py
"""Population replay for fp_measure prefilters (rules/testing.md "Population replay").

Verdict producer: fp_measure writes FP artifacts that evidence_gate._fp_artifact_admits_promotion reads.
A prefilter is sound only if it is a NECESSARY condition of firing: every file in which the scanner's
predicate fires must contain the prefilter substring (case-insensitive, as fp_measure tests it). This
suite checks that property directly, as an oracle independent of how fp_measure skips files, in ONE
pass over the real population. Provenance (2026-10-10 UTC): the CLI review leg (gpt-5.6-sol) proposed this
oracle as a COMPLEMENT to its preferred check, a dry-run measure() with a complete audit; the Agent leg
preferred moving the skip test into a shared fp_measure helper. Neither primary option is built here.
RESIDUAL RISK: the oracle does not exercise measure()'s own skip code, so a change to that code other
than case handling (which test_fp_measure.py test_prefilter_is_case_insensitive pins) could pass this
suite while fp_measure undercounts. FP_PREFILTER_AUDIT_EVERY=1 on a real run covers it at run time.

  1. Instrument: tests/test_fp_prefilter_audit.py must pass first.
  2. Population: every non-sidechain ~/.claude/projects/*/*.jsonl (fp_measure's corpus and policy),
     read once. FAIL VACUOUS below MIN_FILES usable files, and FAIL when unreadable files plus
     files with parse failures exceed MAX_LOSS_SHARE (lost files cannot be judged).
  3. Coverage: every prefiltered scanner must fire somewhere in the population (else its check is
     vacuous) and the production scanner set must be non-empty.
  4. Threshold (absolute, PROVISIONAL 2026-10-10 UTC): firing files that lack the prefilter = 0 per scanner.
  5. Witnesses (review round 2): FIXTURE, the same scan() and hidden() on one synthetic file where a14h
     fires through "**You**:" without the literal "you:", must report exactly 1 hidden file; this kills
     a broken comparison, e.g. one that always finds the prefilter. M2, an inverted check on the real
     population, must flag > 0 files per firing scanner. (A never-present-sentinel mutant was dropped:
     it only re-counted firing files.)
Exit 0 pass, 1 fail. Read-only: writes no artifact and no log.
"""
from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ["EVIDENCE_GATE_NO_LOG"] = "1"
HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent
sys.path.insert(0, str(TOOLS))
sys.argv = [sys.argv[0]]
import fp_measure as F  # noqa: E402

MIN_FILES = 200
MAX_LOSS_SHARE = 0.05   # PROVISIONAL: fp_measure artifacts of 2026-10-10 UTC show 57 parse-failure ROWS over 3,183 files; this suite counts FILES with any failure
FIXTURE_TEXT = ("Body text explaining the change in enough words to pass the minimum length check, "
                "padding padding padding padding padding padding padding padding padding padding.\n\n"
                "Done: changed the file\nOpen: Nothing\n**You**: Nothing. If you want more, say so.")


def scan(paths: list[str], pre: dict[str, str]):
    """One pass: per scanner, the files where its predicate fires and the files containing its prefilter."""
    firing = {sid: set() for sid in pre}
    present = {sid: set() for sid in pre}
    fires = {sid: 0 for sid in pre}
    unreadable = parse_loss = 0
    for fp in paths:
        try:
            raw = open(fp, encoding="utf-8", errors="replace").read()
        except OSError:
            unreadable += 1
            continue
        texts, failed = F._assistant_texts_from_raw(raw)
        if failed:
            parse_loss += 1
        for sid, p in pre.items():
            if re.search(re.escape(p), raw, re.IGNORECASE):
                present[sid].add(fp)
            pred = F.SCANNER_PREDICATES[sid][0]
            n = sum(1 for t in texts if t and pred(t))
            if n:
                fires[sid] += n
                firing[sid].add(fp)
    return firing, present, fires, unreadable, parse_loss


def fixture_hidden(prefilter: str) -> int:
    """Hidden-fire count for one synthetic file where a14h fires without its prefilter (expected 1)."""
    rows = [{"type": "user", "message": {"role": "user", "content": "hi"}},
            {"type": "assistant", "message": {"role": "assistant",
                                              "content": [{"type": "text", "text": FIXTURE_TEXT}]}}]
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "t.jsonl"
        p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        firing, present, _, _, _ = scan([str(p)], {"a14h": prefilter})
        return hidden(firing, present)["a14h"]


def hidden(firing: dict[str, set[str]], present: dict[str, set[str]], invert: bool = False) -> dict[str, int]:
    """Per scanner, the number of firing files where the prefilter is absent (or present, if inverted)."""
    return {sid: sum(1 for f in files if (f in present[sid]) == invert) for sid, files in firing.items()}


def main() -> int:
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                        str(HERE / "test_fp_prefilter_audit.py")], capture_output=True, text=True)
    last = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
    print(f"[1] instrument: rc={r.returncode} {last}")
    if r.returncode:
        print("FAIL: instrument tests do not pass")
        return 1
    pre = {k: v[2] for k, v in F.SCANNER_PREDICATES.items() if v[2]}
    if not pre:
        print("FAIL VACUOUS: no prefiltered scanner registered")
        return 1
    if "a14h" not in pre:
        print("FAIL: the fixture witness needs the a14h scanner, which has no prefilter now; update the fixture")
        return 1
    fx = fixture_hidden(pre["a14h"])
    print(f"[0] fixture witness: hidden files {fx} (must be 1)")
    if fx != 1:
        print("FAIL: the fixture witness did not report exactly 1 hidden file: either a14h no longer fires on "
              "FIXTURE_TEXT, the a14h prefilter changed, or the comparison is broken; population numbers would "
              "be meaningless until this is resolved")
        return 1
    files = sorted(f for f in glob.glob(os.path.expanduser("~/.claude/projects/*/*.jsonl"))
                   if not F._is_sidechain_path(f))
    firing, present, fires, unreadable, parse_loss = scan(files, pre)
    usable = len(files) - unreadable - parse_loss
    loss = (unreadable + parse_loss) / max(1, len(files))
    print(f"[2] population: {len(files)} non-sidechain transcript files; unreadable {unreadable}; "
          f"files with parse failures {parse_loss}; loss share {loss:.1%}")
    fails = []
    if usable < MIN_FILES:
        print(f"FAIL VACUOUS: {usable} usable files < {MIN_FILES}")
        return 1
    if loss > MAX_LOSS_SHARE:
        fails.append(f"loss share {loss:.1%} > {MAX_LOSS_SHARE:.0%}")
    base = hidden(firing, present)
    for sid in sorted(pre):
        print(f"[3/4] {sid}: prefilter {pre[sid]!r}; fires {fires[sid]} in {len(firing[sid])} files; "
              f"firing files without the prefilter {base[sid]}")
        if not firing[sid]:
            fails.append(f"{sid} never fires in the population (vacuous check)")
        if base[sid]:
            fails.append(f"{sid} prefilter hides fires in {base[sid]} file(s)")
    m2 = hidden(firing, present, invert=True)
    print(f"[5] M2 inverted check: {m2}  (each must be > 0)")
    survived = [s for s in pre if firing[s] and m2[s] == 0]
    if survived:
        fails.append(f"mutant M2 survived for {survived}")
    if fails:
        print("FAIL: " + "; ".join(fails))
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
