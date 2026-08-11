#!/usr/bin/env python3
"""CONSUMER for gate_blocks_acked.jsonl -- the gate-block acknowledgement ledger.

WHY: CLAUDE.md instructs that "the durable record is gate_blocks_acked.jsonl", and NOTHING
has ever read it. A ledger with no reader records for an audience that does not exist.

FINDING ON FIRST RUN, and the reason this matters more than the aggregation: the files live at
/tmp/claude-<session>/gate_blocks_acked.jsonl. /tmp session state has a ~24-36h retention
horizon. The record the governance system calls DURABLE is EPHEMERAL, so the audit trail for
gate blocks silently evaporates. This tool reports that as a FAILURE, not a footnote.

WHAT IT ANSWERS, which no one could answer before:
  - which gates actually fire, ranked -- so enforcement effort can follow reality
  - fire_count per block: a gate that fires repeatedly before an ACK is one being worked
    around, not one being heeded
  - ack latency: how long a block sat before it was acknowledged
  - how much of the record is already past the retention horizon

Exit 0 normally; 1 when ledger data sits beyond the retention horizon (i.e. is being lost);
2 when no ledger exists at all. --self-check returns 1 on fixture failure.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

TMP_ROOT = Path("/tmp")
RETENTION_HOURS = 36.0        # documented /tmp session-artifact horizon; past this, assume loss


def _ts(v):
    if v is None:
        return None
    raw = str(v)
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        d = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def load(paths) -> tuple[list[dict], int]:
    """Returns (rows, unparseable). Malformed lines are COUNTED, never skipped silently --
    a consumer that drops rows reports health it did not measure."""
    rows, bad = [], 0
    for p in paths:
        try:
            for line in p.read_text(errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                    r["_source"] = str(p)
                    rows.append(r)
                except json.JSONDecodeError:
                    bad += 1
        except OSError:
            bad += 1
    return rows, bad


def analyse(rows, bad, now=None):
    now = now or datetime.now(timezone.utc)
    by_hook = collections.Counter()
    worked_around = 0        # fire_count > 1 => the gate fired again before being heeded
    latencies, at_risk, undatable, future = [], 0, 0, 0
    for r in rows:
        by_hook[r.get("hook", "<unknown>")] += 1
        try:
            if int(r.get("fire_count", 1)) > 1:
                worked_around += 1
        except (TypeError, ValueError):
            pass
        bt, at = _ts(r.get("block_timestamp")), _ts(r.get("acked_at"))
        if bt and at and at >= bt:
            latencies.append((at - bt).total_seconds())
        stamp = at or bt
        if stamp is None:
            # Cannot evaluate retention for this row. Independent review, 2026-08-11: this
            # previously fell through to OK -- the tool reported health on data it could not
            # assess, which is the false negative it exists to prevent.
            undatable += 1
        elif (now - stamp) > timedelta(hours=RETENTION_HOURS):
            at_risk += 1
        elif stamp > now + timedelta(minutes=5):
            future += 1          # a future stamp is corrupt data, never "comfortably fresh"
    return {
        "total": len(rows), "unparseable": bad, "by_hook": by_hook.most_common(),
        "worked_around": worked_around,
        "median_ack_seconds": (sorted(latencies)[len(latencies) // 2] if latencies else None),
        "past_retention": at_risk, "undatable": undatable, "future_stamped": future,
        # A record past the horizon is not "old" -- it is GONE the next time /tmp is reaped.
        # UNRELIABLE outranks OK: corrupt or undatable input means the measurement failed,
        # and a failed measurement must never be reported as health.
        "verdict": ("LOSING_RECORD" if at_risk else
                    "EMPTY" if not rows else
                    "UNRELIABLE" if (bad or undatable or future) else "OK"),
    }


def self_check() -> int:
    now = datetime.now(timezone.utc)
    def row(hook, fires, block_h, ack_h):
        return {"hook": hook, "fire_count": fires,
                "block_timestamp": (now - timedelta(hours=block_h)).isoformat(),
                "acked_at": (now - timedelta(hours=ack_h)).isoformat()}
    ok = []
    ok.append(("no rows must be EMPTY, never OK", analyse([], 0, now)["verdict"] == "EMPTY"))
    ok.append(("a row past the retention horizon must read LOSING_RECORD",
               analyse([row("g", 1, 100, 99)], 0, now)["verdict"] == "LOSING_RECORD"))
    ok.append(("a fresh row must read OK",
               analyse([row("g", 1, 2, 1)], 0, now)["verdict"] == "OK"))
    ok.append(("fire_count > 1 counts as worked-around",
               analyse([row("g", 3, 2, 1)], 0, now)["worked_around"] == 1))
    ok.append(("fire_count == 1 is NOT worked-around",
               analyse([row("g", 1, 2, 1)], 0, now)["worked_around"] == 0))
    a = analyse([row("alpha", 1, 2, 1), row("alpha", 1, 3, 2), row("beta", 1, 2, 1)], 0, now)
    ok.append(("hooks rank by frequency", a["by_hook"][0] == ("alpha", 2)))
    ok.append(("ack latency is computed", a["median_ack_seconds"] is not None))
    ok.append(("unparseable rows are surfaced, not dropped",
               analyse([], 7, now)["unparseable"] == 7))
    ok.append(("malformed rows must NOT read as OK",
               analyse([row("g", 1, 2, 1)], 9, now)["verdict"] == "UNRELIABLE"))
    ok.append(("a row with no usable timestamp must NOT read as OK",
               analyse([{"hook": "g", "fire_count": 1}], 0, now)["verdict"] == "UNRELIABLE"))
    ok.append(("an unparsable timestamp must NOT read as OK",
               analyse([{"hook": "g", "acked_at": "not-a-date"}], 0, now)["verdict"] == "UNRELIABLE"))
    ok.append(("a FUTURE timestamp is corrupt, not fresh",
               analyse([row("g", 1, -50, -50)], 0, now)["verdict"] == "UNRELIABLE"))
    # I/O-BOUNDARY fixtures. Independent review (GAC-2): gutting load() to return ([], 0) left
    # 12/12 PASSING, because every fixture built row dicts by hand and called analyse().
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        led = Path(td)/"l.jsonl"
        led.write_text('{"hook":"g","fire_count":1}\n' + 'NOT JSON\n' + '{"hook":"h","fire_count":2}\n')
        rows, badn = load([led])
        ok.append(("load() must actually READ rows (not silently return [])", len(rows) == 2))
        ok.append(("load() must COUNT malformed lines, not drop them", badn == 1))
        ok.append(("a ledger that fails to load must not read as OK",
                   analyse(*load([Path(td)/"missing.jsonl"]), now)["verdict"] == "EMPTY"))

    bad = [m for m, good in ok if not good]
    for m in bad:
        print(f"  [FAIL/self-check] {m}")
    if not bad:
        print(f"  [PASS/self-check] {len(ok)}/{len(ok)} checks proved the analysis")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-check", action="store_true")
    ap.add_argument("--report-only", action="store_true")
    args = ap.parse_args()

    if args.self_check:
        print("GATE ACK CONSUMER: self-check")
        return self_check()

    paths = sorted(TMP_ROOT.glob("claude-*/gate_blocks_acked.jsonl"))
    if not paths:
        print("GATE ACK CONSUMER: no ledger found — nothing has ever been acknowledged, "
              "or /tmp has already reaped it. Both are reportable, neither is 'fine'.")
        return 2

    rows, bad = load(paths)
    a = analyse(rows, bad)
    print(f"GATE ACK CONSUMER: {a['verdict']}")
    print(f"  {a['total']} acknowledgement(s) across {len(paths)} session ledger(s), "
          f"{a['unparseable']} unparseable")
    if a["median_ack_seconds"] is not None:
        print(f"  median ack latency: {a['median_ack_seconds']:.0f}s")
    print(f"  blocks that fired MORE THAN ONCE before being heeded: {a['worked_around']}")
    print("  gates by fire volume:")
    for hook, n in a["by_hook"][:10]:
        print(f"    {n:4d}  {hook}")
    if a["past_retention"]:
        print(f"\n  {a['past_retention']} record(s) are past the ~{RETENTION_HOURS:.0f}h /tmp")
        print("  retention horizon. CLAUDE.md calls this ledger the DURABLE record; it lives")
        print("  in /tmp and is not durable. The audit trail for gate blocks is evaporating.")
    return 0 if (a["verdict"] != "LOSING_RECORD" or args.report_only) else 1


if __name__ == "__main__":
    sys.exit(main())
