#!/usr/bin/env python3
"""REPAIR, not detection: re-announce alerts that were never delivered to anyone.

WHY THIS EXISTS. Measured 2026-08-13: over 14 days, **11 DOWN/error events had no channel
deliver at all** -- ntfy and moltbot both failed -- including this workspace's own company site.
Nothing noticed, because `notify.sh`'s delivery ledger carried the `success` field and had no
consumer. A consumer was added the same day; it DETECTS. This REPAIRS, and it is the first
repair mechanism in a stack that was otherwise entirely detect-and-notify.

THE FAILURE IS CORRELATED, WHICH IS THE WHOLE POINT. The silent events cluster: six distinct
sites reported DOWN between 08:15 and 08:19 on 2026-08-11 while every notification failed. That
is not six outages -- it is the monitoring host losing network egress. The checks and the alert
channel share a dependency, so **every connectivity loss is silent by construction**: the system
cannot tell you that it cannot tell you. Retrying later, from a moment when the network works,
is the only local repair available for that class.

WHAT IT DOES NOT CLAIM. Re-sending a five-day-old "site DOWN" page would be noise, and worse,
would read as a current outage. So this sends a DIGEST of what was missed, with the window, and
says plainly that the events are historical and their current state is unverified. The operator
learns that their alerting went dark and when -- which is the fact that was lost.

An external dead-man's-switch (Prometheus Watchdog / Dead Man's Snitch: a heartbeat routed to an
outside service that complains when it STOPS arriving) is the correct structural fix for
correlated failure, because it does not share the dependency. That needs an external account and
is the user's decision, not this tool's.

Usage: notify-redeliver.py [--days 14] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

LEDGER = Path.home() / ".cache" / "notify" / "notifications.jsonl"
STATE = Path.home() / ".metrics" / "notify-redeliver-state.json"
NOTIFY = Path.home() / "bin" / "notify.sh"
ADVERSE_HINT = ("DOWN", "ERROR", "FAIL", "UNHEALTHY", "ADVERSE", "UNKNOWN")


def _ts(v):
    """ISO or float epoch -- both are live in this estate's logs."""
    if isinstance(v, (int, float)):
        try:
            return datetime.fromtimestamp(float(v), timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    s = str(v or "")
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def undelivered(days: int, ledger: Path | None = None) -> list[dict]:
    """Events where EVERY attempt failed. A partial failure reached someone; this did not."""
    p = ledger or LEDGER
    if not p.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    events: dict[str, dict] = {}
    for line in p.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        t = _ts(r.get("timestamp") or r.get("ts"))
        if t is None or t < cutoff:
            continue
        title = str(r.get("title") or "")
        if not any(h in title.upper() for h in ADVERSE_HINT):
            continue
        # Bucket by title+hour: the same event fans out across channels and can straddle a
        # minute boundary, which an earlier minute-keyed pass split into phantom extra events.
        k = f"{title}|{t:%Y-%m-%dT%H}"
        e = events.setdefault(k, {"title": title, "first": t, "last": t,
                                  "ok": 0, "fail": 0, "withheld": 0})
        e["first"], e["last"] = min(e["first"], t), max(e["last"], t)
        # THREE STATES (2026-08-27, audit ~/dev/share/notify-consumer-audit-2026.md S3).
        # From 2026-08-27 notify.sh writes `success: false` on the dedup-suppressed path, because
        # a suppressed send contacted no channel and `success: true` was a claim of delivery for a
        # notification that reached nobody. Bucketing that as `fail` here would be a REGRESSION the
        # audit names explicitly: an adverse event whose every row in an hour bucket was merely
        # deduped would satisfy `ok == 0 and fail > 0` and this repair tool would RE-SEND it --
        # undoing the dedup's purpose, for an alert a human already received inside the window.
        # So suppression gets its own bucket: it is not a reached human (`ok`) and not a broken
        # channel (`fail`), and it keeps an all-withheld event out of the redelivery set entirely.
        # A genuinely failed attempt sitting alongside a withheld one still redelivers (fail > 0).
        # Tested BEFORE `success` -- post-fix withheld rows carry `success: false`.
        if r.get("suppressed") is True:
            e["withheld"] += 1
        else:
            e["fail" if r.get("success") is False else "ok"] += 1
    return sorted((e for e in events.values() if e["ok"] == 0 and e["fail"] > 0),
                  key=lambda e: e["first"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    miss = undelivered(a.days)
    if not miss:
        print("no fully-undelivered adverse events in window")
        return 0

    # Idempotent: a digest already sent for these events must not be sent again on every cron
    # tick. Keyed on the newest event, so a NEW silent event re-arms it.
    newest = max(e["last"] for e in miss).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        prev = json.loads(STATE.read_text()).get("newest") if STATE.exists() else None
    except (OSError, ValueError):
        prev = None
    if prev == newest and not a.dry_run:
        print(f"already redelivered through {newest}")
        return 0

    lines = [f"{e['first']:%Y-%m-%d %H:%M} {e['title'][:52]} ({e['fail']} attempts, 0 delivered)"
             for e in miss[-12:]]
    body = (f"{len(miss)} alert(s) reached NOBODY between "
            f"{min(e['first'] for e in miss):%Y-%m-%d %H:%M} and {newest}.\n"
            f"Every channel failed for each. These are HISTORICAL; current state is NOT "
            f"verified by this message.\n\n" + "\n".join(lines) +
            "\n\nThe checks and the alert channel share a network dependency, so a "
            "connectivity loss silences both. An external dead-man's-switch is the structural fix.")

    if a.dry_run:
        print(body)
        return 0
    if not NOTIFY.exists():
        print("notify.sh absent -- cannot repair", file=sys.stderr)
        return 2

    before = LEDGER.stat().st_size if LEDGER.exists() else 0
    subprocess.run([str(NOTIFY), f"ALERTING WENT DARK: {len(miss)} alert(s) reached nobody",
                    body, "--priority", "high", "--channel", "auto"],
                   capture_output=True, text=True, timeout=120)

    # VERIFY BY READ-BACK, never by the notifier's own return value (CLAUDE.md 12).
    #
    # SEEK FROM THE PRE-SEND OFFSET, never a fixed tail. An external review flagged the tail
    # window and was right: this ledger has CONCURRENT WRITERS -- other monitors logged into it
    # during this tool's own development -- so a fixed `[-10:]` can miss the row and report a
    # false NOT CONFIRMED. Reading exactly what was appended after `before` is both correct and
    # cheaper.
    #
    # HONEST LIMIT, and it is the same one this workspace keeps rediscovering. This ledger is
    # written BY notify.sh, so `success: True` is the notifier's own account of its own work --
    # the actor auditing the actor. It is strictly better than trusting the exit code, and it is
    # NOT proof of receipt. Real end-to-end proof requires reading the RECEIVING end (the ntfy
    # topic, the Telegram chat), which nothing here does. Treat a VERIFIED result as "the sender
    # recorded success", never as "a human was reached".
    delivered = False
    if LEDGER.exists():
        try:
            with LEDGER.open("r", errors="replace") as fh:
                fh.seek(before)
                appended = fh.read()
        except OSError:
            appended = ""
        for line in appended.splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if "WENT DARK" in str(r.get("title", "")) and r.get("success") is not False:
                delivered = True
    print(f"redelivery {'VERIFIED' if delivered else 'NOT CONFIRMED'} for {len(miss)} event(s)")
    if delivered:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps({"newest": newest, "count": len(miss)}))
    return 0 if delivered else 1


if __name__ == "__main__":
    sys.exit(main())
