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

# 2.5: §8 trigger-vocabulary recurrence (IDEA-10022 / ISSUE-3321 / ISSUE-3322)
# Threshold: > 5:1 trigger:ACK ratio over rolling 7-day window.
# THRESHOLD STATUS: ASSUMPTION — not yet objectives-grounded. Calibration revisit
# after 14 days of live data; if FP rate > 30%, re-derive against OBJECTIVES.md.
ANOMALY_LOG="$HOME/.claude/logs/anomaly-detection.jsonl"
if [[ -f "$ANOMALY_LOG" ]] && command -v jq >/dev/null 2>&1; then
    # Cutoff format must match log timestamp format (no timezone).
    # log_anomaly_event writes datetime.now().isoformat() = "2026-04-22T21:55:51.821144" (no TZ).
    # Generating cutoff with TZ yields lexicographic mis-ordering ("+00:00" < "."). (codex-ask 2026-04-25)
    WEEK_AGO=$(date -d '7 days ago' '+%Y-%m-%dT%H:%M:%S' 2>/dev/null || date -v-7d '+%Y-%m-%dT%H:%M:%S' 2>/dev/null || echo "")
    if [[ -n "$WEEK_AGO" ]]; then
        # Count unack'd triggers (the actual quantity of concern), not all triggers.
        # Earlier "5:1 trigger:ACK ratio" was wrong because ack_present is a row-level
        # field set TRUE for every trigger row in an ACKed response — clusters inflate
        # ack count linearly with triggers. (codex-ask HIGH finding 2026-04-25)
        UNACKD_TRIGGERS=$(jq -c --arg cutoff "$WEEK_AGO" \
            'select(.pattern_matched=="anomaly_extended_trigger" and (.timestamp // "") >= $cutoff and .ack_present!=true)' \
            "$ANOMALY_LOG" 2>/dev/null | wc -l)
        TOTAL_TRIGGERS=$(jq -c --arg cutoff "$WEEK_AGO" \
            'select(.pattern_matched=="anomaly_extended_trigger" and (.timestamp // "") >= $cutoff)' \
            "$ANOMALY_LOG" 2>/dev/null | wc -l)
        # Fire when there are >=5 unacknowledged triggers in the 7-day window.
        if (( UNACKD_TRIGGERS >= 5 )); then
            echo "TRIGGERED: §8 trigger-vocabulary unacknowledged — ${UNACKD_TRIGGERS} unack'd / ${TOTAL_TRIGGERS} total triggers over 7d (threshold: 5+ unack'd, ASSUMPTION-level)"
            echo "  Source: IDEA-10022 telemetry (evidence_gate.py A5-Extended); ISSUE-3321 / ISSUE-3322 / ISSUE-3326"
            echo "  Action: Review IDEA-10021 / IDEA-10028 priority — scanner build needed; telemetry has confirmed recurrence"
            echo ""
            TRIGGERED=$((TRIGGERED + 1))
        fi
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
# Count only new detections since the last archive_metadata entry.
# Prior detections have already been triaged and archived — re-counting them
# causes spurious re-triggers (observed: 9 archived detections re-fired threshold 3).
# Threshold 5 matches the hook's own RECURRENCE_THRESHOLD (raised 2026-04-11).
PIPELINE_LOG="$HOME/.claude/logs/bash-pipeline-safety.jsonl"
if [[ -f "$PIPELINE_LOG" ]]; then
    LAST_ARCHIVE_LINE=$(grep -n '"event".*"archive_metadata"' "$PIPELINE_LOG" 2>/dev/null | tail -1 | cut -d: -f1)
    if [[ -n "$LAST_ARCHIVE_LINE" ]]; then
        DETECT_COUNT=$(tail -n "+$((LAST_ARCHIVE_LINE + 1))" "$PIPELINE_LOG" | grep -c '"pattern"' 2>/dev/null || true)
    else
        DETECT_COUNT=$(grep -c '"pattern"' "$PIPELINE_LOG" 2>/dev/null || true)
    fi
    if (( DETECT_COUNT >= 5 )); then
        echo "TRIGGERED: Bash pipeline safety recurrence - ${DETECT_COUNT} new detections since last triage (threshold: 5)"
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
    if ! command -v gate-stats >/dev/null 2>&1; then
        echo "WARN: gate-stats not found in PATH — calibration trigger #6 skipped"
        TOTAL_GATE=0
        REAL_JSON=""
    else
        REAL_JSON=$(gate-stats --json --real-only 2>/dev/null)
        TOTAL_GATE=$(echo "$REAL_JSON" | jq -r '.total_events // 0' 2>/dev/null || echo 0)
    fi
    if (( TOTAL_GATE >= 20 )); then
        # Recompute block reasons from real events only using gate-stats JSON
        TOP_BLOCK=$(echo "$REAL_JSON" | jq -r '.block_reasons | to_entries | sort_by(-.value) | .[0] | select(. != null) | "\(.value) \(.key | split(":")[1])"' 2>/dev/null)
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

