#!/bin/bash
# ByteRover Initial Curation Script (Chunked Version)
# Curates essential ecosystem patterns in smaller chunks to avoid timeouts
#
# PREREQUISITE: brv REPL must be running in the target project directory
# Usage: Start 'brv' in one terminal, then run this script in another
#
# Created: 2026-02-04

set -e

SLEEP_TIME=3  # Seconds between curations

echo "=============================================="
echo "ByteRover Initial Curation Script (Chunked)"
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
echo "Starting curations (smaller chunks)..."
echo ""

# ============================================
# 1. Discovery Protocol - Part 1 (Steps)
# ============================================
echo "[1/16] Discovery Protocol - Steps..."
brv curate "Discovery Protocol Steps: 1) grep FILE-INDEX.txt 2) Check AUTHORITATIVE.yaml 3) Run smart-discover.sh 4) Check infrastructure dirs. Tool: ~/bin/pre-decision-discovery.sh" --headless
sleep $SLEEP_TIME

# ============================================
# 2. Discovery Protocol - Part 2 (Decision Tree)
# ============================================
echo "[2/16] Discovery Protocol - Decision Tree..."
brv curate "Discovery Decision Tree: Found existing? Edit it. Found similar? Consolidate. Found deprecated? DO NOT recreate. Truly new? Create AND update AUTHORITATIVE.yaml." --headless
sleep $SLEEP_TIME

# ============================================
# 3. Hardware Constraints - Specs
# ============================================
echo "[3/16] Hardware Constraints - Specs..."
brv curate "Hardware: Lenovo 21DM, 16GB RAM, i7-1255U, NO GPU in WSL2. WSL2 has 12GB RAM, 6 processors, 8GB swap. Normal state: 7.7GB used, heavy swap is normal." --headless
sleep $SLEEP_TIME

# ============================================
# 4. Hardware Constraints - Rules
# ============================================
echo "[4/16] Hardware Constraints - Rules..."
brv curate "Hardware Rules: PROHIBITED - local ML, GPU tools, memory-intensive processing. ALLOWED - Cloud APIs (OpenAI, Anthropic, Gemini, DeepSeek), lightweight CLI, streaming." --headless
sleep $SLEEP_TIME

# ============================================
# 5. Testing Philosophy - Questions
# ============================================
echo "[5/16] Testing Philosophy - Questions..."
brv curate "Testing (DEC-086): Ask 4 questions first. 1) Impact if fails? 2) Reversibility? 3) Observability? 4) Complexity (LOC, branches)?" --headless
sleep $SLEEP_TIME

# ============================================
# 6. Testing Philosophy - Risk Levels
# ============================================
echo "[6/16] Testing Philosophy - Risk Levels..."
brv curate "Testing Risk Levels: LOW (<50 LOC) = integration only. MEDIUM (50-200 LOC) = integration + edge cases. HIGH (security/data) = full harness + multi-check." --headless
sleep $SLEEP_TIME

# ============================================
# 7. Governance - Summary Files
# ============================================
echo "[7/16] Governance - Summary Files..."
brv curate "Governance Summary-First (DEC-116): Use DECISIONS-SUMMARY.yaml before DECISIONS-LOG.md. Same for ISSUES and IDEAS. Location: ~/dev/infrastructure/dev-env-docs/" --headless
sleep $SLEEP_TIME

# ============================================
# 8. Governance - ID Formats
# ============================================
echo "[8/16] Governance - ID Formats..."
brv curate "Governance IDs: DEC-### for decisions, ISSUE-### for issues, IDEA-### for ideas. Regenerate summaries: python3 ~/dev/infrastructure/tools/generate-governance-summaries.py" --headless
sleep $SLEEP_TIME

# ============================================
# 9. MCP Servers - Local
# ============================================
echo "[9/16] MCP Servers - Local..."
brv curate "MCP Local Servers: chrome-devtools (browser), code-executor (Docker sandbox), gemini, gemini-file-search, governance, gitkraken. Config: ~/.claude.json" --headless
sleep $SLEEP_TIME

# ============================================
# 10. MCP Servers - Docker & HTTP
# ============================================
echo "[10/16] MCP Servers - Docker & HTTP..."
brv curate "MCP Docker Gateway: git, github, fetch, sqlite, youtube_transcript, time, duckduckgo. HTTP: dart (mcp.dartai.com), context7, Vercel." --headless
sleep $SLEEP_TIME

# ============================================
# 11. Multi-Agent - Agents
# ============================================
echo "[11/16] Multi-Agent - Agents..."
brv curate "Active Agents: Claude Code (Opus/Sonnet, ~70 MCP tools), Gemini CLI (~90 tools), Codex CLI (MCP disabled), GitHub Copilot (VS Code only)." --headless
sleep $SLEEP_TIME

# ============================================
# 12. Multi-Agent - Design
# ============================================
echo "[12/16] Multi-Agent - Design..."
brv curate "Multi-Agent Design: Common denominator is filesystem + bash. All capabilities need bash fallback. Instructions: ~/dev/AGENTS.md. Entry: ~/dev/START-HERE.md" --headless
sleep $SLEEP_TIME

# ============================================
# 13. Security - 1Password
# ============================================
echo "[13/16] Security - 1Password..."
brv curate "1Password Integration: All API keys in Development vault. Pattern: op://Development/<item>/credential. Health: ~/dev/infrastructure/1password/health-check.sh" --headless
sleep $SLEEP_TIME

# ============================================
# 14. Security - Sensitive Paths
# ============================================
echo "[14/16] Security - Sensitive Paths..."
brv curate "Security-Sensitive Paths (NO git remote by design): ~/.claude, ~/.ssh, ~/.gnupg, ~/.aws, ~/.config/gh. NEVER propose adding remotes without investigation." --headless
sleep $SLEEP_TIME

# ============================================
# 15. File Management - Rules
# ============================================
echo "[15/16] File Management - Rules..."
brv curate "File Management Rules: 1) MODIFY before CREATE 2) ARCHIVE before DELETE (~/docs/archive/) 3) DELETE before ACCUMULATE duplicates." --headless
sleep $SLEEP_TIME

# ============================================
# 16. File Management - Git
# ============================================
echo "[16/16] File Management - Git..."
brv curate "Git Awareness: Check git status when entering directories. Commit after significant work. Push to remote for infrastructure repos. Never leave PLAN.md files." --headless

echo ""
echo "=============================================="
echo "Curation Complete! (16 chunks)"
echo "=============================================="
echo ""
echo "Verify with: brv query 'What patterns are documented?'"
echo "Push to remote: brv push"
