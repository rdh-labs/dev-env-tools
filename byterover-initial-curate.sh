#!/bin/bash
# ByteRover Initial Curation Script
# Curates essential ecosystem patterns for persistent memory
#
# PREREQUISITE: brv REPL must be running in the target project directory
# Usage: Start 'brv' in one terminal, then run this script in another
#
# Created: 2026-02-04

set -e

echo "=============================================="
echo "ByteRover Initial Curation Script"
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
echo "Starting curations..."
echo ""

# ============================================
# 1. Discovery Protocol (Critical)
# ============================================
echo "[1/8] Curating: Discovery Protocol..."
brv curate "MANDATORY Discovery Protocol before creating ANY file or architecture:

1. Search FILE-INDEX.txt: grep -i 'topic' ~/dev/FILE-INDEX.txt
2. Check AUTHORITATIVE.yaml: grep -i 'topic' ~/dev/AUTHORITATIVE-SUMMARY.yaml
3. Run smart discovery: ~/dev/infrastructure/tools/smart-discover.sh 'topic'
4. Check infrastructure dirs: ls ~/dev/infrastructure/ | grep -i 'topic'

Decision Tree:
- Found existing? → Edit it, don't create new
- Found similar? → Consolidate into existing
- Found deprecated? → DO NOT recreate
- Truly new? → Create AND update AUTHORITATIVE.yaml

Tool: ~/bin/pre-decision-discovery.sh 'topic' additional-terms

This prevents duplicate systems (past failures: lessons-learned SQLite when ~/lessons.md existed, multiple config systems when AUTHORITATIVE.yaml existed)." --headless

sleep 2

# ============================================
# 2. Hardware Constraints
# ============================================
echo "[2/8] Curating: Hardware Constraints..."
brv curate "Hardware Constraints - MUST READ before recommendations:

System: Lenovo 21DM, 16GB RAM, Intel i7-1255U (10 cores)
WSL2: 12GB RAM allocated, 6 processors, 8GB swap
GPU: NONE (Intel Iris Xe not accessible in WSL2)

Typical state: 7.7GB used, 184MB free, heavy swap NORMAL

PROHIBITED:
- Local ML models (SentenceTransformers, local LLMs, embeddings)
- GPU-dependent tools
- Memory-intensive local processing

ALLOWED:
- Cloud APIs (OpenAI, Anthropic, Gemini, DeepSeek, Groq)
- Lightweight CLI tools
- Streaming/incremental processing

Full specs: ~/dev/AUTHORITATIVE.yaml → development_environment" --headless

sleep 2

# ============================================
# 3. Testing Philosophy (DEC-086)
# ============================================
echo "[3/8] Curating: Testing Philosophy..."
brv curate "Risk-Based Testing Philosophy (DEC-086):

Before testing, ask 4 questions:
1. Impact if fails? (Minor → Feature broken → Security/data loss)
2. Reversibility? (Easy rollback → Some recovery → Irreversible)
3. Observability? (Caught immediately → Eventually → Hidden)
4. Complexity? (Lines of code, execution branches)

Testing by Risk Level:
- LOW (<50 LOC, reversible, observable): Integration test only, happy path
- MEDIUM (50-200 LOC, some recovery): Integration + manual edge cases + code review
- HIGH (security/data/irreversible, >200 LOC): Full test harness + multi-check + document failure modes

Examples:
- LOW: UI text changes, simple utilities, docs
- MEDIUM: Config changes, new CLI commands, non-critical integrations
- HIGH: Authentication, data migrations, infrastructure, secrets

Principle: Working code > perfect tests. Context-dependent rigor." --headless

sleep 2

# ============================================
# 4. Governance Tracking
# ============================================
echo "[4/8] Curating: Governance Tracking..."
brv curate "Governance Tracking - Summary-First Pattern (DEC-116):

ALWAYS use summary files first (96% token reduction):
- DECISIONS-SUMMARY.yaml (4.1K tokens) before DECISIONS-LOG.md (170K tokens)
- ISSUES-SUMMARY.yaml (3.9K tokens) before ISSUES-TRACKER.md
- IDEAS-SUMMARY.yaml (3.8K tokens) before IDEAS-BACKLOG.md

Location: ~/dev/infrastructure/dev-env-docs/

Pattern:
1. grep -i 'keyword' DECISIONS-SUMMARY.yaml  # Fast lookup
2. Only if more detail needed: grep -A20 'DEC-086' DECISIONS-LOG.md

ID Formats:
- DEC-### for decisions
- ISSUE-### for issues
- IDEA-### for ideas

