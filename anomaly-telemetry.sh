#!/usr/bin/env bash
# IDEA-1295: Engram admissions-control telemetry
# Surfaces behavioral anomaly classes with >=3 Engram observations and no structural governance filing.
# Auto-dispatches Dart task creation via local claude session.
# Wired into SessionStart via check-backlog-triggers.sh (trigger #9).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCANNER="${SCRIPT_DIR}/anomaly_telemetry.py"
ENGRAM_DB="${HOME}/.engram/engram.db"
LOG_DIR="${HOME}/.claude/logs"
STATE_FILE="${LOG_DIR}/.anomaly-telemetry-state.json"
TMP_OUT="/tmp/anomaly_telemetry_out_$$.json"

# Guard: Engram DB and scanner must exist
[[ -f "$ENGRAM_DB" ]] || exit 0
[[ -f "$SCANNER" ]] || exit 0
[[ -d "$LOG_DIR" ]] || mkdir -p "$LOG_DIR"

trap 'rm -f "$TMP_OUT"' EXIT

# Run scanner - write to temp file to avoid quoting issues
python3 "$SCANNER" > "$TMP_OUT" 2>/dev/null || exit 0

# Load already-dispatched clusters from state file
python3 - <<PYEOF > /tmp/anomaly_new_$$.json 2>/dev/null || echo '[]' > /tmp/anomaly_new_$$.json
import json, os

scan_result = json.load(open("$TMP_OUT"))
state_file = "$STATE_FILE"
already = []
if os.path.exists(state_file):
    try:
        already = json.load(open(state_file)).get("dispatched", [])
    except Exception:
        pass

new = [c for c in scan_result if c["class"] not in already]
print(json.dumps(new))
PYEOF

NEW_COUNT=$(python3 -c "import json; print(len(json.load(open('/tmp/anomaly_new_$$.json'))))" 2>/dev/null || echo 0)
trap 'rm -f "$TMP_OUT" "/tmp/anomaly_new_$$.json"' EXIT

if (( NEW_COUNT == 0 )); then
    exit 0
fi

# Print TRIGGERED lines (captured by check-backlog-triggers.sh)
python3 - <<PYEOF 2>/dev/null
import json
clusters = json.load(open("/tmp/anomaly_new_$$.json"))
for c in clusters:
    print(f"TRIGGERED: Anomaly telemetry (IDEA-1295) - \"{c['class']}\" has {c['count']} recurrences, no enforcement gate")
    print(f"  Engram IDs: {c['obs_ids']}")
    for t in c["titles"][:2]:
        print(f"  - {t}")
    print(f"  Action: Dart task queued for enforcement gate implementation")
    print()
PYEOF

# Build dispatch prompt and write to temp file (avoids quoting issues with claude -p)
PROMPT_FILE="/tmp/anomaly_telemetry_prompt_$$.txt"
trap 'rm -f "$TMP_OUT" "/tmp/anomaly_new_$$.json" "$PROMPT_FILE"' EXIT

python3 - <<PYEOF > "$PROMPT_FILE" 2>/dev/null
import json
clusters = json.load(open("/tmp/anomaly_new_$$.json"))
lines = [
    "Create Dart tasks for anomaly classes surfaced by IDEA-1295 Engram telemetry.",
    "For EACH cluster below, call mcp__dart__create_task with these parameters.",
    "All tasks: dartboard=General/Governance/Backlog, priority=High.",
    "",
]
for i, c in enumerate(clusters, 1):
    obs_str = str(c["obs_ids"])
    titles_str = " | ".join(c["titles"])
    lines += [
        f"Cluster {i}:",
        f'  title: "Implement enforcement gate for: {c["class"]}"',
        f'  description: "IDEA-1295 Engram telemetry detected {c["count"]} observations (IDs: {obs_str}) matching behavioral class \\"{c["class"]}\\" in the last 30 days with no linked structural enforcement gate. Per DEC-297: >=3 recurrences without governance filing warrants an enforcement gate task. Representative patterns: {titles_str}"',
        "",
    ]
print("\n".join(lines))
PYEOF

# Auto-dispatch Dart task creation via local claude session (not cloud - needs local Engram MCP)
DISPATCH_DATE=$(date +%Y%m%d)
DISPATCH_LOG="${LOG_DIR}/anomaly-telemetry-dispatch-${DISPATCH_DATE}.log"

# RETIRED Phase 1 (decouple-capability, Dart O7t4WAplaNNk): auto-Dart mint removed —
# anomaly findings no longer auto-create mandatory tracked work the user must supervise.
# The dispatch prompt is still built above (harmless; the trap at the top of this script
# removes $PROMPT_FILE on exit) but is NOT dispatched. Clusters are surfaced via the
# TRIGGERED: lines printed earlier. anomaly_telemetry.py clustering is left untouched,
# reserved for Phase 2 (which will also wire clusters into tools/findings_ledger.py).
echo "  >> auto-Dart dispatch RETIRED (decouple Phase 1) — ${NEW_COUNT} class(es) surfaced (TRIGGERED lines above), NOT minted, no Dart task created."

# Update state: mark clusters as SEEN (dedup key). NOTE: the "dispatched" JSON key is
# retained for backward-compat with existing state files; post-Phase-1 it means "surfaced/
# seen", NOT dispatched — the auto-Dart dispatch above is retired (decouple Phase 1).
python3 - <<PYEOF 2>/dev/null
import json, time, os

clusters = json.load(open("/tmp/anomaly_new_$$.json"))
state_file = "$STATE_FILE"

d = {}
if os.path.exists(state_file):
    try:
        d = json.load(open(state_file))
    except Exception:
        pass

already = d.get("dispatched", [])
new_keys = [c["class"] for c in clusters]
d["dispatched"] = list(set(already + new_keys))
d["last_run"] = int(time.time())

with open(state_file, "w") as f:
    json.dump(d, f, indent=2)
PYEOF

# Push notification
if command -v notify.sh &>/dev/null; then
    notify.sh "Anomaly Telemetry" \
        "${NEW_COUNT} ungoverned anomaly class(es) - Dart tasks queued (IDEA-1295)" \
        --priority default --channel auto 2>/dev/null || true
fi
