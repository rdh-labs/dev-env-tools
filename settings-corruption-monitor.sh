#!/bin/bash
# Periodic corruption detection (optional - cron: every 5 minutes)
# Complements post-write guard for cases where corruption happens outside tool use
#
# Installation (optional):
#   */5 * * * * ~/dev/infrastructure/tools/settings-corruption-monitor.sh >/dev/null 2>&1

set -euo pipefail

VALIDATOR_MODULE="$HOME/.claude/hooks/governance/permission_validator.py"
LOG_FILE="$HOME/.claude/logs/corruption-events.log"

# Ensure log directory exists
mkdir -p "$(dirname "$LOG_FILE")"

# Scan constrained roots (same as sanitizer)
SCAN_ROOTS=(
    "$HOME/.claude"
    "$HOME/dev"
    "$HOME/dev/projects"
    "$HOME/dev/infrastructure"
    "$HOME/dev/sandbox"
)

for root in "${SCAN_ROOTS[@]}"; do
    if [[ ! -d "$root" ]]; then
        continue
    fi

    find "$root" -name "settings.local.json" 2>/dev/null | while read -r settings_file; do
        # Quick validation check using Python module
        if ! python3 -c "
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path.home() / '.claude/hooks/governance'))
from permission_validator import validate_settings_permissions

settings_path = Path('$settings_file')
if settings_path.exists():
    try:
        settings = json.loads(settings_path.read_text())
        all_valid, errors = validate_settings_permissions(settings)
        sys.exit(0 if all_valid else 1)
    except:
        sys.exit(1)
else:
    sys.exit(0)
" 2>/dev/null; then
            echo "$(date -Iseconds)|$settings_file|corruption detected" >> "$LOG_FILE"

            # Alert user
            "$HOME/bin/notify.sh" \
                "Settings Corruption Detected" \
                "Corruption in $settings_file - auto-sanitization will run on next session" \
                --priority high \
                --channel both 2>/dev/null || true
        fi
    done
done
