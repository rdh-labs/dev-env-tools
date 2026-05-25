#!/usr/bin/env bash
#
# backlog-drift-check.sh - IDEA-10307 Tier 1 advisory backlog-drift gate.
#
# Pure cache-READ unit (no network - the live Dart query lives in dart_queue_surface.py,
# which writes high_count tasks to the main cache and `last_high_closed_at`+`queried_at`
# to the dedicated close-recency file this reads (Dart ildWF7YsnNql)).
# This separation keeps the gate deterministically testable:
#   bash ~/bin/tests/test-backlog-drift.sh
#
# Fires ONLY on genuine drift: >=8 of the top-10 cached open tasks are HIGH
# priority AND the most recently closed HIGH task was >7 days ago. Stays silent
# while HIGH tasks are actively closing (last close <=7d). Degrades fail-safe to a
# high-count-only warning when close-recency is unavailable (old cache / query failed).
#
# ADVISORY / READ-ONLY: emits text to stdout, exits 0 unconditionally, mutates
# nothing except its own consecutive-fire counter. Never blocks or aborts its caller.
#
# Output (on fire) uses the "TRIGGERED: [BACKLOG-DRIFT]" prefix - the existing
# grep contract consumed by check-backlog-triggers.sh and evidence_gate.py A47.
set -uo pipefail
trap 'exit 0' ERR   # any unexpected error -> silent, fail-safe exit 0 (never abort caller)

CACHE="${1:-${DART_QUEUE_CACHE_FILE:-$HOME/.claude/.dart-queue-cache.json}}"
# Close-recency lives in a dedicated single-source file (Dart ildWF7YsnNql), written
# only by dart_queue_surface.py on a successful close-query, so it survives /queue
# overwrites of the main cache and close-query timeouts. high_count still comes from
# the main CACHE above. Env override mirrors shared/backlog_drift.py for test isolation.
CLOSE="${DART_LAST_HIGH_CLOSE_FILE:-$HOME/.claude/.dart-last-high-close.json}"
STATE="${BACKLOG_DRIFT_STATE_FILE:-$HOME/.claude/.backlog-drift-fire-count}"
DART_URL="https://app.dartai.com/t/JO2DwufcIeKT"
THRESHOLD=8
DRIFT_DAYS=7
STALE_HOURS=24   # close-recency older than this since the last successful query => untrusted.
#   KEEP IN SYNC with shared/backlog_drift.py (STALENESS_MAX_HOURS). On stale, this advisory
#   degrades to the high-count-only warning (its fail-safe direction is to surface, not suppress).

# Parse cache + close file: emit "<high_count>\t<status>\t<over_7d>\t<age_days>".
#   status: ok=close timestamp parsed | none=no close data | bad=unparseable | stale=last
#           successful query older than STALE_HOURS (close-recency no longer trusted)
#   over_7d: 1 if last close >7d ago (only meaningful when status=ok)
# argv: 1=main cache (high_count), 2=dedicated close file (close-recency), 3=stale-hours.
# Any file/JSON error on the close file -> status none -> degraded. Centralises all
# timestamp parsing in Python (robust for the microsecond+TZ ISO format Dart returns).
# Read the parser into a variable first (heredoc -> read), then pipe to python.
# Avoids the "heredoc nested inside $(...)" bash parser gotcha (unterminated
# here-document warning). `read -d ''` returns non-zero at EOF -> `|| true`.
read -r -d '' _BD_PY <<'PY' || true
import json, sys, datetime
# high_count from the main cache
try:
    d = json.load(open(sys.argv[1]))
    tt = d.get("top_tasks", []) or []
    hc = sum(1 for t in tt[:10] if str(t.get("priority", "")).lower() == "high")
except Exception:
    print("0\tnone\t0\t0"); sys.exit(0)
# close-recency from the dedicated single-source file (argv[2]); no main-cache fallback
try:
    cd = json.load(open(sys.argv[2]))
except Exception:
    print(f"{hc}\tnone\t0\t0"); sys.exit(0)
lc = cd.get("last_high_closed_at", "")
q = cd.get("queried_at", "")
if not lc or not q:
    print(f"{hc}\tnone\t0\t0"); sys.exit(0)