# 7. Phase 3 QC: evaluator gate reaches 30+ events spanning 3+ distinct sessions
# Monitors the dual-agent evaluator (Architecture C) deployed 2026-04-10
EVAL_LOG="$HOME/.claude/logs/evaluator-gate.jsonl"
if [[ -f "$EVAL_LOG" ]]; then
    EVAL_COUNT=$(wc -l < "$EVAL_LOG")
    EVAL_SESSION_COUNT=$(jq -r 'select(.transcript_path != null and .transcript_path != "") | .transcript_path' "$EVAL_LOG" 2>/dev/null | sort -u | wc -l)
    if (( EVAL_COUNT >= 30 && EVAL_SESSION_COUNT >= 3 )); then
        EVAL_BLOCKS=$(jq -r 'select(.outcome == "block") | .outcome' "$EVAL_LOG" 2>/dev/null | wc -l)
        EVAL_CATCH_RATE=$(( EVAL_BLOCKS * 100 / EVAL_COUNT ))
        echo "TRIGGERED: Phase 3 QC evaluation — evaluator gate has ${EVAL_COUNT} events across ${EVAL_SESSION_COUNT} sessions"
        echo "  Catch rate: ${EVAL_CATCH_RATE}% (threshold: >=20% catch, <=30% FP)"
        echo "  Dart: https://app.dartai.com/t/ijvhNWUwvFT4"
        echo "  Action: Evaluate evaluator gate calibration — decide Phase 3 (re-run loop)"
        echo "  NOTE: Strategic architecture decision — requires user session."
        echo "  Prompt: Phase 3 QC evaluation: evaluator gate has ${EVAL_COUNT} events across ${EVAL_SESSION_COUNT} sessions, ${EVAL_CATCH_RATE}% catch rate. Read ~/.claude/logs/evaluator-gate.jsonl. Analyze: (1) catch rate vs >=20% threshold, (2) FP rate vs <=30% threshold, (3) mandate distribution (which mandates fire most), (4) model reliability (gemini vs glm fallback frequency). Decide: proceed to Phase 3 (re-run loop) or continue calibrating. Write findings to ~/dev/share/phase3-qc-evaluation-\$(date +%Y-%m-%d).md."
        echo ""
        TRIGGERED=$((TRIGGERED + 1))
    elif (( EVAL_COUNT >= 10 )); then
        echo "WATCH: Evaluator gate — ${EVAL_COUNT} events across ${EVAL_SESSION_COUNT} session(s) (need 30+ events, 3+ sessions)"
        echo "  Continue accumulating events across sessions before Phase 3 evaluation"
        echo ""
    fi
fi

