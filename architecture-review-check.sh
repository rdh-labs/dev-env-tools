#!/usr/bin/env bash
#
# architecture-review-check.sh - the architecture re-evaluation tripwire (ISSUE-3405).
#
# Reads architecture-assumptions.yaml and surfaces any ACTIVE assumption whose
# next_review date has arrived, so the agent re-checks its FALSIFIER against the
# watch_source and either confirms-and-bumps or acts on an external development.
# This is the "control loop" the static MCP-budget policy lacked: a policy blocks
# new mistakes; this re-opens an old decision when the world may have changed.
#
# Pure READ unit (no network, no `claude mcp list` on the hot path - the live count
# is run by the agent in-session when an entry surfaces, not on every SessionStart).
# Deterministically testable:  bash ~/bin/tests/test-architecture-review.sh
#
# ADVISORY / READ-ONLY: emits text to stdout, exits 0 unconditionally, mutates only
# its own consecutive-fire counter. Never blocks or aborts its caller. NO-SILENT-DEATH:
# a parse/read failure is logged to ERRLOG (a silent failure would let the tripwire rot
# invisibly - the exact failure mode it exists to prevent).
#
# Output (on fire) uses the "TRIGGERED: [ARCH-REVIEW]" prefix - the grep contract
# consumed by check-backlog-triggers.sh.
set -uo pipefail
trap 'exit 0' ERR   # any unexpected error -> silent, fail-safe exit 0 (never abort caller)

REG="${1:-${ARCH_ASSUMPTIONS_FILE:-$HOME/dev/infrastructure/dev-env-docs/knowledge/architecture-assumptions.yaml}}"
STATE="${ARCH_REVIEW_STATE_FILE:-$HOME/.claude/.arch-review-fire-count}"
ERRLOG="${ARCH_REVIEW_ERRLOG:-$HOME/.claude/logs/architecture-review-check.err.jsonl}"
TODAY="${ARCH_REVIEW_TODAY:-}"          # test override; empty => real today
MAX_SHOW=5                              # bound noise: list at most N due entries

# Parse the register. Emit one TSV line per DUE active entry:
#   <days_overdue>\t<id>\t<falsifier_oneline>\t<watch_oneline>\t<action_oneline>
# A non-zero exit / printed "__ERR__:<reason>" line means the parse failed - the bash
# side logs it to ERRLOG (no-silent-death) and stays silent (fail-safe: do not fabricate
# a "review due" nag from a broken read).
read -r -d '' _AR_PY <<'PY' || true
import sys, datetime
try:
    import yaml
except Exception as e:
    print("__ERR__:yaml-import-failed:%s" % type(e).__name__); sys.exit(0)
path = sys.argv[1]
today_s = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] else ""
try:
    today = datetime.date.fromisoformat(today_s) if today_s else datetime.date.today()
except Exception:
    today = datetime.date.today()
try:
    with open(path) as fh:
        data = yaml.safe_load(fh) or {}
except FileNotFoundError:
    sys.exit(0)                      # no register yet -> silent, not an error
except Exception as e:
    print("__ERR__:parse-failed:%s" % type(e).__name__); sys.exit(0)
items = data.get("assumptions") or []
if not isinstance(items, list):
    print("__ERR__:bad-shape:assumptions-not-list"); sys.exit(0)
def one(s):  # collapse whitespace, trim, neutralize tabs/newlines for TSV
    return " ".join(str(s or "").split())[:200]
due = []
for a in items:
    if not isinstance(a, dict):
        continue
    if str(a.get("status", "active")).lower() != "active":
        continue
    nr = a.get("next_review")
    if not nr:
        continue
    try:
        nrd = datetime.date.fromisoformat(str(nr))
    except Exception:
        print("__ERR__:bad-date:%s" % one(a.get("id", "?"))); continue
    if nrd <= today:
        ws = a.get("watch_source")
        if isinstance(ws, list):
            ws = ws[0] if ws else ""
        due.append((( today - nrd).days, one(a.get("id", "?")),
                    one(a.get("falsifier")), one(ws), one(a.get("on_review_action"))))
due.sort(reverse=True)               # most-overdue first
for days, _id, fals, ws, act in due:
    print("%d\t%s\t%s\t%s\t%s" % (days, _id, fals, ws, act))
PY

