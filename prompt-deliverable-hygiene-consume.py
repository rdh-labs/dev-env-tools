#!/usr/bin/env python3
"""CONSUMER for prompt-deliverable-hygiene telemetry. Reads, evaluates, notifies, escalates.

WHY THIS EXISTS: the producer was shipped without a reader — a 26th write-only stream in a
workspace where ~25 metrics streams already had no identified consumer. A measurement nobody
reads is a detector with extra steps.

OBJECTIVE SERVED (explicit, per the requirement that functionality name its objective):
  Prompt deliverables should go through the governed optimizer (IDEA-10068) and be registered
  in prompt-library.md (IDEA-10101). Measured 2026-08-10: ~1 in 61 cite the optimizer. This
  consumer exists so that number moves, and so nobody has to remember to look at it.

SUCCESS / FAILURE CRITERIA (explicit, pre-registered, not inferred at read time):
  PASS       citation rate improving or steady AND the feed is fresh
  REGRESSED  citation upper-bound fell vs the 7-day-ago baseline  -> NOTIFY
  STALE      newest sample older than STALE_HOURS                 -> NOTIFY (cron died)
  EMPTY      no samples at all                                    -> NOTIFY (producer never ran)
  Absence of data is a FAILURE state, never a pass. That distinction is the whole point:
  a silent producer and a healthy corpus look identical to a naive reader.

EVALUATION CRITERIA: trend on `cites_hi_pct` (the upper bound). The lower bound is a filename
match and moves only when someone writes the literal path; the upper bound moves when the
procedure is actually referenced, so it is the honest signal. Registration is NOT scored —
eligibility is not machine-decidable (see the producer's own note).

NO SILENT FAILURES:
  - staleness detection catches the producer dying, which is the failure mode a producer
    cannot report about itself;
  - unparseable rows are COUNTED and surfaced, never skipped silently;
  - notification failure is reported to stderr with a non-zero-ish marker in the log, so a
    broken notify path does not read as "nothing to report";
  - this consumer logs EVERY run, so its own silence is detectable by the same staleness test.

HONEST LIMIT — stated rather than papered over: if THIS consumer stops running, nothing
detects that. The chain terminates here. Extending it further is turtles-all-the-way-down;
the mitigation is that its own log is checkable by the same --self-check any successor runs.

Exit codes: 0 always for evaluation (advisory). --self-check returns 1 on fixture failure.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

FEED = Path.home() / ".metrics" / "prompt-deliverable-hygiene.jsonl"
OWN_LOG = Path.home() / ".metrics" / "prompt-deliverable-hygiene-consume.jsonl"
NOTIFY = Path.home() / "bin" / "notify.sh"

STALE_HOURS = 48          # 2 missed daily runs = the cron is dead, not merely late
REGRESSION_PP = 1.0       # percentage points of drop that count as a real regression
BASELINE_DAYS = 7


def _ts(row: dict):
    raw = str(row.get("ts", ""))
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        d = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def read_feed(path: Path):
    """Returns (rows, unparseable_count). Unparseable lines are COUNTED, never dropped
    silently — a consumer that skips malformed rows reports health it did not measure."""
    rows, bad = [], 0
    if not path.exists():
        return rows, bad
    try:
        for line in path.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                bad += 1
    except OSError:
        return [], -1  # -1 distinguishes "unreadable" from "clean and empty"
    return rows, bad


def evaluate(rows, bad, now=None):
    now = now or datetime.now(timezone.utc)
    dated = [(t, r) for r in rows if (t := _ts(r))]
    dated.sort(key=lambda p: p[0])

    if bad == -1:
        return {"verdict": "EMPTY", "reason": "feed unreadable", "notify": True,
                "unparseable": 0, "samples": 0, "latest_pct": None, "baseline_pct": None}
    if not dated:
        return {"verdict": "EMPTY", "reason": "no samples — producer has never run",
                "notify": True, "unparseable": bad, "samples": 0,
                "latest_pct": None, "baseline_pct": None}

    newest_t, newest = dated[-1]
    age_h = (now - newest_t).total_seconds() / 3600.0
    latest = (newest.get("strict") or {}).get("cites_hi_pct")

    if age_h > STALE_HOURS:
        return {"verdict": "STALE", "reason": f"newest sample {age_h:.0f}h old (>{STALE_HOURS}h) — cron likely dead",
                "notify": True, "unparseable": bad, "samples": len(dated),
                "latest_pct": latest, "baseline_pct": None, "age_hours": round(age_h, 1)}

    cutoff = now - timedelta(days=BASELINE_DAYS)
    older = [r for t, r in dated if t <= cutoff]
    baseline = (older[-1].get("strict") or {}).get("cites_hi_pct") if older else None

    verdict, reason, notify = "PASS", "fresh; no regression detected", False
    if baseline is not None and latest is not None and (baseline - latest) >= REGRESSION_PP:
        verdict = "REGRESSED"
        reason = f"citation upper bound fell {baseline:.1f}% -> {latest:.1f}% over {BASELINE_DAYS}d"
        notify = True
    elif baseline is None:
        reason = f"fresh; no {BASELINE_DAYS}d baseline yet ({len(dated)} sample(s)) — trend not yet evaluable"

    if bad:
        reason += f"; {bad} unparseable row(s) in feed"
        notify = True  # corrupt telemetry is itself a failure, not a footnote

    return {"verdict": verdict, "reason": reason, "notify": notify, "unparseable": bad,
            "samples": len(dated), "latest_pct": latest, "baseline_pct": baseline,
            "age_hours": round(age_h, 1)}


def notify(result) -> str:
    """Returns delivery status. A failed notification must NOT read as 'nothing to report'."""
    if not NOTIFY.exists():
        print(f"  [WARN] {NOTIFY} missing — alert NOT delivered", file=sys.stderr)
        return "undelivered:notify-missing"
    title = f"prompt-hygiene {result['verdict']}"
    body = f"{result['reason']} | samples={result['samples']} latest={result['latest_pct']}"
    try:
        p = subprocess.run([str(NOTIFY), title, body, "--priority", "high", "--channel", "auto"],
                           capture_output=True, text=True, timeout=60)
        if p.returncode != 0:
            print(f"  [WARN] notify.sh exit {p.returncode} — alert NOT delivered", file=sys.stderr)
            return f"undelivered:exit{p.returncode}"
        return "delivered"
    except Exception as exc:
        print(f"  [WARN] notify dispatch failed: {exc} — alert NOT delivered", file=sys.stderr)
        return "undelivered:exception"


def self_check() -> int:
    """Fixtures with known answers. Proves the evaluation logic, not merely that it runs."""
    now = datetime.now(timezone.utc)
    def row(days_ago, pct):
        return {"ts": (now - timedelta(days=days_ago)).isoformat(),
                "strict": {"cites_hi_pct": pct}}
    checks = []
    checks.append(("empty feed must be EMPTY, never PASS",
                   evaluate([], 0, now)["verdict"] == "EMPTY"))
    checks.append(("stale feed must be STALE",
                   evaluate([row(5, 6.6)], 0, now)["verdict"] == "STALE"))
    checks.append(("regression must be caught",
                   evaluate([row(8, 9.0), row(0, 6.0)], 0, now)["verdict"] == "REGRESSED"))
    checks.append(("steady must PASS",
                   evaluate([row(8, 6.6), row(0, 6.6)], 0, now)["verdict"] == "PASS"))
    checks.append(("unparseable rows must force a notify",
                   evaluate([row(8, 6.6), row(0, 6.6)], 3, now)["notify"] is True))
    checks.append(("unreadable feed is EMPTY, not PASS",
                   evaluate([], -1, now)["verdict"] == "EMPTY"))
    failed = [m for m, ok in checks if not ok]
    for m in failed:
        print(f"  [FAIL/self-check] {m}")
    if not failed:
        print(f"  [PASS/self-check] {len(checks)}/{len(checks)} checks proved the evaluation logic")
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--log", action="store_true")
    ap.add_argument("--notify", action="store_true", help="dispatch an alert when the verdict warrants it")
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args()

    if args.self_check:
        print("PROMPT-HYGIENE CONSUMER: self-check")
        return self_check()

    rows, bad = read_feed(FEED)
    result = evaluate(rows, bad)
    result["ts"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    result["notify_status"] = notify(result) if (args.notify and result["notify"]) else "not-attempted"

    if args.log:
        try:  # own log makes THIS consumer's silence detectable by the same staleness test
            OWN_LOG.parent.mkdir(parents=True, exist_ok=True)
            with OWN_LOG.open("a") as fh:
                fh.write(json.dumps(result) + "\n")
        except OSError as exc:
            print(f"  [WARN] could not write {OWN_LOG}: {exc}", file=sys.stderr)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"PROMPT-HYGIENE CONSUMER: {result['verdict']}")
        print(f"  {result['reason']}")
        print(f"  samples={result['samples']} latest={result['latest_pct']} "
              f"baseline={result['baseline_pct']} unparseable={result['unparseable']}")
        print(f"  notify: {result['notify_status']}")
        print("  objective: prompt deliverables run the governed optimizer (IDEA-10068) and")
        print("             register in prompt-library.md (IDEA-10101). Owner: Dart WjGEGzegaIkQ.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
