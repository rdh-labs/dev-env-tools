#!/usr/bin/env bash
#
# backlog-drift-check.sh - IDEA-10307 Tier 1 advisory backlog-drift gate.
#
# Pure cache-READ unit (no network - the live Dart query lives in
# dart_queue_surface.py, which writes `last_high_closed_at` into the cache).
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
STATE="${BACKLOG_DRIFT_STATE_FILE:-$HOME/.claude/.backlog-drift-fire-count}"
DART_URL="https://app.dartai.com/t/JO2DwufcIeKT"
THRESHOLD=8
DRIFT_DAYS=7

# Parse cache: emit "<high_count>\t<status>\t<over_7d>\t<age_days>".
#   status: ok=close timestamp parsed | none=no field | bad=unparseable
#   over_7d: 1 if last close >7d ago (only meaningful when status=ok)
# Any file/JSON error -> high_count 0, status none -> silent. Centralises all
# timestamp parsing in Python (robust for the microsecond+TZ ISO format Dart returns).
# Read the parser into a variable first (heredoc -> read), then pipe to python.
# Avoids the "heredoc nested inside $(...)" bash parser gotcha (unterminated
# here-document warning). `read -d ''` returns non-zero at EOF -> `|| true`.
read -r -d '' _BD_PY <<'PY' || true
import json, sys, datetime
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    print("0\tnone\t0\t0"); sys.exit(0)
tt = d.get("top_tasks", []) or []
hc = sum(1 for t in tt[:10] if str(t.get("priority", "")).lower() == "high")
lc = d.get("last_high_closed_at", "")
if not lc:
    print(f"{hc}\tnone\t0\t0"); sys.exit(0)
try:
    dt = datetime.datetime.fromisoformat(lc.replace("Z", "+00:00"))
    now = datetime.datetime.now(dt.tzinfo) if dt.tzinfo else datetime.datetime.now()
    secs = (now - dt).total_seconds()
    over = 1 if secs > 7 * 86400 else 0
    print(f"{hc}\tok\t{over}\t{int(secs // 86400)}")
except Exception:
    print(f"{hc}\tbad\t0\t0")
PY
# Only parse a regular, readable file (a fifo/device/dir path could otherwise block
# SessionStart) and bound parse time with `timeout` when available — the "never block
# the caller" contract. Non-regular/missing/slow -> default -> silent.
PARSED=""
if [[ -f "$CACHE" && -r "$CACHE" ]]; then
    _TO=""; command -v timeout >/dev/null 2>&1 && _TO="timeout 5"
    PARSED=$(printf '%s' "$_BD_PY" | $_TO python3 - "$CACHE" 2>/dev/null) || PARSED=""
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
        none|bad)
            FIRE=1; DEGRADED=1                     # fail-safe: surface high-count
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
