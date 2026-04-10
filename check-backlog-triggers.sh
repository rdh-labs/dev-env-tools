#!/usr/bin/env bash
set -euo pipefail

# Check backlog trigger conditions and surface items ready for action.
# Run at session start or periodically. Outputs only triggered items.
# Source: EVAL-Review-Swarm-2026-04-06.md backlog trigger system

TRIGGERED=0

# 1. Phase 2 QC: evidence gate reaches 30+ events spanning 3+ distinct sessions
# ISSUE-3035: single-session concentration is not representative — require distribution
GATE_LOG="$HOME/.claude/logs/evidence-gate.jsonl"
if [[ -f "$GATE_LOG" ]]; then
    GATE_COUNT=$(wc -l < "$GATE_LOG")
    # Count distinct session_ids (non-null) in the log
    # requires: jq — if absent, SESSION_COUNT=0, WATCH fires with "0 sessions" (non-blocking, non-misleading)
    SESSION_COUNT=$(jq -r 'select(.session_id != null and .session_id != "") | .session_id' "$GATE_LOG" 2>/dev/null | sort -u | wc -l)
    if (( GATE_COUNT >= 30 && SESSION_COUNT >= 3 )); then
        echo "TRIGGERED: Phase 2 QC evaluation - evidence gate has ${GATE_COUNT} events across ${SESSION_COUNT} sessions (thresholds: 30 events, 3 sessions)"
        echo "  Dart: https://app.dartai.com/t/x5DzAGbfApMu"
        echo "  Action: Evaluate readiness for Architecture C+ dual-agent evaluator"
        echo "  NOTE: Strategic architecture decision — requires user session, not auto-dispatched."
        echo "  Prompt: Phase 2 QC evaluation: evidence gate has ${GATE_COUNT} events across ${SESSION_COUNT} sessions. Run gate-stats --json for calibration data. Read ~/dev/share/STRUCTURAL-QC-RESEARCH-2026-04-04.md for Architecture C+ design. Evaluate: (1) pass/block rate distribution, (2) false positive patterns, (3) whether single-agent gate (A+) is sufficient or dual-agent evaluator (C+) is needed. Write go/no-go recommendation to ~/dev/share/phase2-qc-evaluation-\$(date +%Y-%m-%d).md."
        echo ""
        TRIGGERED=$((TRIGGERED + 1))
    elif (( GATE_COUNT >= 30 && SESSION_COUNT < 3 )); then
        echo "WATCH: Phase 2 QC - ${GATE_COUNT} events but only ${SESSION_COUNT} distinct session(s) (need 3+ for representative sample — ISSUE-3035)"
        echo "  Continue accumulating events across sessions before evaluating Phase 2"
        echo ""
    fi
fi

# 2. Satisficing pattern: 3+ detections in evidence gate
if [[ -f "$GATE_LOG" ]]; then
    SATISFICE_COUNT=$(grep -c 'satisfic' "$GATE_LOG" 2>/dev/null || true)
    if (( SATISFICE_COUNT >= 3 )); then
        echo "TRIGGERED: Pre-implementation review swarm - ${SATISFICE_COUNT} satisficing detections (threshold: 3)"
        echo "  Dart: https://app.dartai.com/t/80onex6wrOda"
        echo "  Action: Build refinement swarm skill for pre-implementation review"
        echo "  Prompt: Build a pre-implementation refinement swarm skill. ${SATISFICE_COUNT} satisficing detections in evidence gate log (~/.claude/logs/evidence-gate.jsonl). Analyze the satisficing patterns, then design a /refine skill that runs parallel review agents before implementation starts. Reference: ~/dev/share/EVAL-Review-Swarm-2026-04-06.md for swarm architecture patterns."
        echo ""
        TRIGGERED=$((TRIGGERED + 1))
    fi
fi

