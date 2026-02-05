#!/bin/bash
# ByteRover Full Curation Script
# Curates ALL essential ecosystem patterns in small chunks
#
# PREREQUISITE: brv REPL must be running in the target project directory
# Usage: Start 'brv' in one terminal, then run this script in another
#
# Created: 2026-02-04

set -e

SLEEP_TIME=3  # Seconds between curations
TIMEOUT=60    # Timeout per curation in seconds
TOTAL=32

echo "=============================================="
echo "ByteRover Full Curation Script ($TOTAL chunks)"
echo "=============================================="
echo ""

# Check if brv is available
if ! command -v brv &> /dev/null; then
    echo "ERROR: brv command not found. Install with: npm install -g byterover-cli"
    exit 1
fi

# Check brv status
echo "Checking ByteRover status..."
STATUS=$(brv status 2>&1)
if echo "$STATUS" | grep -q "No instance running"; then
    echo "ERROR: brv REPL not running."
    echo "Start it first: cd <project-dir> && brv"
    exit 1
fi

echo "$STATUS"
echo ""
echo "Starting curations (with ${TIMEOUT}s timeout per chunk)..."
echo ""

# Function to curate with timeout
curate_chunk() {
    local num=$1
    local desc=$2
    local content=$3

    echo "[$num/$TOTAL] $desc"
    if timeout $TIMEOUT brv curate "$content" --headless 2>&1; then
        echo "  ✓ Done"
    else
        echo "  ⚠ Timeout or error, continuing..."
    fi
    sleep $SLEEP_TIME
}

# ============================================
# DISCOVERY PROTOCOL
# ============================================
curate_chunk 1 "Discovery Protocol - Steps..." \
    "Discovery Protocol Steps: 1) grep FILE-INDEX.txt 2) Check AUTHORITATIVE.yaml 3) Run smart-discover.sh 4) Check infrastructure dirs. Tool: ~/bin/pre-decision-discovery.sh"

curate_chunk 2 "Discovery Protocol - Decision Tree..." \
    "Discovery Decision Tree: Found existing? Edit it. Found similar? Consolidate. Found deprecated? DO NOT recreate. Truly new? Create AND update AUTHORITATIVE.yaml."

# ============================================
# HARDWARE CONSTRAINTS
# ============================================
curate_chunk 3 "Hardware Constraints - Specs..." \
    "Hardware: Lenovo 21DM, 16GB RAM, i7-1255U, NO GPU in WSL2. WSL2 has 12GB RAM, 6 processors, 8GB swap. Normal state: 7.7GB used, heavy swap is normal."

curate_chunk 4 "Hardware Constraints - Rules..." \
    "Hardware Rules: PROHIBITED - local ML, GPU tools, memory-intensive processing. ALLOWED - Cloud APIs (OpenAI, Anthropic, Gemini, DeepSeek), lightweight CLI, streaming."

# ============================================
# TESTING PHILOSOPHY
# ============================================
curate_chunk 5 "Testing Philosophy - Questions..." \
    "Testing (DEC-086): Ask 4 questions first. 1) Impact if fails? 2) Reversibility? 3) Observability? 4) Complexity (LOC, branches)?"

curate_chunk 6 "Testing Philosophy - Risk Levels..." \
    "Testing Risk Levels: LOW (<50 LOC) = integration only. MEDIUM (50-200 LOC) = integration + edge cases. HIGH (security/data) = full harness + multi-check."

# ============================================
# GOVERNANCE TRACKING
# ============================================
curate_chunk 7 "Governance - Summary Files..." \
    "Governance Summary-First (DEC-116): Use DECISIONS-SUMMARY.yaml before DECISIONS-LOG.md. Same for ISSUES and IDEAS. 96% token reduction."

curate_chunk 8 "Governance - ID Formats..." \
    "Governance IDs: DEC-### for decisions, ISSUE-### for issues, IDEA-### for ideas. Location: ~/dev/infrastructure/dev-env-docs/"

# ============================================
# MCP SERVERS
# ============================================
curate_chunk 9 "MCP Servers - Local..." \
    "MCP Local Servers: chrome-devtools, code-executor, gemini, gemini-file-search, governance, gitkraken. Config: ~/.claude.json"

curate_chunk 10 "MCP Servers - Docker & HTTP..." \
    "MCP Docker Gateway: git, github, fetch, sqlite, youtube_transcript, time, duckduckgo. HTTP: dart, context7, Vercel."

# ============================================
# MULTI-AGENT ENVIRONMENT
# ============================================
curate_chunk 11 "Multi-Agent - Agents..." \
    "Active Agents: Claude Code (Opus/Sonnet, ~70 MCP tools), Gemini CLI (~90 tools), Codex CLI (MCP disabled), GitHub Copilot (VS Code)."

curate_chunk 12 "Multi-Agent - Design..." \
    "Multi-Agent Design: Common denominator is filesystem + bash. All capabilities need bash fallback. Instructions: ~/dev/AGENTS.md"

