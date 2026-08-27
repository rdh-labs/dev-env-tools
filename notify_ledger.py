"""notify_ledger.py — ONE definition of what a notify.sh ledger row means.

WHY THIS EXISTS (2026-08-27). Four consumers independently encoded the same three-way
classification of `~/.cache/notify/notifications.jsonl`, and they did not agree. Measured on
the live ledger: one row carries `success` as the STRING "False" rather than the boolean, and
that single row classified as **failed** by enforcement-registration-cron.sh
(`str(r.get("success")).lower() == "false"`), as **delivered** by governed-outcomes-check.py
(`r.get("success") is False` — a str is not False), and as **ok/delivered** by
notify-redeliver.py (same predicate). The tool built to answer "did an adverse alert reach
someone" was therefore counting a FAILED delivery as a successful one — the exact defect the
2026-08-27 fix set out to remove, surviving in a second form because the predicate was
copied four times instead of shared once.

`is False` / `== "true"` disagree on every value that is neither a bool nor the expected
string. Truthiness is normalised here, in one place, so the disagreement cannot recur.

STATE ORDER IS LOAD-BEARING and must not be reordered:
  1. rehearsed  — a dry run returns success by design while contacting no channel.
  2. withheld   — dedup-suppressed rows carry success:false since 2026-08-27; testing
                  `success` first would file every dedup event as a transmission failure.
  3. delivered / failed — only now does `success` mean what it says.
  4. unknown    — DEC-326: inability to classify is a FAILURE state, never a PASS state.
                  A row shape this module does not recognise means the ledger schema moved,
                  and guessing is how that goes unnoticed for months. Never fold `unknown`
                  into a neighbouring bucket to keep totals tidy.

LEGACY ROWS: rows predating 2026-08-13 have no `dry_run` field and rows predating 2026-08-27
have no `delivered` field. Missing `dry_run` means REAL — a delivery that predates the flag
cannot have been a rehearsal. `delivered` is deliberately NOT consulted here: it is a
convenience field for consumers, and deriving state from it would make this module disagree
with essentially the whole ledger — only 55 of ~57,400 rows carry the field. (An earlier
version of this note said "47,000 rows predate it", which was the wrong quantity: 47,000 is
approximately the `success:true` count, not the pre-`delivered` count.)
"""

import json

DELIVERED = "delivered"
FAILED = "failed"
WITHHELD = "withheld"
REHEARSED = "rehearsed"
UNKNOWN = "unknown"

STATES = (DELIVERED, FAILED, WITHHELD, REHEARSED, UNKNOWN)

#: States in which no channel was contacted. Withheld and rehearsed are NOT failures — nothing
#: broke — but they are equally not deliveries, and a consumer counting "did this reach
#: someone" must exclude them. That distinction is the whole point of this module.
NOT_DELIVERED = (FAILED, WITHHELD, REHEARSED, UNKNOWN)

_TRUE = {"true", "1", "yes", "y", "t"}
_FALSE = {"false", "0", "no", "n", "f"}