# 8. A14 calibration: response-tail-missing.jsonl has 10+ entries across 3+ distinct sessions
# IDEA-1149/IDEA-1150/ISSUE-3179/L-519/L-520/IDEA-1155: advisory escalation threshold.
# Once enough real-world A14 blocks accumulate, review TAIL_LINES and regex thresholds.
A14_LOG="$HOME/.claude/logs/response-tail-missing.jsonl"
if [[ -f "$A14_LOG" ]]; then
    A14_COUNT=$(wc -l < "$A14_LOG")
    A14_SESSION_COUNT=$(jq -r 'select(.session_id != null and .session_id != "") | .session_id' "$A14_LOG" 2>/dev/null | sort -u | wc -l)
    if (( A14_COUNT >= 10 && A14_SESSION_COUNT >= 3 )); then
        echo "TRIGGERED: A14 calibration ready — ${A14_COUNT} blocks across ${A14_SESSION_COUNT} sessions (thresholds: 10 events, 3 sessions)"
        echo "  Log: ~/.claude/logs/response-tail-missing.jsonl"
        echo "  Tuning dials: TAIL_LINES (currently 25) in evidence_gate.py; .{3,} content minimum in TAIL_DONE_RE/TAIL_OPEN_RE/TAIL_YOU_RE"
        echo "  Action: Review FP rate — if >20%, tighten TAIL_LINES or relax regex; if blocks are all legitimate, no change needed"
        echo ""

        # Auto-dispatch calibration review (diagnostic, not a production change).
        # Needs local ~/.claude/logs/ — cannot cloud-dispatch.
        A14_CALIB_FLAG="$HOME/.claude/logs/.a14-calibration-review-dispatched"
        if [[ ! -f "$A14_CALIB_FLAG" ]]; then
            echo "  >> Auto-dispatching local A14 calibration review session..."
            claude -p "A14 calibration ready: ${A14_COUNT} blocks across ${A14_SESSION_COUNT} sessions. Review ~/.claude/logs/response-tail-missing.jsonl — consider tuning TAIL_LINES or regex .{3,} content minimum in ~/dev/infrastructure/dev-env-config/claude/hooks/stop/evidence_gate.py if FP rate >20%. Steps: (1) read the log and categorize entries (legitimate block vs false positive), (2) compute FP rate, (3) if FP rate >20% recommend tuning TAIL_LINES or relaxing content minimum, (4) if FP rate <=20% confirm scanner is well-calibrated. Write findings to ~/dev/share/a14-calibration-review-\$(date +%Y-%m-%d).md." \
                --output-format text \
                > "$HOME/.claude/logs/a14-calibration-review-$(date +%Y%m%d).log" 2>&1 &
            touch "$A14_CALIB_FLAG"
            echo "  >> Dispatched (PID: $!) — results in ~/.claude/logs/a14-calibration-review-$(date +%Y%m%d).log"
        fi
        echo ""
        TRIGGERED=$((TRIGGERED + 1))
    elif (( A14_COUNT >= 1 )); then
        echo "WATCH: A14 calibration — ${A14_COUNT} block(s) across ${A14_SESSION_COUNT} session(s) (need 10+ events, 3+ sessions)"
        echo "  Continue accumulating A14 blocks before calibration review"
        echo ""
    fi
fi

