#!/usr/bin/env python3
"""findings_ledger.py — the below-bar findings ledger (decouple-capability Phase 1).

Standalone module + CLI (NOT a hook). A below-DEC-297-bar anomaly finding is LOGGED
as context here instead of minting a mandatory IDEA — so analysis stays free of tracked
work the user must supervise. Work-blocking / >=2-of-3-HIGH-impact / irreversible findings
are ADMITTED to the queue (disposition=queued); everything else is `logged`.

Append/rotation semantics (`_append_capped`) are COPIED WITH ATTRIBUTION from
`remediation_ledger.py` (dev-env-config/claude/hooks/stop/remediation_ledger.py:420-452).
Importing across repos (tools/ <- hooks/stop/) is fragile, so the invariants are duplicated
here and pinned by a parity test (test_findings_ledger.py). Preserved invariants:
  F4: split on "\\n" (NOT splitlines() — U+2028 breaks splitlines; remediation_ledger.py:435).
  F2: NEVER drop the just-appended batch (remediation_ledger.py:439-444).

Advances Dart O7t4WAplaNNk. classify()'s full impact-pathway + AI re-triage/calibration
engine is Phase 2 (out of scope); v1 is the mechanical admission threshold below.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

_HOME = Path.home()
LEDGER_PATH = Path(os.environ.get(
    "FINDINGS_LEDGER_PATH", str(_HOME / ".claude" / "logs" / "findings-ledger.jsonl")))
GUARDRAIL_PATH = Path(os.environ.get(
    "FINDINGS_GUARDRAIL_PATH", str(_HOME / ".claude" / "logs" / ".findings-guardrail.json")))
METRICS_LOG = Path(os.environ.get(
    "FINDINGS_LEDGER_METRICS", str(_HOME / ".claude" / "logs" / "findings-ledger-metrics.jsonl")))

LEDGER_CAP = 2000
DISPOSITIONS = ("logged", "queued", "dropped", "promoted")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- append/rotation --- COPY-WITH-ATTRIBUTION from remediation_ledger.py:420-452 ---
def _append_capped(path: Path, entries: list[dict]) -> int:
    """Append entries (concurrency-safe append-mode), then evict ONLY the pre-existing
    prefix to keep the file ~LEDGER_CAP. F2: NEVER drop the just-appended batch — keep at
    least `len(entries)` rows even if the batch alone exceeds LEDGER_CAP, and log a visible
    `ledger_batch_over_cap` event (no silent cap). F4: split on "\\n" to match the producer
    (splitlines() also breaks on U+2028). Rewrites only on overflow. Fail-open to -1.

    Parity-pinned to remediation_ledger._append_capped by test_findings_ledger.py.
    """
    if not entries:
        return -1
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:   # append = concurrency-safe common path
            for e in entries:
                f.write(json.dumps(e) + "\n")
        existing = [ln for ln in
                    path.read_text(encoding="utf-8", errors="replace").split("\n") if ln.strip()]
        n = len(existing)
        if n > LEDGER_CAP:
            batch_n = len(entries)
            if batch_n > LEDGER_CAP:
                _write_metrics({"ts": _now(), "event": "ledger_batch_over_cap",
                                "batch": batch_n, "cap": LEDGER_CAP})
            # keep at least the current batch (the last batch_n lines just written)
            kept = existing[-max(LEDGER_CAP, batch_n):]
            fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".flcap-")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write("\n".join(kept) + "\n")
            os.replace(tmp, path)
            return len(kept)
        return n
    except OSError:
        return -1


def _write_metrics(entry: dict) -> None:
    try:
        METRICS_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(METRICS_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass


# --- classification (v1 mechanical threshold; full engine = Phase 2) ----------------
def classify(finding: dict) -> str:
    """Mechanical admission threshold (v1). Returns a disposition:
      'queued'  — ADMIT to the WIP-limited queue: work-blocking OR Impact-Pathway
                  >=2-of-3 axes HIGH OR irreversibility-override.
      'logged'  — below-bar: logged as context, no mandatory tracked work.
    The finding carries the axes; deriving them (the impact-pathway classifier) and the
    re-triage/calibration/promotion engine are Phase 2 (out of scope, IDEA-10241)."""
    if finding.get("work_blocking"):
        return "queued"
    axes = finding.get("impact_axes")
    axes = axes if isinstance(axes, list) else []  # a bare string must not iterate per-char
    high = sum(1 for a in axes if str(a).strip().upper() == "HIGH")
    if high >= 2:  # >=2-of-3-HIGH
        return "queued"
    if finding.get("irreversible"):
        return "queued"
    return "logged"


def _finding_id(finding: dict) -> str:
    basis = (finding.get("id") or finding.get("normalized_class")
             or json.dumps(finding, sort_keys=True))
    return "fnd-" + hashlib.sha1(basis.encode("utf-8")).hexdigest()[:12]


def record(finding: dict, session_id: str = "") -> dict:
    """Build a ledger row from a finding and append it. Returns the row (with its
    computed disposition). A caller-supplied `disposition` is honored only if valid;
    otherwise classify() decides."""
    now = _now()
    disp = finding.get("disposition")
    if disp not in DISPOSITIONS:
        disp = classify(finding)
    row = {
        "id": finding.get("id") or _finding_id(finding),
        "normalized_class": finding.get("normalized_class", ""),
        "first_seen": finding.get("first_seen", now),
        "last_seen": now,
        "recurrence_count": int(finding.get("recurrence_count", 1) or 1),
        "impact_axes": list(finding.get("impact_axes", [])),
        "disposition": disp,
        "session_id": session_id or finding.get("session_id", ""),
        "retriage_history": list(finding.get("retriage_history", [])),
        "calibration_outcome": finding.get("calibration_outcome"),
    }
    _append_capped(LEDGER_PATH, [row])
    return row


# --- read helpers -------------------------------------------------------------------
def read_all(path: Path = LEDGER_PATH) -> list[dict]:
    """Read all ledger rows. F4: split on "\\n" (U+2028). Fail-open to []."""
    try:
        return [json.loads(ln) for ln in
                path.read_text(encoding="utf-8", errors="replace").split("\n") if ln.strip()]
    except (OSError, json.JSONDecodeError):
        return []


def queued_depth(path: Path = LEDGER_PATH) -> int:
    return sum(1 for r in read_all(path) if r.get("disposition") == "queued")


def logged_rows(path: Path = LEDGER_PATH) -> list[dict]:
    return [r for r in read_all(path) if r.get("disposition") == "logged"]


def last_write_ts(path: Path = LEDGER_PATH, rows: list[dict] | None = None) -> str | None:
    """ISO timestamp of the most recent row's last_seen (ledger staleness signal).
    Pass `rows` to reuse an already-read list and avoid re-parsing the file."""
    if rows is None:
        rows = read_all(path)
    return rows[-1].get("last_seen") if rows else None


# --- guardrail counter (backlog-opens + staleness) ----------------------------------
def _read_guardrail() -> dict:
    try:
        return json.loads(GUARDRAIL_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"backlog_opens": 0, "last_open": None}


def bump_backlog_opens() -> dict:
    """Increment the backlog-opens counter — the real writer wired this phase. If this
    trends UP the ledger is being pulled as intended; if queued items sit unopened, the
    digest's staleness reading surfaces it. The read-modify-write runs under an exclusive
    file lock so concurrent callers (multi-agent env) don't lose increments; atomic replace.
    Fail-open (returns best-effort state)."""
    try:
        GUARDRAIL_PATH.parent.mkdir(parents=True, exist_ok=True)
        lock_path = GUARDRAIL_PATH.parent / (GUARDRAIL_PATH.name + ".lock")
        with open(lock_path, "a") as lock_fh:
            try:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
            except OSError:
                pass  # lock best-effort; proceed
            state = _read_guardrail()
            state["backlog_opens"] = int(state.get("backlog_opens", 0)) + 1
            state["last_open"] = _now()
            fd, tmp = tempfile.mkstemp(dir=str(GUARDRAIL_PATH.parent), prefix=".flg-")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(state, fh)
            os.replace(tmp, GUARDRAIL_PATH)
        return state
    except OSError:
        return _read_guardrail()


def guardrail_state() -> dict:
    """Read-only guardrail snapshot for the portfolio digest (reader side)."""
    return _read_guardrail()


# --- CLI ----------------------------------------------------------------------------
def _cmd_record(args) -> int:
    raw = sys.stdin.read()
    try:
        finding = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        print("findings_ledger: invalid JSON on stdin", file=sys.stderr)
        return 1
    if not isinstance(finding, dict):
        print("findings_ledger: stdin JSON must be an object", file=sys.stderr)
        return 1
    row = record(finding, session_id=args.session_id or "")
    print(json.dumps(row))
    return 0


def _cmd_backlog(args) -> int:
    # Pull-based on-demand affordance: prints logged items ONLY when asked, and
    # increments the guardrail opens-counter (the wired writer).
    if not args.no_count:
        bump_backlog_opens()
    rows = logged_rows()
    if args.count:
        print(len(rows))
        return 0
    if not rows:
        print("findings-ledger: no logged (below-bar) findings.")
        return 0
    for r in rows:
        print(f"{r.get('id')}  [{r.get('normalized_class','')[:60]}]  "
              f"seen={r.get('recurrence_count')}  last={r.get('last_seen','')}")
    return 0


def _cmd_queue(args) -> int:
    if args.count:
        print(queued_depth())
        return 0
    for r in read_all():
        if r.get("disposition") == "queued":
            print(f"{r.get('id')}  [{r.get('normalized_class','')[:60]}]  axes={r.get('impact_axes')}")
    return 0


def _cmd_stats(args) -> int:
    rows = read_all()
    counts = {d: sum(1 for r in rows if r.get("disposition") == d) for d in DISPOSITIONS}
    print(json.dumps({
        "total": len(rows), "by_disposition": counts,
        "last_write": last_write_ts(rows=rows), "guardrail": guardrail_state(),
    }, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Below-bar findings ledger (decouple Phase 1).")
    p.add_argument("--session-id", default=os.environ.get("CLAUDE_CODE_SESSION_ID", ""))
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("record", help="append a finding (JSON on stdin)")
    sp.set_defaults(func=_cmd_record)

    sp = sub.add_parser("backlog", help="print logged (below-bar) findings on demand")
    sp.add_argument("--count", action="store_true", help="print only the count")
    sp.add_argument("--no-count", action="store_true", help="do NOT bump the opens-counter")
    sp.set_defaults(func=_cmd_backlog)

    sp = sub.add_parser("queue", help="print queued (admitted) findings")
    sp.add_argument("--count", action="store_true", help="print only the count")
    sp.set_defaults(func=_cmd_queue)

    sp = sub.add_parser("stats", help="ledger + guardrail summary (JSON)")
    sp.set_defaults(func=_cmd_stats)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