log_err() {  # append a properly-escaped JSON line to ERRLOG; never fail the caller
    local reason="$1"
    mkdir -p "$(dirname "$ERRLOG")" 2>/dev/null || true
    # json.dumps escapes quotes/backslashes/control chars/newlines (codex MEDIUM) — a
    # hand-rolled printf that only stripped '"' could emit invalid JSONL and break
    # downstream parsing. Errors are rare, so the python spawn cost is negligible.
    python3 - "$reason" "$REG" "$ERRLOG" <<'PYL' 2>/dev/null || true
import json, sys, datetime
with open(sys.argv[3], "a") as fh:
    fh.write(json.dumps({"ts": datetime.datetime.now().isoformat(),
                         "check": "architecture-review",
                         "reason": sys.argv[1], "reg": sys.argv[2]}) + "\n")
PYL
}

# Parse a regular, readable file; bound parse time (never block SessionStart).
# no-silent-death: a register that EXISTS but is unreadable / not a regular file is a
# monitoring FAILURE, not "nothing due" — log it (codex HIGH). A truly absent register
# stays silent (the python side treats FileNotFoundError as "no register yet").
OUT=""
if [[ -e "$REG" && ( ! -f "$REG" || ! -r "$REG" ) ]]; then
    log_err "register-exists-but-unreadable-or-not-regular"
elif [[ -f "$REG" && -r "$REG" ]]; then
    _TO=""; command -v timeout >/dev/null 2>&1 && _TO="timeout 5"
    OUT=$(printf '%s' "$_AR_PY" | $_TO python3 - "$REG" "$TODAY" 2>/dev/null) || { log_err "python-nonzero"; OUT=""; }
fi

# No-silent-death: surface parse errors to ERRLOG, then stay silent (fail-safe).
if grep -q '^__ERR__:' <<<"$OUT" 2>/dev/null; then
    # Log EVERY parse-error line, not just the first (codex MEDIUM) — multiple bad-date
    # entries would otherwise hide the scope of register corruption.
    while IFS= read -r _eline; do
        [[ -n "$_eline" ]] && log_err "${_eline#__ERR__:}"
    done < <(grep '^__ERR__:' <<<"$OUT")
    OUT="$(grep -v '^__ERR__:' <<<"$OUT")"
fi

# Strip blank lines; if nothing due -> reset fire-counter and exit silently.
DUE_LINES="$(grep -vE '^[[:space:]]*$' <<<"$OUT" || true)"
if [[ -z "$DUE_LINES" ]]; then
    rm -f "$STATE" 2>/dev/null || true
    exit 0
fi

N=$(printf '%s\n' "$DUE_LINES" | wc -l | tr -d '[:space:]')

# Fire: increment + persist consecutive-fire counter (visible escalation, ADHD-persistent).
CNT=0
if [[ -f "$STATE" ]]; then
    _prev=$(head -1 "$STATE" 2>/dev/null || echo "")
    [[ "$_prev" =~ ^[0-9]+$ ]] && CNT="$_prev"
fi
CNT=$(( CNT + 1 ))
printf '%s\n' "$CNT" > "$STATE" 2>/dev/null || true
SESS="sessions"; (( CNT == 1 )) && SESS="session"
ENT="entries"; (( N == 1 )) && ENT="entry"

echo "TRIGGERED: [ARCH-REVIEW] ${N} architectural assumption ${ENT} due for re-evaluation - fired ${CNT} consecutive ${SESS}"
SHOWN=0
while IFS=$'\t' read -r days id fals ws act; do
    [[ -n "$id" ]] || continue
    (( SHOWN >= MAX_SHOW )) && { echo "    ... and more (capped at ${MAX_SHOW})"; break; }
    echo "    • ${id} (${days}d overdue)"
    [[ -n "$fals" ]] && echo "        falsifier: ${fals}"
    [[ -n "$ws"   ]] && echo "        watch: ${ws}"
    [[ -n "$act"  ]] && echo "        do: ${act}"
    SHOWN=$(( SHOWN + 1 ))
done <<< "$DUE_LINES"
echo "  Action: for each entry, check the watch_source for the falsifier; if it has NOT shipped, run on_review_action and bump next_review in architecture-assumptions.yaml; if it HAS, open a reconsideration."
echo "  Register: ${REG}"
exit 0
