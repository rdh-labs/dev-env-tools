#!/usr/bin/env bash
# seed-hook-verification.sh
#
# Seeds ~/.claude/hooks/pending-hook-verifications.json so that
# new_hook_activation_verifier.py can verify the hook on next session start.
#
# Usage:
#   seed-hook-verification.sh <hook_name> <hook_path> [--matcher <matcher>] [--issue <ISSUE-NNN>]
#
# Example:
#   seed-hook-verification.sh shell_script_sanitizer \
#     ~/.claude/hooks/posttooluse/shell_script_sanitizer.py \
#     --matcher Write --issue ISSUE-2322
#
# Related: new_hook_activation_verifier.py, L-078, DEC-230, L-223

set -euo pipefail

PENDING_FILE="$HOME/.claude/hooks/pending-hook-verifications.json"

usage() {
    echo "Usage: $0 <hook_name> <hook_path> [--matcher <matcher>] [--issue <ISSUE-NNN>...]"
    echo ""
    echo "Seeds a hook verification entry for new_hook_activation_verifier.py"
    exit 1
}

if [[ $# -lt 2 ]]; then
    usage
fi

hook_name="$1"
hook_path="$2"
shift 2

matcher=""
issues=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --matcher)
            matcher="$2"
            shift 2
            ;;
        --issue)
            issues+=("$2")
            shift 2
            ;;
        *)
            echo "Unknown option: $1"
            usage
            ;;
    esac
done

# Resolve path
hook_path="$(realpath "$hook_path" 2>/dev/null || echo "$hook_path")"

# Validate hook file exists
if [[ ! -f "$hook_path" ]]; then
    echo "ERROR: Hook file not found: $hook_path" >&2
    exit 1
fi

# Build issues JSON array
issues_json="[]"
if [[ ${#issues[@]} -gt 0 ]]; then
    issues_json=$(printf '%s\n' "${issues[@]}" | jq -Rn '[inputs]')
fi

# Build new entry
new_entry=$(jq -cn \
    --arg name "$hook_name" \
    --arg path "$hook_path" \
    --arg matcher "$matcher" \
    --arg deployed "$(date +%Y-%m-%d)" \
    --argjson issues "$issues_json" \
    '{hook_name: $name, hook_path: $path, matcher: $matcher, deployed: $deployed, related_issues: $issues}')

# Append to existing pending file or create new one
if [[ -f "$PENDING_FILE" ]]; then
    existing=$(jq '.' "$PENDING_FILE" 2>/dev/null || echo '[]')
    # Check for duplicate
    dup=$(echo "$existing" | jq --arg name "$hook_name" '[.[] | select(.hook_name == $name)] | length')
    if [[ "$dup" -gt 0 ]]; then
        echo "Hook '$hook_name' already pending verification - updating entry."
        existing=$(echo "$existing" | jq --arg name "$hook_name" '[.[] | select(.hook_name != $name)]')
    fi
    echo "$existing" | jq --argjson entry "$new_entry" '. + [$entry]' > "$PENDING_FILE"
else
    echo "[$new_entry]" | jq '.' > "$PENDING_FILE"
fi

echo "Seeded verification for '$hook_name' in $PENDING_FILE"
echo "Will be verified on next Claude Code session start."