# 3. Engram observation count (requires engram MCP - fallback to file count)
ENGRAM_DB="$HOME/.local/share/engram/engram.db"
if [[ -f "$ENGRAM_DB" ]]; then
    OBS_COUNT=$(sqlite3 "$ENGRAM_DB" "SELECT COUNT(*) FROM observations;" 2>/dev/null || echo "0")
    if (( OBS_COUNT >= 100 )); then
        echo "TRIGGERED: Project skill audit for Obj 8 - Engram has ${OBS_COUNT} observations (threshold: 100)"
        echo "  Dart: https://app.dartai.com/t/zXJHKIuWSI7P"
        echo "  Action: Adapt project-skill-audit to analyze recurring patterns"
        echo "  Prompt: Engram has ${OBS_COUNT} observations. Run mem_search for recurring patterns across projects. Identify the top 5 most frequent decision/learning/discovery themes. Propose which should become skills, CLAUDE.md rules, or hooks. Write findings to ~/dev/share/engram-pattern-audit-\$(date +%Y-%m-%d).md."
        echo ""
        TRIGGERED=$((TRIGGERED + 1))
    fi
fi

# 4. Bash pipeline safety: L-467 pattern recurrence threshold
PIPELINE_LOG="$HOME/.claude/logs/bash-pipeline-safety.jsonl"
if [[ -f "$PIPELINE_LOG" ]]; then
    DETECT_COUNT=$(grep -c '"pattern"' "$PIPELINE_LOG" 2>/dev/null || true)
    if (( DETECT_COUNT >= 3 )); then
        echo "TRIGGERED: Bash pipeline safety recurrence - ${DETECT_COUNT} || echo pattern detections (threshold: 3)"
        echo "  Source: L-467 / ISSUE-3025 — misattributed failure from && ... || echo chains"
        echo "  Log: ~/.claude/logs/bash-pipeline-safety.jsonl"
        echo "  Action: Review detections, assess structural fix to command patterns"
        echo "  Prompt: Bash pipeline safety: ${DETECT_COUNT} detections of && ... || echo pattern (L-467/ISSUE-3025). Read ~/.claude/logs/bash-pipeline-safety.jsonl, categorize detections (true positive vs false positive), and propose a structural fix — either a Bash tool wrapper or a CLAUDE.md rule change. Reference: MEMORY.md Bash Critical Patterns section."
        echo ""
        TRIGGERED=$((TRIGGERED + 1))
    fi
fi

# 5. Evidence Gate v2: Layer 4 promotion decision (IDEA-1022)
if [[ -f "$GATE_LOG" ]]; then
    L4_TOTAL=$(jq -r 'select(.transcript_support != null) | .transcript_support' "$GATE_LOG" 2>/dev/null | wc -l)
    if (( L4_TOTAL >= 20 )); then
        L4_UNSUPPORTED=$(jq -r 'select(.transcript_support == false) | .transcript_support' "$GATE_LOG" 2>/dev/null | wc -l)
        L4_FP_RATE=0
        if (( L4_TOTAL > 0 )); then
            L4_FP_RATE=$(( L4_UNSUPPORTED * 100 / L4_TOTAL ))
        fi
        if (( L4_FP_RATE < 10 )); then
            echo "TRIGGERED: Layer 4 promotion — ${L4_TOTAL} events, ${L4_FP_RATE}% unsupported (<10% threshold)"
            echo "  Action: Promote transcript cross-reference from soft warning to hard gate"
            echo "  Run: gate-stats --json for full calibration data"
            echo "  NOTE: Irreversible production change — requires user decision, not auto-dispatched."
            echo "  Prompt: Layer 4 promotion: gate-stats --json shows ${L4_TOTAL} events, ${L4_FP_RATE}% FP rate. Read ~/.claude/logs/evidence-gate.jsonl for unsupported events. If FP rate confirms <10%, promote transcript_support from soft warning to hard gate in evidence_gate.py. Design: ~/dev/share/EVIDENCE-GATE-V2-DESIGN-2026-04-07.md Section 8."
            echo ""
            TRIGGERED=$((TRIGGERED + 1))
        else
            echo "WATCH: Layer 4 FP rate ${L4_FP_RATE}% (${L4_UNSUPPORTED}/${L4_TOTAL}) — too high for hard gate"
            echo "  Investigate unsupported evidence lines before promoting"
            echo ""
        fi
    fi