# ============================================
# SECURITY
# ============================================
curate_chunk 13 "Security - 1Password..." \
    "1Password: All API keys in Development vault. Pattern: op://Development/<item>/credential. Health: ~/dev/infrastructure/1password/health-check.sh"

curate_chunk 14 "Security - Sensitive Paths..." \
    "Security-Sensitive Paths (NO git remote): ~/.claude, ~/.ssh, ~/.gnupg, ~/.aws, ~/.config/gh. Never add remotes without investigation."

curate_chunk 15 "Security - Code Executor..." \
    "Code Executor: Docker sandbox with seccomp filtering, network isolation, runs as nobody:65534. Location: ~/.local/share/mcp-servers/code-executor/"

# ============================================
# FILE MANAGEMENT
# ============================================
curate_chunk 16 "File Management - Rules..." \
    "File Management: 1) MODIFY before CREATE 2) ARCHIVE before DELETE (~/docs/archive/) 3) DELETE before ACCUMULATE duplicates."

curate_chunk 17 "File Management - Git..." \
    "Git Awareness: Check git status on directory entry. Commit after significant work. Push for infrastructure repos. Never leave PLAN.md files."

# ============================================
# PERCEPTION GAP PROTOCOL (DEC-085)
# ============================================
curate_chunk 18 "Perception Gap - Problem..." \
    "Perception Gap (DEC-085): When user sees X but you see Y, STOP blind investigation. Don't build solutions for unvalidated problems."

curate_chunk 19 "Perception Gap - Steps..." \
    "Perception Gap Steps: 1) ASK what user sees 2) STATE 2-3 hypotheses 3) VALIDATE before investigating 4) SOLVE proportionally."

# ============================================
# CONFIDENCE THRESHOLDS (DEC-142)
# ============================================
curate_chunk 20 "Confidence - Thresholds..." \
    "Confidence (DEC-142): Medium risk needs 75%, High risk needs 85%, Critical needs 90%. Below threshold? Suggest Opus escalation."

curate_chunk 21 "Confidence - Evidence Quality..." \
    "Evidence Quality: HIGH = file read or web search this session. MEDIUM = previous session. LOW = training data (STALE)."

# ============================================
# MULTI-CHECK VALIDATION
# ============================================
curate_chunk 22 "Multi-Check - When to Use..." \
    "Multi-Check: MUST use for irreversible changes, security decisions, confidence <85%. Tool: ~/dev/infrastructure/multi-check/multi-check.py"

curate_chunk 23 "Multi-Check - Interpretation..." \
    "Multi-Check Results: Consensus = proceed. Split opinions = present alternatives. All uncertain = escalate to user."

# ============================================
# KNOWLEDGE FRESHNESS
# ============================================
curate_chunk 24 "Knowledge Freshness - Sources..." \
    "Knowledge Freshness: Training data is STALE. File reads = HIGH reliability. Web search = HIGH. User statement = HIGH."

curate_chunk 25 "Knowledge Freshness - Red Flags..." \
    "Red Flags needing validation: 'was removed in...', 'previously configured...', 'no longer exists...'. Check AUTHORITATIVE.yaml."

# ============================================
# GOAL-CRITICAL CONSTRAINTS GATE
# ============================================
curate_chunk 26 "Goal-Critical - Gate..." \
    "Goal-Critical Gate: Before rejecting task, ask: Does it address strategic goal? If yes, MUST complete unless hard constraint violated."

curate_chunk 27 "Goal-Critical - Tradeoffs..." \
    "Task Rejection: Need objective impact, alternatives achieving goal, benchmark data if citing performance. Overhead without data is invalid."

# ============================================
# REFLEXIVE GOVERNANCE
# ============================================
curate_chunk 28 "Reflexive Governance - Evaluation..." \
    "Reflexive Governance: Every decision stress-tests parameters. Evaluate: Confirmed, Friction, Gap, or Blocker."

curate_chunk 29 "Reflexive Governance - Actions..." \
    "Parameter Actions: Confirmed = no change. Friction = IDEAS-BACKLOG. Gap = new parameter. Blocker = STOP, create ISSUE-###."

# ============================================
# PROJECT TEMPLATES
# ============================================
curate_chunk 30 "Project Templates - Types..." \
    "Templates (DEC-061): infrastructure, project, sandbox (has LIFECYCLE.md). Use: copier copy ~/dev/infrastructure/project-templates/TYPE"

curate_chunk 31 "Project Templates - Philosophy..." \
    "Template Philosophy: Minimal core + on-demand. WHEN-TO-CREATE.md lists optional files. Don't pre-create placeholders."

# ============================================
# NOTIFICATION SYSTEM
# ============================================
curate_chunk 32 "Notification System..." \
    "Notifications: ~/bin/notify.sh 'Title' 'Message' --priority high --channel auto. Channels: ntfy, moltbot, both, auto."

echo ""
echo "=============================================="
echo "Curation Complete! ($TOTAL chunks)"
echo "=============================================="
echo ""
echo "Verify with: brv query 'What patterns are documented?'"
echo "Push to remote: brv push"