def truthy(value):
    """Normalise a ledger truth value. Returns True, False, or None for 'cannot tell'.

    None is a real answer, not a failure to answer: a `success` field this cannot read is
    exactly the case that made four consumers disagree, and collapsing it to False would
    trade a visible disagreement for a silent one.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v in _TRUE:
            return True
        if v in _FALSE:
            return False
        return None
    if isinstance(value, (int, float)):
        return bool(value)
    return None


def classify_row(row):
    """Classify one ledger row into exactly one of STATES. Never raises."""
    if not isinstance(row, dict):
        return UNKNOWN
    if row.get("dry_run") is True:
        return REHEARSED
    if row.get("suppressed") is True:
        return WITHHELD
    success = truthy(row.get("success"))
    if success is True:
        return DELIVERED
    if success is False:
        return FAILED
    return UNKNOWN


def reached_someone(row):
    """True only if a channel was actually contacted and accepted the message."""
    return classify_row(row) == DELIVERED



def _try(line):
    """Parse one line, or None. Used by the reader invariant to compute the naive baseline."""
    try:
        return json.JSONDecoder(strict=False).decode(line)
    except ValueError:
        return None


def read_records(text, max_join=40):
    """Yield JSON objects from a JSONL ledger whose records may SPAN LINES.

    Measured 2026-08-13: ~4.3% of `notifications.jsonl` records fail a naive per-line
    `json.loads` -- not corruption, but `message` fields carrying unescaped newlines, so one
    record occupies several lines. A `.splitlines()` reader drops them silently, and a dropped
    record here IS a notification that reached nobody: the reader's failure mode and the defect
    it hunts are the same shape.

    Lives here rather than in governed-outcomes-check.py because that filename contains hyphens
    and cannot be imported -- which is exactly why notify-redeliver.py hand-rolled a lossy
    `.splitlines()` copy instead of reusing it.

    STRICT=FALSE IS THE WHOLE MECHANISM, and shipping without it made this reader WORSE than
    the naive per-line loop it replaced. A raw newline inside a JSON string is an INVALID
    CONTROL CHARACTER: `json.loads` rejects the buffer at the same byte offset no matter how
    many continuation lines are appended, so joining alone recovers NOTHING while `max_join`
    silently discards the valid records swallowed along the way. Measured on the live ledger:
    naive 55,135 records; join+`json.loads` 54,611 with ZERO successful joins (-524);
    join+`strict=False` 55,455 with 476 multi-line records actually recovered (+320).
    The remedy was inherited from a 2026-08-13 docstring and re-quoted rather than re-tested.

    Unparseable buffers are yielded as None so callers can COUNT them rather than lose them.
    """
    decode = json.JSONDecoder(strict=False).decode
    buf, joined = "", 0
    for line in text.splitlines():
        if not buf and not line.strip():
            continue
        buf = line if not buf else buf + "\n" + line
        try:
            yield decode(buf)
        except ValueError:
            joined += 1
            if joined <= max_join:
                continue
            yield None
        buf, joined = "", 0
    if buf:
        try:
            yield decode(buf)
        except ValueError:
            yield None


if __name__ == "__main__":
    # Self-check with BOTH polarities per state, plus the three real-world shapes that made
    # the four consumers disagree. Run: python3 notify_ledger.py
    cases = [
        ({"success": True}, DELIVERED),
        ({"success": False}, FAILED),
        # The live divergence: ONE row on the real ledger carries the string, not the bool.
        ({"success": "False"}, FAILED),
        ({"success": "true"}, DELIVERED),
        ({"success": False, "suppressed": True}, WITHHELD),
        # Withheld must win over success, or every dedup event files as a failure.
        ({"success": True, "suppressed": True}, WITHHELD),
        # Rehearsal must win over BOTH: a dry run returns success while contacting no channel.
        ({"success": True, "dry_run": True}, REHEARSED),
        ({"success": True, "dry_run": True, "suppressed": True}, REHEARSED),
        # Legacy row, no dry_run field: missing means REAL, never unknown-so-ignore.
        ({"success": True, "hostname": "R-Lenovo"}, DELIVERED),
        # DEC-326: unrecognisable is UNKNOWN, never folded into a neighbour.
        ({}, UNKNOWN),
        ({"success": None}, UNKNOWN),
        ({"success": "maybe"}, UNKNOWN),
        ("not a dict", UNKNOWN),
    ]
    # READER INVARIANT: a reader that recovers spanning records must never return FEWER
    # records than the naive per-line loop it replaces. Its absence is precisely why a
    # -524-record reader shipped as a fix and was migrated onto by a second consumer.
    import pathlib
    _led = pathlib.Path.home() / ".cache" / "notify" / "notifications.jsonl"
    reader_note = "skipped (no ledger)"
    if _led.exists():
        _txt = _led.read_text(errors="replace")
        _naive = sum(1 for l in _txt.splitlines() if l.strip()
                     and _try(l) is not None)
        _ours = sum(1 for r in read_records(_txt) if r is not None)
        if _ours < _naive:
            print(f"FAIL reader invariant: read_records={_ours} < naive={_naive} "
                  f"({_naive - _ours} records LOST vs the reader this replaces)")
            raise SystemExit(1)
        reader_note = f"reader {_ours} >= naive {_naive} (+{_ours - _naive})"

    failures = [(r, want, classify_row(r)) for r, want in cases if classify_row(r) != want]
    for row, want, got in failures:
        print(f"FAIL {row!r}: want {want}, got {got}")
    # Assert the suite can fail, so a green result is not a dead matcher: this must NOT pass.
    control_ok = classify_row({"success": True}) != FAILED
    if failures or not control_ok:
        print(f"FAILED: {len(failures)}/{len(cases)}")
        raise SystemExit(1)
    print(f"PASS: {len(cases)}/{len(cases)} classifications, both polarities per state; "
          f"{reader_note}")
