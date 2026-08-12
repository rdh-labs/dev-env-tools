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
from datetime import datetime, timedelta, timezone
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


# The date /handoff began reading its artifact back (§12). Rows before this carry no
# `artifact_verified` field and are UNKNOWN by construction — never counted as verified.
HANDOFF_READBACK_CUTOVER = "2026-08-12"


def _handoff_rows(path: Path, days: int) -> tuple[list[dict], list[str]]:
    """Pure parse over ONE log file, so fixtures drive it without live state."""
    if not path.exists():
        return [], [f"{path.name} absent"]
    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
    rows, bad = [], 0
    for line in path.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if not isinstance(r, dict):     # `[]` and `"x"` are valid JSON and have no .get
            bad += 1
            continue
        try:
            ts = datetime.fromisoformat(str(r.get("timestamp", "")).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except ValueError:
            bad += 1
            continue
        if ts.timestamp() >= cutoff:
            r["_ts"] = ts          # normalized UTC; callers MUST NOT compare timestamps lexically
            rows.append(r)
    return rows, ([f"{bad} unparseable row(s)"] if bad else [])


def outcome_handoff_log_artifact_disagreement(days: int, path: Path | None = None) -> dict:
    """THE OUTCOME: do the handoff LOG and the handoff ARTIFACTS disagree, in EITHER direction?

    RENAMED 2026-08-12 (/simplify, grounded MEDIUM). It was `outcome_handoff_` + `unverified` (name assembled here so a future rename cannot
    silently rewrite this explanation the way one already did), which
    described only log->artifact. When the artifact->log direction was added the OUTCOME STRING
    was updated and the FUNCTION NAME was not -- so the name asserted half of what it did.

    Consumer for the `artifact_verified` field added to /handoff on 2026-08-12. Before that,
    handoff.jsonl was pure self-report: `result` was computed from knowledge-capture counts and
    `scope`, so it read "success" whenever scope was project or global — i.e. always. Session
    4ac72061 logged `result: "success"` on 2026-08-11 with no .md on disk until the next day,
    and every disk-side check (A45, A62-T5/T9, handoff_validation.py) keys off this log.

    Two distinct adverse signals, deliberately NOT merged:
      - a row that read back FALSE, or `result: attempted` — the handoff did not persist;
      - a post-cutover row with NO `artifact_verified` field at all, which means /handoff is
        not running the read-back. That is UNKNOWN, not clean: this check monitors its own
        upstream fix, so silent removal of the read-back surfaces here rather than passing.
    """
    rows, unreadable = _handoff_rows(
        path or Path.home() / ".claude" / "logs" / "handoff.jsonl", days)
    adverse = sum(1 for r in rows
                  if ("artifact_verified" in r and r["artifact_verified"] is not True)
                  or r.get("result") == "attempted")
    _cut = datetime.fromisoformat(HANDOFF_READBACK_CUTOVER).replace(tzinfo=timezone.utc)
    unfielded = [r for r in rows if "artifact_verified" not in r]
    post = [r for r in unfielded if r["_ts"] >= _cut]      # normalized compare, never lexical:
    pre = [r for r in unfielded if r["_ts"] < _cut]        # "2026-08-11T23:30-01:00" IS post-cutover
    if post:
        unreadable.append(f"{len(post)} post-cutover row(s) with no artifact_verified field "
                          f"— /handoff read-back is not running")
    if pre:
        # Q3(a): a legacy row claiming success with no artifact reads identically to a real one.
        # The 4ac72061 incident IS such a row. UNKNOWN, never clean — ages out of the window.
        unreadable.append(f"{len(pre)} pre-cutover row(s) predate the read-back field "
                          f"— artifact state UNKNOWN, not clean")
    # RECONCILIATION, both directions (outbox prior art, register rows 165-166). Until now this
    # checked only LOG -> ARTIFACT. The inverse — an artifact with no log row — is the window an
    # ordered-write-plus-read-back leaves open, and it is precisely the 8-9 orphan handoffs this
    # workspace could not explain. Checking one direction and calling it reconciliation is how
    # the inverse class stayed invisible.
    orphan_artifacts = []
    if path is None:                      # live mode only; fixtures drive the log side
        hdir = Path.home() / ".claude" / "handoffs"
        if hdir.is_dir():
            logged = {r.get("session_id") for r in rows if r.get("session_id")}
            for f in hdir.glob("handoff-*.md"):
                m = re.match(r"handoff-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                             r"[0-9a-f]{4}-[0-9a-f]{12})-", f.name)
                if m and m.group(1) not in logged:
                    try:
                        # age-only: mtime bounds the reporting window. No identity is
                        # derived from it — the session id comes from the FILENAME, which
                        # is authoritative for that artifact.
                        if f.stat().st_mtime >= datetime.now(timezone.utc).timestamp() - days*86400:
                            orphan_artifacts.append(f.name)
                    except OSError:
                        pass

    return {"outcome": "handoff_log_artifact_disagreement",
            "orphan_artifacts": sorted(orphan_artifacts),
            # UNKNOWN only when there is nothing adverse to report. A CLI review recommended
            # `None if unreadable else adverse`; applied verbatim, it ERASED real counts —
            # `_verdict` sums only int `adverse` values, so a genuine finding vanished from the
            # JSON entirely and ADVERSE could never outrank UNKNOWN. Verified 2026-08-12 with a
            # real adverse row + one legacy row: verdict UNKNOWN, adverse_total 0.
            # A real count always survives; incomplete evidence still shows in `unreadable`.
            # BOTH directions count. A clean log side with orphaned artifacts is not clean.
            "adverse": (adverse + len(orphan_artifacts)) or (None if unreadable else 0),
            "unreadable": unreadable}


HANDOFF_SID_RE = re.compile(r"Session ID:?\**\s*`?([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                            r"[0-9a-f]{4}-[0-9a-f]{12})`?", re.I)


def outcome_handoff_misfiled(path: Path | None = None) -> dict:
    """THE OUTCOME: does a handoff name a session other than the one it is filed under?

    HONEST PROVENANCE — read this before trusting the check's framing. I built this believing
    `handoff-f28dbcbc-…md` declared a DIFFERENT session id in its body, and called the class
    "misfiled handoffs". That was FALSE (ANOMALY-REGISTER row 160): the file contains its own
    uuid three times and merely REFERENCES another. I had misread my own scan output, which
    printed the alphabetically-first uuid rather than an internal id. The founding example
    evaporated; the check did not, because it independently surfaced 5 real cases.

    WHAT IT ACTUALLY MEASURES, stated narrowly: a handoff whose body declares a `Session ID:`
    different from its filename, OR (absent that label) whose body names full uuids and NEVER
    its own. 382 of 387 comparable handoffs DO name their own id, so the 5 that do not are
    unusual. **Whether "unusual" means MISFILED is UNKNOWN and UNTRIAGED** — do not let this
    docstring imply a mechanism. The last mechanism proposed here was wrong.

    WHY IT MATTERS IF REAL: every handoff gate globs by FILENAME, so a handoff filed under the
    wrong id is indistinguishable from an absent one — the session reads as never having handed
    off, permanently, while its work sits on disk under another name.

    KNOWN FALSE-NEGATIVE HISTORY: the first version required a literal `Session ID:` label and
    reported adverse=0; the second read only the first 4KB and also reported 0. Both were clean
    on the very corpus that motivated the check. It reports what it can compare (`compared`) so
    a low denominator is visible rather than passing silently.
    """
    hdir = path or (Path.home() / ".claude" / "handoffs")
    if not hdir.is_dir():
        return {"outcome": "handoff_misfiled", "adverse": None,
                "unreadable": ["handoffs dir absent"]}
    bad, unreadable, checked = [], [], 0
    for f in sorted(hdir.glob("handoff-*.md")):
        m = re.match(r"handoff-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})-", f.name)
        if not m:
            continue                      # deprecated short-form names carry no full id to compare
        try:
            # WHOLE file, not a head slice. A 4000-char window missed the founding positive
            # (f28dbcbc declares its true id well past 4KB). Handoffs are ~10KB; reading
            # them fully costs nothing and a truncated read is a silent false negative.
            head = f.read_text(errors="replace")
        except OSError as exc:
            unreadable.append(f"{f.name}: {exc}")
            continue
        # A labelled `Session ID:` is the strong signal, but not every handoff carries one --
        # the f28dbcbc case (the incident that motivated this check) has NO label and was
        # reported CLEAN by the first version. A check that reads clean on its own founding
        # positive is vacuous. Fall back to the SET of full uuids in the body.
        labelled = HANDOFF_SID_RE.search(head)
        body_ids = set(re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                                  head))
        if labelled:
            checked += 1
            if labelled.group(1) != m.group(1):
                bad.append(f"{f.name} declares Session ID {labelled.group(1)}")
        elif body_ids:
            # Bodies legitimately mention OTHER sessions (peer coordination), so a differing id
            # is not enough. Adverse only when the filename's own id appears NOWHERE in the body.
            checked += 1
            if m.group(1) not in body_ids:
                bad.append(f"{f.name} body names {sorted(body_ids)[0]} and never its own id")
    return {"outcome": "handoff_misfiled", "adverse": len(bad) if (checked or not unreadable) else None,
            "where": bad, "compared": checked, "unreadable": unreadable,
            "note": "a misfiled handoff is indistinguishable from an absent one to every "
                    "filename-globbing gate"}


HEARTBEAT = Path.home() / ".metrics" / "scheduled-check-heartbeat.jsonl"

# Cadence in hours, from the live schedule. Grace is 2x: one missed run is noise (a closed
# laptop), two is a signal.
EXPECTED_CADENCE_H = {
    "artifact-sweep": 12, "gate-ledger-archive": 1, "selfcheck-canary": 168,
    "anomaly-conversion": 168, "correction-rate": 168, "gate-ack": 24,
    "governed-outcomes": 168,
}


def outcome_check_not_running(path: Path | None = None) -> dict:
    """THE OUTCOME: has a scheduled check silently STOPPED RUNNING?

    Consumer for the heartbeat `scheduled-check-runner.sh` writes. Without it the heartbeat is
    another artifact nobody reads -- the exact defect it exists to fix, one layer up. A detector
    that stops running produces the same silence as a clean result.

    NOT compliance measurement (see this file's contract). This is the controls' own BLINDNESS
    in durable state: if a check is not running, every outcome it covers is unmeasured.

    HONEST LIMIT, circular and stated: this runs on the schedule it audits. If THIS stops,
    nothing detects it. Six of seven become falsifiable; the seventh needs an external observer
    this workspace does not have.
    """
    hb = path or HEARTBEAT
    if not hb.exists():
        return {"outcome": "scheduled_check_not_running", "adverse": None,
                "stale": [], "never_seen": sorted(EXPECTED_CADENCE_H),
                "unreadable": ["no heartbeat yet -- no scheduled run has completed"],
                "note": "expected before the first scheduled run; UNKNOWN, never a pass"}
    last, bad = {}, 0
    for line in hb.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if not isinstance(r, dict) or "check" not in r:
            bad += 1
            continue
        try:
            ts = datetime.fromisoformat(str(r.get("ts", "")).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except ValueError:
            bad += 1
            continue
        k = r["check"]
        if k not in last or ts > last[k]:
            last[k] = ts
    now = datetime.now(timezone.utc)
    stale, never = [], []
    for name, cad in EXPECTED_CADENCE_H.items():
        if name not in last:
            never.append(name)
        elif (now - last[name]).total_seconds() > cad * 3600 * 2:
            stale.append(f"{name}: last ran {(now - last[name]).total_seconds() / 3600:.1f}h ago, "
                         f"cadence {cad}h")
    return {"outcome": "scheduled_check_not_running", "adverse": len(stale),
            "stale": stale, "never_seen": sorted(never),
            "unreadable": ([f"{bad} unparseable heartbeat row(s)"] if bad else [])
                          + ([f"{len(never)} check(s) have never heartbeated"] if never else []),
            "note": "a check that stopped running is indistinguishable from a clean one "
                    "without this"}


def run(days: int) -> dict:
    checks = [outcome_credential_in_history(days), outcome_artifacts_lost(),
              outcome_destructive_overrides(days), outcome_canary_survivors(),
              outcome_handoff_log_artifact_disagreement(days),
              outcome_handoff_misfiled(),
              outcome_check_not_running()]
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
                     return_value={"outcome": "k", "adverse": 0, "unreadable": []}), \
         _mock.patch(__name__ + ".outcome_handoff_log_artifact_disagreement",
                     return_value={"outcome": "h", "adverse": 0, "unreadable": []}), \
         _mock.patch(__name__ + ".outcome_handoff_misfiled",
                     return_value={"outcome": "m", "adverse": 0, "unreadable": []}), \
         _mock.patch(__name__ + ".outcome_check_not_running",
                     return_value={"outcome": "r", "adverse": 0, "unreadable": []}):
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

    # --- outcome_handoff_log_artifact_disagreement: drive the REAL function over a temp log ------------
    # NOT a mock. The verdict-precedence fixture above mocks this check to a canned value;
    # if that were the only coverage, gutting the function would leave every check green --
    # the exact vacuous-fixture defect this file's canary exists to catch.
    import tempfile as _tf
    with _tf.TemporaryDirectory() as _td:
        _log = Path(_td) / "handoff.jsonl"
        _now = datetime.now(timezone.utc).isoformat()

        def _write(*rows):
            _log.write_text("".join(json.dumps(r) + "\n" for r in rows))

        _write({"timestamp": _now, "result": "success", "artifact_verified": True})
        r = outcome_handoff_log_artifact_disagreement(30, _log)
        ok.append(("a verified handoff is not adverse", r["adverse"] == 0 and not r["unreadable"]))

        # artifact_verified TRUE, so ONLY the result=attempted clause can make this adverse.
        # The original fixture set it False and passed via the other clause -- vacuous.
        _write({"timestamp": _now, "result": "attempted", "artifact_verified": True})
        ok.append(("result=attempted is ADVERSE on its own clause",
                   outcome_handoff_log_artifact_disagreement(30, _log)["adverse"] == 1))
        # H1 regression guard: a real adverse count must SURVIVE an unreadable row.
        _write({"timestamp": _now, "result": "success", "artifact_verified": False},
               {"timestamp": "2026-01-02T03:04:05", "result": "success"})
        _r = outcome_handoff_log_artifact_disagreement(3650, _log)
        ok.append(("a real adverse count survives unreadable rows (ADVERSE > UNKNOWN)",
                   _r["adverse"] == 1 and _r["unreadable"]))
        # H3: a valid-JSON non-object line must not crash.
        _log.write_text("[]\n" + json.dumps({"timestamp": _now, "result": "attempted",
                                             "artifact_verified": True}) + "\n")
        _r = outcome_handoff_log_artifact_disagreement(30, _log)
        ok.append(("a non-object JSON line is counted bad, not fatal",
                   _r["adverse"] == 1 and any("unparseable" in u for u in _r["unreadable"])))

        # The incident: success claimed, artifact absent. Must not read as clean.
        _write({"timestamp": _now, "result": "success", "artifact_verified": False})
        ok.append(("success + artifact_verified False is ADVERSE",
                   outcome_handoff_log_artifact_disagreement(30, _log)["adverse"] == 1))

        # Self-monitoring: a post-cutover row with NO field means /handoff stopped reading back.
        _write({"timestamp": _now, "result": "success"})
        r = outcome_handoff_log_artifact_disagreement(30, _log)
        ok.append(("post-cutover row missing the field is UNKNOWN, never clean",
                   r["adverse"] is None and any("read-back is not running" in u for u in r["unreadable"])))

        # A legacy row claiming success with no artifact is INDISTINGUISHABLE from a real one --
        # the 4ac72061 incident is exactly such a row. UNKNOWN, never clean. These age out of the
        # 7-day window, so this does not scream forever.
        _write({"timestamp": "2026-01-02T03:04:05", "result": "success"})
        r = outcome_handoff_log_artifact_disagreement(3650, _log)
        ok.append(("pre-cutover legacy row is UNKNOWN, not clean",
                   r["adverse"] is None and any("predate the read-back field" in u for u in r["unreadable"])))

        # An explicit null must NOT pass as verified (only `is True` counts).
        _write({"timestamp": _now, "result": "success", "artifact_verified": None})
        ok.append(("artifact_verified: null is ADVERSE, not clean",
                   outcome_handoff_log_artifact_disagreement(30, _log)["adverse"] == 1))

        # Timezone offsets must not be compared lexically: this is UTC 2026-08-12T00:30 (POST),
        # but the string "2026-08-11T23:30:00-01:00" sorts BEFORE the cutover.
        _write({"timestamp": "2026-08-11T23:30:00-01:00", "result": "success"})
        r = outcome_handoff_log_artifact_disagreement(3650, _log)
        ok.append(("tz-offset row is classified by NORMALIZED time, not string order",
                   any("read-back is not running" in u for u in r["unreadable"])))

        # Window must actually bound: an old adverse row falls out of a 1-day window.
        _write({"timestamp": "2026-01-02T03:04:05", "result": "attempted"})
        ok.append(("out-of-window adverse row is excluded",
                   outcome_handoff_log_artifact_disagreement(1, _log)["adverse"] == 0))

        _log.write_text("not json at all\n")
        ok.append(("an unparseable row is UNREADABLE, never a pass",
                   bool(outcome_handoff_log_artifact_disagreement(30, _log)["unreadable"])))

    # --- outcome_handoff_misfiled: drive the REAL function over a temp handoffs dir --------
    # It had NO fixtures when first written and its first two versions reported adverse=0 on the
    # corpus that motivated it. Fixtures now encode BOTH known false-negative shapes.
    with _tf.TemporaryDirectory() as td2:
        hd = Path(td2)
        A = "aaaaaaaa-1111-2222-3333-444444444444"
        B = "bbbbbbbb-5555-6666-7777-888888888888"

        (hd / f"handoff-{A}-2026-08-12.md").write_text(f"# Handoff\nSession ID: {A}\nwork\n")
        ok.append(("a self-consistent labelled handoff is NOT adverse",
                   outcome_handoff_misfiled(hd)["adverse"] == 0))

        (hd / f"handoff-{A}-2026-08-12.md").write_text(f"# Handoff\nSession ID: {B}\nwork\n")
        ok.append(("a labelled id differing from the filename IS adverse",
                   outcome_handoff_misfiled(hd)["adverse"] == 1))

        # Regression: the label-only version missed unlabelled files entirely.
        (hd / f"handoff-{A}-2026-08-12.md").write_text(f"# Handoff\nno label, mentions {B} only\n")
        ok.append(("an UNLABELLED body naming only another uuid IS adverse",
                   outcome_handoff_misfiled(hd)["adverse"] == 1))

        # A body may legitimately reference peers; naming its own id anywhere clears it.
        (hd / f"handoff-{A}-2026-08-12.md").write_text(f"# Handoff\npeer {B}\nmine {A}\n")
        ok.append(("naming a PEER while also naming its own id is NOT adverse",
                   outcome_handoff_misfiled(hd)["adverse"] == 0))

        # Regression: the 4KB-head version truncated past the declaration.
        (hd / f"handoff-{A}-2026-08-12.md").write_text("x" * 5000 + f"\nmentions {B} only\n")
        ok.append(("a declaration BEYOND 4KB is still seen (no head-slice truncation)",
                   outcome_handoff_misfiled(hd)["adverse"] == 1))

        # Deprecated short-form names carry no full id to compare — skipped, never adverse.
        for f in hd.glob("*.md"):
            f.unlink()
        (hd / "handoff-aaaaaaaa-2026-08-12.md").write_text(f"# Handoff\nmentions {B}\n")
        r_ = outcome_handoff_misfiled(hd)
        ok.append(("a deprecated short-form filename is SKIPPED, not adverse",
                   r_["adverse"] == 0 and r_["compared"] == 0))

    # --- outcome_check_not_running: drive the REAL function over a temp heartbeat ----------
    with _tf.TemporaryDirectory() as td3:
        _hb = Path(td3) / "hb.jsonl"
        _now = datetime.now(timezone.utc)

        def _row(name, hours_ago):
            return json.dumps({"ts": (_now - timedelta(hours=hours_ago)).isoformat(),
                               "check": name, "rc": 0, "status": "ok"})

        # An ABSENT heartbeat must return the SAME KEY SET as the main path. An early return
        # with fewer keys crashes any consumer indexing them -- caught by a smoke test raising
        # KeyError, which is why this fixture exists.
        _r = outcome_check_not_running(Path(td3) / "nope.jsonl")
        ok.append(("absent heartbeat -> UNKNOWN with the full key set",
                   _r["adverse"] is None and {"stale", "never_seen", "note"} <= set(_r)))

        _hb.write_text("\n".join(_row(n, 0.1) for n in EXPECTED_CADENCE_H) + "\n")
        ok.append(("all checks fresh -> adverse 0", outcome_check_not_running(_hb)["adverse"] == 0))

        _rows = [_row(n, 0.1) for n in EXPECTED_CADENCE_H if n != "gate-ledger-archive"]
        _hb.write_text("\n".join(_rows + [_row("gate-ledger-archive", 5)]) + "\n")
        _r = outcome_check_not_running(_hb)
        ok.append(("beyond 2x cadence -> ADVERSE",
                   _r["adverse"] == 1 and "gate-ledger-archive" in _r["stale"][0]))

        _hb.write_text("\n".join(_rows + [_row("gate-ledger-archive", 1.5)]) + "\n")
        ok.append(("within the 2x grace -> NOT adverse",
                   outcome_check_not_running(_hb)["adverse"] == 0))

        # Never-seen is UNKNOWN, not adverse: before first run everything is never-seen, and
        # calling that ADVERSE would cry wolf on every fresh install.
        _hb.write_text(_row("governed-outcomes", 0.1) + "\n")
        _r = outcome_check_not_running(_hb)
        ok.append(("never-seen checks are UNKNOWN, not adverse",
                   _r["adverse"] == 0 and len(_r["never_seen"]) == 6 and _r["unreadable"]))

        _hb.write_text("[]\n" + _row("governed-outcomes", 0.1) + "\n")
        ok.append(("a non-object heartbeat row is counted bad, not fatal",
                   any("unparseable" in u for u in outcome_check_not_running(_hb)["unreadable"])))

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