stale_hours = float(sys.argv[3]) if len(sys.argv) > 3 else 24.0
try:
    qdt = datetime.datetime.fromisoformat(q.replace("Z", "+00:00"))
    nowq = datetime.datetime.now(qdt.tzinfo) if qdt.tzinfo else datetime.datetime.now()
    secs_q = (nowq - qdt).total_seconds()
    # Untrusted if older than the cap OR queried_at in the FUTURE (negative => clock skew).
    if secs_q > stale_hours * 3600 or secs_q < 0:
        print(f"{hc}\tstale\t0\t0"); sys.exit(0)
except Exception:
    print(f"{hc}\tbad\t0\t0"); sys.exit(0)
try:
    dt = datetime.datetime.fromisoformat(lc.replace("Z", "+00:00"))
    now = datetime.datetime.now(dt.tzinfo) if dt.tzinfo else datetime.datetime.now()
    secs = (now - dt).total_seconds()
    over = 1 if secs > 7 * 86400 else 0
    print(f"{hc}\tok\t{over}\t{int(secs // 86400)}")
except Exception:
    print(f"{hc}\tbad\t0\t0")
PY
# Only parse regular, readable files (a fifo/device/dir path could otherwise block
# SessionStart) and bound parse time with `timeout` when available — the "never block
# the caller" contract. Missing main cache OR missing close file -> default -> silent/degraded.
PARSED=""
if [[ -f "$CACHE" && -r "$CACHE" ]]; then
    _CLOSE_ARG="$CLOSE"
    [[ -f "$CLOSE" && -r "$CLOSE" ]] || _CLOSE_ARG="/nonexistent/dart-close.json"
    _TO=""; command -v timeout >/dev/null 2>&1 && _TO="timeout 5"
    PARSED=$(printf '%s' "$_BD_PY" | $_TO python3 - "$CACHE" "$_CLOSE_ARG" "$STALE_HOURS" 2>/dev/null) || PARSED=""
fi
[[ -n "$PARSED" ]] || PARSED=$'0\tnone\t0\t0'

IFS=$'\t' read -r HC STATUS OVER AGED <<<"$PARSED"
HC="${HC:-0}"; STATUS="${STATUS:-none}"; OVER="${OVER:-0}"; AGED="${AGED:-0}"

# Decide fire (full two-condition gate, fail-safe on missing close data).
FIRE=0
DEGRADED=0
if (( HC >= THRESHOLD )); then
    case "$STATUS" in
        ok)
            if (( OVER == 1 )); then FIRE=1; fi   # drift: high AND not closing
            # else: a HIGH was closed <=7d ago -> actively closing -> silent
            ;;
        none|bad|stale)
            FIRE=1; DEGRADED=1                     # fail-safe: surface high-count (close-recency missing/unparseable/stale)
            ;;
    esac
fi

if (( FIRE == 0 )); then
    # No drift -> reset the consecutive-fire counter.
    rm -f "$STATE" 2>/dev/null || true
    exit 0
fi

# Fire: increment + persist consecutive-fire counter.
CNT=0
if [[ -f "$STATE" ]]; then
    _prev=$(head -1 "$STATE" 2>/dev/null || echo "")
    [[ "$_prev" =~ ^[0-9]+$ ]] && CNT="$_prev"   # corrupt content -> reset to 0, don't inflate
fi
CNT=$(( CNT + 1 ))
printf '%s\n' "$CNT" > "$STATE" 2>/dev/null || true

SESS="sessions"
if (( CNT == 1 )); then SESS="session"; fi

if (( DEGRADED == 1 )); then
    echo "TRIGGERED: [BACKLOG-DRIFT] ${HC}/10 top tasks HIGH (close-recency unavailable) - fired ${CNT} consecutive ${SESS}"
else
    echo "TRIGGERED: [BACKLOG-DRIFT] ${HC}/10 top tasks HIGH; last HIGH closed ${AGED}d ago (>${DRIFT_DAYS}d threshold) - fired ${CNT} consecutive ${SESS}"
fi
echo "  Dart: ${DART_URL}"
echo "  Action: Close or defer 2 existing HIGH tasks before opening new work this session"
echo "  Prompt: Backlog drift signal: ${HC} HIGH-priority tasks open (top 10), no HIGH task closed in >${DRIFT_DAYS} days. Run /queue, then close or defer at least 2 HIGH items before opening new work. IDEA-10307."
exit 0