fi

# 6. Evidence Gate v2: Goodhart signal — >30% blocks from same reason
# Uses --real-only to exclude test-run clusters (events within 0.5s of each other)
# that contaminate metrics. Fix deployed 2026-04-10: EVIDENCE_GATE_NO_LOG prevents
# future test contamination; --real-only handles historical contamination.
if [[ -f "$GATE_LOG" ]]; then
    REAL_JSON=$(gate-stats --json --real-only 2>/dev/null)
    TOTAL_GATE=$(echo "$REAL_JSON" | jq -r '.total_events // 0' 2>/dev/null || echo 0)
    if (( TOTAL_GATE >= 20 )); then
        # Recompute block reasons from real events only using gate-stats JSON
        TOP_BLOCK=$(echo "$REAL_JSON" | jq -r '.block_reasons | to_entries | sort_by(-.value) | .[0] | "\(.value) \(.key | split(":")[1])"' 2>/dev/null)
        if [[ -n "$TOP_BLOCK" ]]; then
            TOP_COUNT=$(echo "$TOP_BLOCK" | awk '{print $1}')
            TOP_REASON=$(echo "$TOP_BLOCK" | awk '{print $2}')
            THRESHOLD=$(( TOTAL_GATE * 30 / 100 ))
            if (( TOP_COUNT > THRESHOLD )); then
                echo "TRIGGERED: Gate calibration review — '${TOP_REASON}' causes ${TOP_COUNT}/${TOTAL_GATE} events (>30%)"
                echo "  Action: Review whether gate criteria are too strict for this block reason"
                echo "  Run: gate-stats for full breakdown"
                echo ""

                # Auto-dispatch calibration review (diagnostic only, not a production change).
                # Must run locally — needs ~/.claude/logs/evidence-gate.jsonl which is not in git.
                CALIB_FLAG="$HOME/.claude/logs/.calibration-review-dispatched"
                if [[ ! -f "$CALIB_FLAG" ]]; then
                    echo "  >> Auto-dispatching local calibration review session..."
                    claude -p "Gate calibration review: '${TOP_REASON}' causes ${TOP_COUNT}/${TOTAL_GATE} blocks (>30%). Run gate-stats for full breakdown. Read ~/.claude/logs/evidence-gate.jsonl and examine the block events with this reason. Determine if gate criteria in ~/.claude/hooks/stop/evidence_gate.py are too strict for this block reason. Output a calibration recommendation (tighten, loosen, or keep) with evidence. Design doc: ~/dev/share/EVIDENCE-GATE-V2-DESIGN-2026-04-07.md. Write your findings to ~/dev/share/gate-calibration-review-\$(date +%Y-%m-%d).md." \
                        --output-format text \
                        > "$HOME/.claude/logs/calibration-review-$(date +%Y%m%d).log" 2>&1 &
                    touch "$CALIB_FLAG"
                    echo "  >> Dispatched (PID: $!) — results in ~/.claude/logs/calibration-review-$(date +%Y%m%d).log"
                fi
                echo ""
                TRIGGERED=$((TRIGGERED + 1))
            fi
        fi
    fi
fi

if (( TRIGGERED == 0 )); then
    # Silent when nothing triggered - don't add noise
    exit 0
fi

echo "=== ${TRIGGERED} backlog trigger(s) ready for action ==="

# ── Push notification for triggered items (not just stdout) ──────────────────
# Without this, triggers only appear in session startup text — relying on
# human attention to notice them. Push ensures awareness even if session
# output is long or skimmed.
if command -v notify.sh &>/dev/null; then
    notify.sh "Backlog Triggers" "${TRIGGERED} trigger(s) ready for action — check session output" --priority default --channel auto 2>/dev/null || true
fi