# 9. Phase B observer: first real end_turn session logged since Phase B deployment (2026-04-23T02:33)
# Phase B added early_exit logging to evidence_gate.py to diagnose 8-day silence.
# This trigger fires once when a real working session's end_turn event appears in the log,
# then self-disables. Determines Phase C target: H1 (silent early-return) vs H2 (crash).
PHASE_B_FLAG="$HOME/.claude/logs/.phase-b-observed"
if [[ -f "$GATE_LOG" ]] && [[ ! -f "$PHASE_B_FLAG" ]]; then
    PHASE_B_ENTRY=$(jq -r 'select(
        .timestamp >= "2026-04-23T02:33" and
        .outcome != "early_exit" and
        (.tool_refs_count // 0) >= 1
    ) | "\(.timestamp) outcome=\(.outcome) tool_refs=\(.tool_refs_count)"' \
        "$GATE_LOG" 2>/dev/null | head -1 || true)
    if [[ -n "$PHASE_B_ENTRY" ]]; then
        PHASE_B_OUTCOME=$(echo "$PHASE_B_ENTRY" | grep -oP 'outcome=\K[^ ]+')
        echo "TRIGGERED: Phase B observation complete — first real end_turn session logged"
        echo "  Entry: $PHASE_B_ENTRY"
        if [[ "$PHASE_B_OUTCOME" == pass* ]]; then
            echo "  Finding: Gate is working correctly for real sessions. Phase C = H2 cleanup only."
            echo "  Phase C action: Add empty-stdin graceful handler in evidence_gate.py (return 0 with crash log entry, not bare exception). LOW risk."
        else
            echo "  Finding: Real session reached gate and was blocked ($PHASE_B_OUTCOME). Gate is evaluating. Phase C = confirm coverage + H2 cleanup."
            echo "  Phase C action: (1) Verify block is legitimate. (2) Add empty-stdin graceful handler. LOW risk."
        fi
        PHASE_B_ENTRY_SAFE="${PHASE_B_ENTRY//\'/}"
        echo "  Prompt for Phase C: Read ~/.claude/logs/evidence-gate.jsonl. Find entry: ${PHASE_B_ENTRY_SAFE}. Phase B confirmed end_turn sessions reach main evaluation. Phase C: add empty-stdin fallback to evidence_gate.py near line 4706 — on JSONDecodeError/empty stdin, log to evidence-gate-crashes.jsonl and return 0 (already partially done by Phase B; verify the handler covers blank-line input). Run /ship before committing. LOW risk, single-file."
        echo ""
        touch "$PHASE_B_FLAG"
        TRIGGERED=$((TRIGGERED + 1))
    fi
fi

# Writing Studio base dir (used by sections 8a and 8)
STUDIO_DIR="$HOME/dev/projects/writing-studio"

# 8a. Writing Studio: Suzanne reply detection via Gmail (runs first — event-driven)
# Checks Gmail for "DONE" replies from Suzanne in studio submission/review threads.
# Uses claude --print with Gmail MCP. Rate-limited to once per 20 min via state file.
# Also sends timeout alert if no reply after 3 days.
SUZANNE_CHECK_SCRIPT="$STUDIO_DIR/collaboration/check_suzanne_replies.sh"
if [[ -f "$SUZANNE_CHECK_SCRIPT" ]] && [[ -x "$SUZANNE_CHECK_SCRIPT" ]]; then
    # Run check and capture any TRIGGERED lines as new trigger items
    GMAIL_TRIGGERS=$("$SUZANNE_CHECK_SCRIPT" 2>/dev/null | grep "^TRIGGERED:" || true)
    if [[ -n "$GMAIL_TRIGGERS" ]]; then
        echo "$GMAIL_TRIGGERS"
        TRIGGERED=$((TRIGGERED + $(echo "$GMAIL_TRIGGERS" | wc -l)))
    fi
fi

# 8. Writing Studio: Suzanne review complete (ntfy form submission → process feedback)
# Polls ntfy for messages tagged writing-studio-review-complete.
# Dispatches process_suzanne_review.sh for each new article.
REVIEW_CONFIG="$STUDIO_DIR/collaboration/review-config.json"
NTFY_LAST_SEEN_FILE="$STUDIO_DIR/collaboration/.ntfy-last-seen-suzanne"
PROCESS_SCRIPT="$STUDIO_DIR/collaboration/process_suzanne_review.sh"

if [[ -f "$REVIEW_CONFIG" ]] && [[ -f "$PROCESS_SCRIPT" ]]; then
    NTFY_TOPIC=$(python3 -c "import json; c=json.load(open('$REVIEW_CONFIG')); print(c.get('ntfy_topic',''))" 2>/dev/null || true)
    REVIEW_TAG=$(python3 -c "import json; c=json.load(open('$REVIEW_CONFIG')); print(c.get('review_complete_tag','writing-studio-review-complete'))" 2>/dev/null || echo "writing-studio-review-complete")

    if [[ -n "$NTFY_TOPIC" ]]; then
        # Get last-seen timestamp (or default to 1 hour ago)
        if [[ -f "$NTFY_LAST_SEEN_FILE" ]]; then
            SINCE=$(cat "$NTFY_LAST_SEEN_FILE" | tr -d '[:space:]')
        else
            SINCE=$(date -u -d '1 hour ago' +%s 2>/dev/null || date -u -v-1H +%s 2>/dev/null || echo "1")
        fi

        # Poll ntfy for new review-complete messages since last check
        NTFY_RESPONSE=$(curl -sf --max-time 5 \
            "https://ntfy.sh/${NTFY_TOPIC}/json?poll=1&since=${SINCE}" \
            2>/dev/null || true)

        if [[ -n "$NTFY_RESPONSE" ]]; then
            # Extract article_ids from messages tagged writing-studio-review-complete
            NEW_ARTICLES=$(echo "$NTFY_RESPONSE" | python3 -c "
import sys, json
articles = []
latest_time = None
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
        tags = msg.get('tags', [])
        # Check for review-complete tag
        if any('writing-studio-review-complete' in t for t in tags):
            # Extract article_id from tags (format: 'article_id:POPUP-2026-XX')
            for tag in tags:
                if tag.startswith('article_id:'):
                    articles.append(tag.split(':', 1)[1])
                    break
            t = msg.get('time', 0)
            if t and (latest_time is None or t > latest_time):
                latest_time = t
    except Exception:
        pass
if latest_time:
    print('LATEST_TIME:' + str(latest_time + 1))
for a in articles:
    print('ARTICLE:' + a)
" 2>/dev/null || true)

            # Update last-seen timestamp
            NEW_LATEST=$(echo "$NEW_ARTICLES" | grep "^LATEST_TIME:" | cut -d: -f2 || true)
            if [[ -n "$NEW_LATEST" ]]; then
                echo "$NEW_LATEST" > "$NTFY_LAST_SEEN_FILE"
            fi

            # Dispatch processing for each new article
            while IFS= read -r line; do
                if [[ "$line" == ARTICLE:* ]]; then
                    ARTICLE_ID="${line#ARTICLE:}"
                    echo "TRIGGERED: Writing Studio — Suzanne review complete for $ARTICLE_ID"
                    echo "  Action: Processing Suzanne feedback (harvest comments → apply → Stage 3 → share)"
                    echo "  Script: $PROCESS_SCRIPT $ARTICLE_ID"
                    echo ""
                    TRIGGERED=$((TRIGGERED + 1))

                    # Auto-dispatch processing (local, needs MCP tools)
                    if [[ -x "$PROCESS_SCRIPT" ]]; then
                        echo "  >> Dispatching process_suzanne_review.sh for $ARTICLE_ID..."
                        "$PROCESS_SCRIPT" "$ARTICLE_ID" \
                            > "$STUDIO_DIR/logs/auto-process-$ARTICLE_ID-$(date +%Y%m%d-%H%M%S).log" 2>&1 &
                        echo "  >> Processing started (PID: $!) — log: logs/auto-process-$ARTICLE_ID-*.log"
                    fi
                fi
            done <<< "$NEW_ARTICLES"
        fi
    fi
fi

# 10. Anomaly telemetry: IDEA-1295 — behavioral classes with >=3 Engram obs, no enforcement gate
ANOMALY_TELEMETRY="$HOME/bin/anomaly-telemetry.sh"
if [[ -f "$ANOMALY_TELEMETRY" ]] && [[ -x "$ANOMALY_TELEMETRY" ]]; then
    ANOMALY_OUTPUT=$("$ANOMALY_TELEMETRY" 2>/dev/null || true)
    if [[ -n "$ANOMALY_OUTPUT" ]]; then
        echo "$ANOMALY_OUTPUT"
        ANOMALY_COUNT=$(echo "$ANOMALY_OUTPUT" | grep -c "^TRIGGERED:" 2>/dev/null || true)
        TRIGGERED=$((TRIGGERED + ANOMALY_COUNT))
    fi
fi

# 11. Pending governance merge files: staged content waiting for next-session merge
# Created when governance_claim_gate blocks direct filing during a session.
# Frontmatter: pending_for (target file), insert_before (anchor), session, created.
while IFS= read -r pf; do
    # Pass $pf as argv[1] — not interpolated into Python source to avoid injection (codex HIGH finding)
    FRONT=$(python3 -c "
import re, sys
fields = {'pending_for': 'unknown', 'insert_before': 'unknown', 'session': 'unknown', 'created': 'unknown'}
in_front = False
for line in open(sys.argv[1]):
    s = line.strip()
    if s == '---':
        if in_front: break
        in_front = True
        continue
    if in_front:
        m = re.match(r'^([\w_]+):\s*(.+)', s)
        if m: fields[m.group(1)] = m.group(2).strip('\"')
for k in ['pending_for','insert_before','session','created']:
    print(fields[k])
" "$pf" 2>/dev/null || true)
    if [[ -z "$FRONT" ]]; then continue; fi
    PENDING_FOR=$(printf '%s\n' "$FRONT" | sed -n '1p'); PENDING_FOR="${PENDING_FOR:-unknown}"
    INSERT_BEFORE=$(printf '%s\n' "$FRONT" | sed -n '2p'); INSERT_BEFORE="${INSERT_BEFORE:-unknown}"
    SESSION_ID=$(printf '%s\n' "$FRONT" | sed -n '3p'); SESSION_ID="${SESSION_ID:-unknown}"
    CREATED=$(printf '%s\n' "$FRONT" | sed -n '4p'); CREATED="${CREATED:-unknown}"
    FNAME=$(basename "$pf")
    echo "TRIGGERED: Pending governance merge — ${FNAME} (session ${SESSION_ID}, created ${CREATED})"
    echo "  File: $pf"
    echo "  Merge into: ${PENDING_FOR} before '${INSERT_BEFORE}'"
    echo "  Action: Insert content after frontmatter block, commit + push dev-env-docs, delete staging file"
    echo "  Prompt: Merge pending governance file: read ${pf} frontmatter for insert location, insert content (lines after closing '---') into ~/dev/infrastructure/dev-env-docs/${PENDING_FOR} immediately before '${INSERT_BEFORE}'. Run /ship, commit + push dev-env-docs, delete ${pf}."
    echo ""
    TRIGGERED=$((TRIGGERED + 1))
done < <(find "$HOME/dev/share" -maxdepth 1 -type f -name "pending-*.md" 2>/dev/null)

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
