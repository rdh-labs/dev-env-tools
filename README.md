# Development Environment Tools

**Purpose:** Essential scripts and libraries for development environment automation, governance, and maintenance.

**Repository:** rdh-labs/dev-env-tools
**Location:** `~/dev/infrastructure/tools/`

---

## Contents

### Libraries (`lib/`)

**Path Resolution Library** (`lib/paths.sh`)
- Central path resolution sourcing from AUTHORITATIVE.yaml
- 5 core functions: infrastructure, projects, archive, mcp_servers, tools paths
- Idempotency guard for safe multiple sourcing
- Comprehensive test suite (7 tests)
- **Usage:** `source ~/dev/infrastructure/tools/lib/paths.sh`
- **Related:** DEC-071

### Discovery & Search

- `smart-discover.sh` - Search before creating files
- `discovery-check.sh` - Legacy discovery tool
- `query-inventory.py` - Query discovery database

### MCP Management

- `mcp-status-checker.py` - Check MCP server health
- `mcp-auto-installer.py` - Install new MCP servers
- `mcp-registry-sync.py` - Sync MCP registry

### Monitoring & Reporting

- `daily-metrics.sh` - Generate daily metrics
- `weekly-report.sh` - Weekly summary report (uses paths.sh)
- `governance-check.sh` - Simple governance check
- `skill_update_checker.py` - Monitor external GitHub repos
- `project-memory-metrics.sh` - Log project-memory save/restore metrics

### Session Management

- `claude-startup.sh` - Initialize Claude sessions
- `init-agent-session.sh` - Setup agent context
- `governance-shortcuts.sh` - Shell aliases/functions

### Core Tools

- `simple-inventory-tracker.py` - Track project inventory
- `portfolio-audit.sh` - Audit portfolio status
- `nightly-maintenance.sh` - Nightly maintenance tasks

### Governance Calendar

- `governance-scheduler.sh` - Scheduler (cron)
- `gov-calendar` - CLI for add/list/due/delete/test
- **Python runtime:** `~/dev/infrastructure/tools/.venv-governance-scheduler` (default `VENV_MODE=system`)
- **Optional isolation:** `VENV_MODE=isolated` uses `.venv-governance-scheduler-iso`
- **Comment preservation:** `ruamel.yaml` preserves YAML comments/formatting

---

## Usage Guidelines

### Daily Use
```bash
# Before any new work
~/dev/infrastructure/tools/smart-discover.sh "feature-name"

# Check governance
~/dev/infrastructure/tools/governance-check.sh

# View metrics
~/dev/infrastructure/tools/daily-metrics.sh
```

### Weekly Maintenance
```bash
# Weekly report
~/dev/infrastructure/tools/weekly-report.sh

# MCP health check
python3 ~/dev/infrastructure/tools/mcp-status-checker.py
```

---

## Path Resolution Pattern (DEC-071)

All scripts should use `lib/paths.sh` instead of hardcoded paths:

**Before:**
```bash
SCRIPTS=$(find /home/ichardart/code/infra/tools -maxdepth 1 -type f -executable)
```

**After:**
```bash
source ~/dev/infrastructure/tools/lib/paths.sh
TOOLS_DIR=$(get_tools_path)
SCRIPTS=$(find "$TOOLS_DIR" -maxdepth 1 -type f -executable)
```

**Benefits:**
- Adapts to directory layout changes automatically
- Sources from AUTHORITATIVE.yaml (single source of truth)
- Fallback defaults ensure robustness

---

## Related Documentation

- **Inventory:** `~/dev/infrastructure/dev-env-docs/scripts/SCRIPTS-INVENTORY.md`
- **Decisions:** DEC-071 (Path Resolution), DEC-074 (Tools Repository)
- **Registry:** `~/dev/AUTHORITATIVE.yaml` → `infrastructure.tools`

---

## Maintenance

**Monthly Review:** Remove scripts unused for 60+ days
**Quality Standards:** Maximum 15 core scripts, each with clear unique purpose
**Documentation:** Update SCRIPTS-INVENTORY.md for any changes

---

*Last Updated: 2026-01-06*
*Part of: Week 1 Infrastructure Consolidation*
