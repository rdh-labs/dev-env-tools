#!/usr/bin/env python3
"""Measure GOVERNED OUTCOMES — did the thing the controls exist to prevent actually happen?

WHY THIS EXISTS. DEC-329 changed the workspace's effectiveness standard from "how many
mechanisms exist" to "are the governed outcomes achieved". A session-end critique then found
DEC-329's execution path was NIL: `grep -rl "governed.outcome"` across tools/, ~/bin,
~/.claude/hooks and ~/.metrics returned nothing. It was a decision that changed a MEASUREMENT
STANDARD and shipped no MEASUREMENT — "a governance artifact whose reader cannot be named",
which is DEC-327's own falsifier turned on DEC-327's author. This is that measurement.

WHAT AN OUTCOME IS, and is not. An outcome is what the control exists to PREVENT, observed
after the fact in durable state. It is NOT:
  - how many controls exist (that is COST — DEC-329 keeps count for exactly that)
  - how often a control fires (that is FRICTION — the retracted fire_count proxy)
  - whether a control was heeded (that is COMPLIANCE, measured against the control, not the
    threat)
Each check below asks: is the bad thing present in durable state right now?

THE INTERPRETATION TRAP, stated because it is the honest limit of the whole approach.
A clean result has two causes and this tool CANNOT distinguish them:
  (a) the controls worked, or (b) nobody tried.
Outcomes are rare events. Absence of an incident is WEAK evidence of control effectiveness.
DEC-329 carries this as its own falsifier: "if outcome measurement cannot distinguish
'controls worked' from 'nobody tried', it is not measuring effectiveness either, and
DEC-329 needs revising rather than defending." Report the trend, never a pass/fail on absence.

Exit 0 when no adverse outcome is detected; 1 when one is; 2 when a check could not run --
which is UNKNOWN, never a pass (DEC-326).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SECRET_RE = re.compile(
    r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"          # jwt
    r"|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY"
    r"|\bAKIA[0-9A-Z]{16}\b"
    r"|\bgh[pousr]_[A-Za-z0-9]{20,}")

REPOS = [Path.home() / "dev/share",
         Path.home() / "dev/infrastructure/tools",
         Path.home() / "dev/infrastructure/dev-env-docs"]
OWN_LOG = Path.home() / ".metrics" / "governed-outcomes.jsonl"


def _git(repo: Path, *args, timeout=180):
    try:
        p = subprocess.run(["git", "-C", str(repo), *args],
                           capture_output=True, text=True, timeout=timeout, errors="replace")
        return p.stdout if p.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


# Files whose PURPOSE is carrying credential-shaped strings: scanner sources, FP-measurement
# corpora, test fixtures. Their matches are expected and are NOT adverse outcomes. They are
# reported SEPARATELY and never auto-hidden -- silently filtering them would make the number
# look clean, which is the defect this whole exercise exists to prevent.
EXPECTED_PATH_RE = re.compile(
    # PATH-SEGMENT anchored, never substring. The first version used bare `scanner|corpus`,
    # so `services/keyscanner_service/prod_config.py` was excused — an independent reviewer
    # constructed exactly that case with an AKIA-shaped key and got adverse=0, verdict CLEAN.
    r"(?:^|/)(?:tests?|fixtures?|testdata|corpus|corpora)(?:/|$)"
    r"|(?:^|/)(?:credential_scanner|sanitize-pii|governed-outcomes-check|"
    r"session-artifact-sweep|selfcheck-mutation-canary)\.py$"
    r"|(?:^|/)[^/]*-fp-[^/]*/"
    r"|(?:^|/)fp-measurement(?:/|$)", re.I)


def outcome_credential_in_history(days: int) -> dict:
    """THE OUTCOME: did a credential reach committed history? Not 'was one blocked'.

    Splits hits into EXPECTED (the file exists to carry the pattern -- scanner source, FP
    corpus, fixture) and NEEDS-REVIEW (everything else). Only NEEDS-REVIEW counts as adverse.
    Verified 2026-08-11: the first real run reported 1 adverse; both underlying hits were
    expected -- an FP-measurement corpus and this workspace's own scanner pattern list.
    """
    hits, scanned, unreadable, expected = [], 0, [], []
    for repo in REPOS:
        if not (repo / ".git").exists():
            unreadable.append(f"{repo.name}: not a repo")
            continue
        count = _git(repo, "log", "--all", "--oneline", f"--since={days}.days")
        diff = _git(repo, "log", "--all", "-p", f"--since={days}.days")
        if diff is None or count is None:
            unreadable.append(f"{repo.name}: git read failed")
            continue
        scanned += len([l for l in count.splitlines() if l.strip()])
        current = "?"
        for line in diff.splitlines():
            if line.startswith("+++ b/"):
                current = line[6:]
            elif line.startswith("+") and SECRET_RE.search(line):
                # PATH ONLY -- never the matched text. A report that echoes the secret has
                # moved the secret into the report.
                (expected if EXPECTED_PATH_RE.search(current) else hits).append(
                    f"{repo.name}/{current}")
    return {"outcome": "credential_in_committed_history", "adverse": len(set(hits)),
            "where": sorted(set(hits)), "expected_pattern_files": sorted(set(expected)),
            "commits_scanned": scanned, "unreadable": unreadable,
            "note": "a control-EFFECT measure: a blocked leak leaves no trace here, which is "
                    "the point -- prevention looks identical to nobody-trying"}


def outcome_artifacts_lost() -> dict:
    """THE OUTCOME: is session work living only in /tmp, past the retention horizon?"""
    tool = Path(__file__).resolve().parent / "session-artifact-sweep.py"
    if not tool.exists():
        return {"outcome": "artifacts_only_in_tmp", "adverse": None,
                "unreadable": ["sweep tool absent"]}
    try:
        p = subprocess.run([sys.executable, str(tool), "--all-sessions"],
                           capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"outcome": "artifacts_only_in_tmp", "adverse": None,
                "unreadable": [f"sweep failed: {exc}"]}
    m = re.search(r"(\d+) with unrescued artifacts", p.stdout)
    return {"outcome": "artifacts_only_in_tmp",
            "adverse": int(m.group(1)) if m else None,
            "unreadable": [] if m else ["could not parse sweep output"]}


def _count_override_log(f: Path, days: int) -> dict:
    """Pure counting over ONE log file, so fixtures can drive it without live state."""
    n, bad, authorised = 0, 0, 0
    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
    for line in f.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            bad += 1
            continue
        raw = str(r.get("ts") or r.get("timestamp") or "")
        try:
            t = datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
        except ValueError:
            bad += 1
            continue
        if t < cutoff:
            continue
        if r.get("event") == "override_used":
            n += 1
        else:
            authorised += 1
    return {"outcome": "destructive_breakglass_used", "adverse": n,
            "authorised_token_uses": authorised,
            "unreadable": [f"{bad} unparseable row(s)"] if bad else [],
            "note": "adverse counts ONLY event=override_used; event=token_used is the "
                    "authorised scoped-token path and is the control functioning."}


def outcome_destructive_overrides(days: int) -> dict:
    """THE OUTCOME: was the destructive-op confirmation BYPASSED via break-glass?

    SEMANTICS, read from the log before counting (2026-08-11) — the third time in one session
    that a metric was nearly built on an unread field. The log carries TWO event types:
      `token_used`     (205 of 221) — a SCOPED, short-TTL token was minted and used. This is
                       the AUTHORISED path. The control working as designed. NOT adverse.
      `override_used`  (16 of 221)  — the ~15-minute break-glass window was used, bypassing
                       per-op confirmation entirely. THIS is the adverse outcome.
    Counting all rows reported 15 "overrides" in 7 days when nearly all were the control
    functioning. Compare `fire_count` (measured ACK latency, not evasion) and
    `claim-probe-fires` (records detections, not effect).
    """
    f = Path.home() / ".claude" / "logs" / "destructive-overrides.jsonl"
    if not f.exists():
        return {"outcome": "destructive_breakglass_used", "adverse": 0,
                "authorised_token_uses": 0, "unreadable": [],
                "note": "no override log -- no break-glass ever recorded"}
    return _count_override_log(f, days)


def _verdict(checks):
    """Verdict from a list of check dicts. Extracted so fixtures can DRIVE it — the previous
    inline version could be sabotaged to always return CLEAN and every fixture still passed.
    Seventh untested fix in one session; the pattern is fixing without testing the fix.

    Precedence: ADVERSE > UNKNOWN > REVIEW_REQUIRED > CLEAN. REVIEW_REQUIRED exists because
    downgrading expected-path hits to nothing let a real leak in any path containing
    "test"/"corpus"/"scanner" read as CLEAN (independent review, 2026-08-11).
    """
    unknown = [c for c in checks if c.get("adverse") is None or c.get("unreadable")]
    adverse = sum(c["adverse"] for c in checks if isinstance(c.get("adverse"), int))
    expected_hits = sum(len(c.get("expected_pattern_files", [])) for c in checks)
    verdict = ("ADVERSE" if adverse else "UNKNOWN" if unknown
               else "REVIEW_REQUIRED" if expected_hits else "CLEAN")
    return adverse, unknown, expected_hits, verdict


def outcome_canary_survivors() -> dict:
    """THE OUTCOME: is the weekly mutation canary reporting survivors nobody has triaged?

    Added by CONSOLIDATION, not as a ninth tool. The canary logs to
    ~/.logs/selfcheck-mutation-canary.log and had NO reader — DEC-327's own rule ("every
    producer needs a consumer") violated by the tool built to enforce that class of finding.
    This file already runs weekly, so the log gets a consumer without adding a mechanism,
    which is what CONSOLIDATE-AND-NET-SUBTRACT asks for.

    A SURVIVOR is not automatically a defect (it may be defence in depth) — but an
    UNTRIAGED one is, because nobody looked. Exit-2/NOT APPLIED lines are UNKNOWN state.
    """
    log = Path.home() / ".logs" / "selfcheck-mutation-canary.log"
    if not log.exists():
        return {"outcome": "canary_survivors_untriaged", "adverse": None,
                "unreadable": ["canary log absent — it has never run, which is not evidence "
                               "the tools are sound"]}
    try:
        text = log.read_text(errors="replace")
    except OSError as exc:
        return {"outcome": "canary_survivors_untriaged", "adverse": None,
                "unreadable": [f"canary log unreadable: {exc}"]}
    # Only the LAST run matters; earlier survivors may already be fixed.
    runs = text.split("SELF-CHECK MUTATION CANARY")
    last = runs[-1] if runs else ""
    lines = [l.strip() for l in last.splitlines()]
    survivors = [l for l in lines if l.startswith("SURVIVED")]
    unapplied = [l for l in lines if "NOT APPLIED" in l]
    return {"outcome": "canary_survivors_untriaged", "adverse": len(survivors),
            "survivors": survivors[:6],
            "unreadable": [f"{len(unapplied)} mutation(s) could not be applied — refactored "
                           f"code, UNKNOWN, never a pass"] if unapplied else [],
            "note": "a survivor may be defence-in-depth; an UNTRIAGED survivor is the finding"}


def run(days: int) -> dict:
    checks = [outcome_credential_in_history(days), outcome_artifacts_lost(),
              outcome_destructive_overrides(days), outcome_canary_survivors()]
    adverse, unknown, expected_hits, verdict = _verdict(checks)
    # UNKNOWN outranks CLEAN: a check that could not run is not evidence of a good outcome.
    return {"verdict": verdict, "adverse_total": adverse, "window_days": days,
            "expected_pattern_hits": expected_hits,
            "checks": checks, "unknown_count": len(unknown)}


def self_check() -> int:
    ok = []

    fake_unknown = {"verdict": None}
    checks = [{"outcome": "a", "adverse": 0, "unreadable": []},
              {"outcome": "b", "adverse": None, "unreadable": ["failed"]}]
    adverse, unknown, expected_hits, verdict = _verdict(checks)
    v = "ADVERSE" if adverse else ("UNKNOWN" if unknown else "CLEAN")
    ok.append(("one unreadable check forces UNKNOWN", v == "UNKNOWN"))
    checks2 = [{"outcome": "a", "adverse": 2, "unreadable": []}]
    a2 = sum(c["adverse"] for c in checks2 if isinstance(c.get("adverse"), int))
    ok.append(("an adverse outcome outranks everything", ("ADVERSE" if a2 else "CLEAN") == "ADVERSE"))
    ok.append(("a JWT-shaped added line is detected",
               bool(SECRET_RE.search("eyJhbGciOiJIUzI1NiJ9.eyJpZCI6OX0"))))
    ok.append(("ordinary prose is not detected", not SECRET_RE.search("just some normal text")))
    ok.append(("a scanner/corpus path is EXPECTED, not adverse",
               bool(EXPECTED_PATH_RE.search("x/checker-fp-review/corpus.jsonl"))))
    ok.append(("an ordinary source path is NOT excused",
               not EXPECTED_PATH_RE.search("projects/app/config.py")))
    # SYNTHETIC, not the live log. The previous version asserted
    # authorised_token_uses > adverse against real local state, so it would fail on a machine
    # whose log was empty or override-heavy even with correct logic — a fixture that depends
    # on the environment is not a fixture.
    import tempfile as _tf
    with _tf.TemporaryDirectory() as _td:
        _f = Path(_td) / "ov.jsonl"
        _now = datetime.now(timezone.utc).isoformat()
        _f.write_text("\n".join([
            json.dumps({"event": "token_used", "ts": _now}),
            json.dumps({"event": "token_used", "ts": _now}),
            json.dumps({"event": "override_used", "ts": _now}),
        ]) + "\n")
        d = _count_override_log(_f, 3650)
    ok.append(("token_used is NOT adverse — it is the control working",
               d["authorised_token_uses"] == 2 and d["adverse"] == 1))
    ok.append(("the adverse count is break-glass only",
               d["outcome"] == "destructive_breakglass_used"))
    ok.append(("the report NEVER contains matched secret text",
               "where" in outcome_credential_in_history(0) and
               all(isinstance(x, str) and "eyJ" not in x
                   for x in outcome_credential_in_history(0)["where"])))
    clean = [{"adverse": 0, "unreadable": []}]
    ok.append(("no hits at all is CLEAN", _verdict(clean)[3] == "CLEAN"))
    exp = [{"adverse": 0, "unreadable": [], "expected_pattern_files": ["a/test/x.py"]}]
    ok.append(("an EXPECTED-path hit is REVIEW_REQUIRED, never CLEAN",
               _verdict(exp)[3] == "REVIEW_REQUIRED"))
    ok.append(("UNKNOWN outranks REVIEW_REQUIRED",
               _verdict([{"adverse": None, "unreadable": ["x"],
                          "expected_pattern_files": ["a/test/x.py"]}])[3] == "UNKNOWN"))
    ok.append(("ADVERSE outranks everything",
               _verdict([{"adverse": 1, "unreadable": ["x"],
                          "expected_pattern_files": ["a/test/x.py"]}])[3] == "ADVERSE"))

    # DRIVE run() — a reviewer hardcoded run() to CLEAN and every fixture still passed,
    # because nothing called it. Monkeypatch the three checks to known values.
    import unittest.mock as _mock
    with _mock.patch(__name__ + ".outcome_credential_in_history",
                     return_value={"outcome": "c", "adverse": 0, "unreadable": [],
                                   "expected_pattern_files": ["svc/keyscanner_service/x.py"]}), \
         _mock.patch(__name__ + ".outcome_artifacts_lost",
                     return_value={"outcome": "a", "adverse": 0, "unreadable": []}), \
         _mock.patch(__name__ + ".outcome_destructive_overrides",
                     return_value={"outcome": "d", "adverse": 0, "unreadable": []}), \
         _mock.patch(__name__ + ".outcome_canary_survivors",
                     return_value={"outcome": "k", "adverse": 0, "unreadable": []}):
        ok.append(("run() must NOT return CLEAN when an expected-path hit exists",
                   run(7)["verdict"] == "REVIEW_REQUIRED"))
    # DRIVE THE ACTUAL SPLIT by feeding a fabricated diff through the real function. A
    # reviewer forced the split to always-"expected" and all fixtures still passed, because
    # they tested the regex OBJECT and never the code path that consumes it.
    import unittest.mock as _m
    _diff = ("+++ b/services/keyscanner_service/prod_config.py\n"
             "+AWS_KEY = 'AKIA" + "IOSFODNN7EXAMPLE'\n"
             "+++ b/pkg/tests/fixtures.py\n"
             "+SAMPLE = 'AKIA" + "IOSFODNN7EXAMPLE'\n")
    with _m.patch(__name__ + "._git", side_effect=lambda repo, *a, **k:
                  ("abc1234 x" if "--oneline" in a else _diff)), \
         _m.patch.object(Path, "exists", lambda self: True):
        _r = outcome_credential_in_history(7)
    ok.append(("a real secret in a keyscanner_service path IS adverse (the split, not the regex)",
               _r["adverse"] >= 1))
    ok.append(("a secret in a genuine tests/ dir is expected, not adverse",
               any("tests/" in e for e in _r["expected_pattern_files"])))

    # Regex-level checks, kept as a cheaper first line.
    ok.append(("a credential in a path merely CONTAINING 'scanner' is NOT excused",
               not EXPECTED_PATH_RE.search("services/keyscanner_service/prod_config.py")))
    ok.append(("a real tests/ directory IS excused",
               bool(EXPECTED_PATH_RE.search("pkg/tests/data.json"))))
    ok.append(("'corpus' as a substring of another word is NOT excused",
               not EXPECTED_PATH_RE.search("app/corpusenginereal/secrets.py")))

    import tempfile as _tmp
    with _tmp.TemporaryDirectory() as _td2:
        with _mock.patch.object(Path, "home", staticmethod(lambda: Path(_td2))):
            (Path(_td2) / ".logs").mkdir(exist_ok=True)
            (Path(_td2) / ".logs" / "selfcheck-mutation-canary.log").write_text(
                "SELF-CHECK MUTATION CANARY\n  SURVIVED  x.py: guard removed\n")
            _c = outcome_canary_survivors()
    ok.append(("an untriaged canary SURVIVOR is an adverse outcome", _c["adverse"] == 1))
    with _mock.patch.object(Path, "home", staticmethod(lambda: Path("/nonexistent-xyz-abc"))):
        _c2 = outcome_canary_survivors()
    ok.append(("a MISSING canary log is UNKNOWN, never clean",
               _c2["adverse"] is None and bool(_c2["unreadable"])))

    failed = [m for m, good in ok if not good]
    for m in failed:
        print(f"  [FAIL/self-check] {m}")
    if not failed:
        print(f"  [PASS/self-check] {len(ok)}/{len(ok)} checks proved the outcome logic")
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--log", action="store_true")
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args()

    if args.self_check:
        print("GOVERNED OUTCOMES: self-check")
        return self_check()

    r = run(args.days)
    r["ts"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    if args.log:
        try:
            OWN_LOG.parent.mkdir(parents=True, exist_ok=True)
            with OWN_LOG.open("a") as fh:
                fh.write(json.dumps(r) + "\n")
        except OSError as exc:
            print(f"  [WARN] could not write {OWN_LOG}: {exc}", file=sys.stderr)

    if args.json:
        print(json.dumps(r, indent=2))
        return 0 if r["verdict"] == "CLEAN" else (2 if r["verdict"] == "UNKNOWN" else 1)

    print(f"GOVERNED OUTCOMES ({args.days}d): {r['verdict']}")
    for c in r["checks"]:
        mark = "  " if c.get("adverse") == 0 else "! "
        print(f"{mark}{c['outcome']:36} adverse={c.get('adverse')}")
        for w in c.get("where", []):
            print(f"      NEEDS REVIEW: {w}")
        for e in c.get("expected_pattern_files", []):
            # PRINTED, not merely counted. The previous version said "NOT hidden" and showed
            # only a number — the comment was false and a reviewer caught it.
            print(f"      REVIEW (expected-pattern path, not counted adverse): {e}")
        for u in c.get("unreadable", []):
            print(f"      UNKNOWN: {u}")
    print("\n  DEC-329: effectiveness is measured here, not by mechanism count (that is COST)")
    print("  and not by how often gates fire (that is FRICTION).")
    print("  TRAP: a CLEAN result cannot distinguish 'the controls worked' from 'nobody")
    print("  tried'. Read the TREND across runs; never treat one clean run as proof.")
    return 0 if r["verdict"] == "CLEAN" else (2 if r["verdict"] == "UNKNOWN" else 1)


if __name__ == "__main__":
    sys.exit(main())