Regenerate summaries: python3 ~/dev/infrastructure/tools/generate-governance-summaries.py" --headless

sleep 2

# ============================================
# 5. MCP Server Inventory
# ============================================
echo "[5/8] Curating: MCP Server Inventory..."
brv curate "MCP Server Inventory (Claude Code):

Local Stdio Servers (8):
- chrome-devtools: Browser automation, screenshots, DOM interaction
- code-executor: Sandboxed Python/Bash in Docker (security hardened)
- gemini: Gemini AI integration for analysis
- gemini-file-search: Vector search with Gemini embeddings
- governance: Query decisions, issues, ideas programmatically
- gitkraken: Git operations, PR management

Docker Gateway (via docker-mcp):
- git, github, fetch, sqlite, youtube_transcript, time, duckduckgo

HTTP/Hosted:
- dart: Task/project management (https://mcp.dartai.com/mcp)
- context7: Library documentation lookup
- Vercel, GitHub Copilot

Config locations:
- Claude Code: ~/.claude.json (mcpServers section)
- Claude Desktop: ~/.config/claude/claude_desktop_config.json

Health check: ~/dev/infrastructure/mcp-hub-deep-dive/gateway-health-check.sh" --headless

sleep 2

# ============================================
# 6. Multi-Agent Environment
# ============================================
echo "[6/8] Curating: Multi-Agent Environment..."
brv curate "Multi-Agent Environment - Design for All Agents:

Active Agents:
- Claude Code (Opus/Sonnet): Full MCP, ~70 tools, primary for complex work
- Gemini CLI: Full MCP, ~90 tools, good for research
- Codex CLI: MCP disabled (17s startup penalty), native shell preferred
- GitHub Copilot: VS Code extension, no MCP

Common Denominator: File system + bash commands
Design Principle: All capabilities should have bash script fallback

Universal Instructions: ~/dev/AGENTS.md
Entry Point: ~/dev/START-HERE.md

Agent Configs:
- Claude: ~/.claude/CLAUDE.md (global), project/.claude/CLAUDE.md (local)
- Gemini: ~/.gemini/settings.json
- Codex: ~/.codex/config.toml

Notification for all agents: ~/bin/notify.sh 'Title' 'Message'" --headless

sleep 2

# ============================================
# 7. Security Patterns
# ============================================
echo "[7/8] Curating: Security Patterns..."
brv curate "Security Patterns - Credential Management:

1Password Integration (PRIMARY):
- All API keys in 1Password Development vault
- Access pattern: op://Development/<item>/credential
- Load via: source ~/.bashrc_claude && op read 'op://...'
- Health check: ~/dev/infrastructure/1password/health-check.sh

Pre-commit Hooks (BLOCKING):
- Credential scanner: ~/.claude/hooks/security/credential_scanner.py
- Blocks commits with API keys, tokens, secrets
- Patterns: sk-ant-*, dsa_*, Bearer tokens, etc.

Security-Sensitive Paths (Intentionally NO git remote):
- ~/.claude (OAuth tokens, scrubbed in commit 6fbcae0)
- ~/.ssh, ~/.gnupg, ~/.aws, ~/.config/gh

Code Executor Sandbox:
- Docker with seccomp filtering, network isolation
- Runs as nobody:65534, all capabilities dropped
- Location: ~/.local/share/mcp-servers/code-executor/

Rule: NEVER propose adding git remotes to security-sensitive paths without investigation." --headless

sleep 2

# ============================================
# 8. File Management Policy
# ============================================
echo "[8/8] Curating: File Management Policy..."
brv curate "File Management Policy:

Core Rules:
1. MODIFY before CREATE - Always search first
2. ARCHIVE before DELETE - Use ~/docs/archive/{category}/
3. DELETE before ACCUMULATE - Remove true duplicates immediately

After work with iterations:
- Archive superseded: mv OLD.md docs/archive/{category}/OLD-v{N}-{descriptor}.md
- Rename final to canonical name
- Clean up plans: rm ~/.claude/plans/{finalized-plan}.md

NEVER leave in active directories:
- PLAN.md, PLAN-FINAL.md, PLAN-v2.md
- Multiple versions of same document

Git Awareness:
- Check git status when entering any directory
- Commit changes after significant work
- Push to remote for infrastructure repos

Full policy: ~/dev/infrastructure/ai-governance/FILE-MANAGEMENT-POLICY.md" --headless

echo ""
echo "=============================================="
echo "Curation Complete!"
echo "=============================================="
echo ""
echo "Verify with: brv query 'What patterns are documented?'"
echo "Push to remote: brv push"
