#!/usr/bin/env bash
# sanitize-pii.sh — Strip PII and secrets from source files before diagram generation
#
# Usage: sanitize-pii.sh --input-dir <dir> --output-dir <dir> [--verbose]
#
# Creates sanitized copies of schema/config files, replacing:
#   - IP addresses → 10.0.0.x placeholders
#   - Email addresses → user@example.com
#   - Connection strings → sanitized versions
#   - API keys/tokens/secrets → REDACTED markers
#   - AWS credentials (access keys + secret keys) → REDACTED
#   - Hostnames → generic .internal names
#
# Codex audit applied (2026-03-16): fixed connection string regex,
# added AWS secret key coverage, added base64/URL-encoding bypass detection.
#
# Exit codes:
#   0 — success
#   1 — invalid arguments
#   2 — input directory not found
#   3 — sanitization validation failed (PII still detected)
#
# Source: Gemini Q1 2026 Visual Retrospective — security hardening
# Ecosystem: DEC-224, Pattern 19, AGENT_RULES visual standards

set -euo pipefail

INPUT_DIR=""
OUTPUT_DIR=""
VERBOSE=0

usage() {
    echo "Usage: $0 --input-dir <dir> --output-dir <dir> [--verbose]"
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --input-dir)  INPUT_DIR="$2"; shift 2 ;;
        --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
        --verbose)    VERBOSE=1; shift ;;
        *)            usage ;;
    esac
done

if [[ -z "$INPUT_DIR" || -z "$OUTPUT_DIR" ]]; then
    usage
fi

if [[ ! -d "$INPUT_DIR" ]]; then
    echo "ERROR: Input directory not found: $INPUT_DIR" >&2
    exit 2
fi

mkdir -p "$OUTPUT_DIR"

log() {
    if [[ "$VERBOSE" -eq 1 ]]; then
        echo "[sanitize] $*" >&2
    fi
}

# File extensions to process
EXTENSIONS=("prisma" "sql" "tf" "yml" "yaml" "json" "env" "conf" "cfg" "toml" "hcl")

sanitize_file() {
    local src="$1"
    local dest="$2"

    cp "$src" "$dest"

    # 1. Replace IPv4 addresses (preserve CIDR notation structure)
    sed -i -E 's/\b([0-9]{1,3}\.){3}[0-9]{1,3}\b/10.0.0.1/g' "$dest"

    # 2. Replace email addresses
    sed -i -E 's/[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/user@example.com/g' "$dest"

    # 3. Replace connection strings — use # as delimiter to avoid pipe/slash conflicts
    #    (Codex audit fix: original | alternation inside sed s| was broken)
    sed -i -E 's#(postgres(ql)?|mysql|mongodb(\+srv)?|redis)://[^"'"'"'[:space:]]+#\1://user:REDACTED@db.internal:5432/app#g' "$dest"

    # 4. Replace Bearer tokens
    sed -i -E 's/Bearer [a-zA-Z0-9._\-]+/Bearer REDACTED/g' "$dest"

    # 5. Replace generic secret key=value patterns
    sed -i -E 's/(API_KEY|SECRET_KEY|ACCESS_TOKEN|AUTH_TOKEN|PRIVATE_KEY|PASSWORD|DB_PASSWORD|JWT_SECRET|SECRET_ACCESS_KEY|AWS_SECRET_ACCESS_KEY)\s*[=:]\s*[^"'"'"'[:space:]]+/\1=REDACTED/g' "$dest"

    # 6. Replace quoted secret values
    sed -i -E 's/(api[_-]?key|secret|token|password|credential)"?\s*[=:]\s*"[^"]*"/\1": "REDACTED"/gI' "$dest"

    # 7. Replace AWS access key IDs (AKIA, ASIA, AIDA, AROA prefixes)
    #    (Codex audit fix: original only checked AKIA in validation, not in sanitization)
    sed -i -E 's/\b(AKIA|ASIA|AIDA|AROA)[A-Z0-9]{16}\b/AKIAREDACTEDREDACTED/g' "$dest"

    # 8. Replace AWS secret access keys (40-char base64-like strings near AWS context)
    #    Heuristic: 40+ char alphanumeric+/+= strings on lines mentioning aws/secret/key
    sed -i -E '/[Aa][Ww][Ss]|[Ss]ecret|SECRET/{s/[A-Za-z0-9/+=]{40,}/REDACTED/g}' "$dest"

    # 9. Replace real hostnames (*.company.com, *.corp.net, *.prod.*, *.staging.*)
    sed -i -E 's/[a-zA-Z0-9-]+\.(company|corp|enterprise|prod|staging)\.[a-zA-Z]{2,}/service.internal/g' "$dest"

    # 10. Replace AWS ARNs
    sed -i -E 's/arn:aws:[a-z0-9-]+:[a-z0-9-]*:[0-9]{12}:/arn:aws:service:region:000000000000:/g' "$dest"

    # 11. Replace GitHub tokens (ghp_, gho_, ghs_, ghr_, github_pat_)
    sed -i -E 's/\b(ghp_|gho_|ghs_|ghr_|github_pat_)[a-zA-Z0-9_]+/\1REDACTED/g' "$dest"

    # 12. Replace Slack tokens (xoxb-, xoxp-, xoxs-, xoxa-)
    sed -i -E 's/\b(xoxb-|xoxp-|xoxs-|xoxa-)[a-zA-Z0-9-]+/\1REDACTED/g' "$dest"

    log "Sanitized: $src -> $dest"
}

# Process matching files
file_count=0
for ext in "${EXTENSIONS[@]}"; do
    while IFS= read -r -d '' file; do
        rel_path="${file#"$INPUT_DIR"/}"
        dest_file="$OUTPUT_DIR/$rel_path"
        mkdir -p "$(dirname "$dest_file")"
        sanitize_file "$file" "$dest_file"
        file_count=$((file_count + 1))
    done < <(find "$INPUT_DIR" -name "*.$ext" -type f -print0 2>/dev/null)
done

echo "[sanitize] Processed $file_count files"

# === Validation pass ===
VALIDATION_FAILED=0

validate_file() {
    local file="$1"

    # Check for real email patterns (not example.com)
    if grep -qPi '[a-z0-9._%+-]+@(?!example\.com)[a-z0-9.-]+\.[a-z]{2,}' "$file" 2>/dev/null; then
        echo "WARN: Possible residual email in $file" >&2
        VALIDATION_FAILED=1
    fi

    # Check for AWS access key IDs (all prefixes)
    if grep -qP '\b(AKIA|ASIA|AIDA|AROA)[A-Z0-9]{16}\b' "$file" 2>/dev/null; then
        echo "WARN: Possible AWS access key in $file" >&2
        VALIDATION_FAILED=1
    fi

    # Check for GitHub tokens
    if grep -qP '\b(ghp_|gho_|ghs_|ghr_|github_pat_)[a-zA-Z0-9_]{10,}' "$file" 2>/dev/null; then
        echo "WARN: Possible GitHub token in $file" >&2
        VALIDATION_FAILED=1
    fi

    # Check for Slack tokens
    if grep -qP '\b(xoxb-|xoxp-|xoxs-|xoxa-)[a-zA-Z0-9-]{10,}' "$file" 2>/dev/null; then
        echo "WARN: Possible Slack token in $file" >&2
        VALIDATION_FAILED=1
    fi

    # Check for base64-encoded secrets (heuristic: long base64 strings on suspicious lines)
    if grep -P '(secret|key|token|password|credential)' "$file" 2>/dev/null | \
       grep -qP '[A-Za-z0-9+/]{44,}={0,2}' 2>/dev/null; then
        echo "WARN: Possible base64-encoded secret in $file" >&2
        VALIDATION_FAILED=1
    fi

    # Check for URL-encoded @ signs in connection strings (bypass attempt)
    if grep -qP '(postgres|mysql|mongodb|redis)://[^"'"'"']*%40' "$file" 2>/dev/null; then
        echo "WARN: Possible URL-encoded connection string in $file" >&2
        VALIDATION_FAILED=1
    fi
}

while IFS= read -r -d '' file; do
    validate_file "$file"
done < <(find "$OUTPUT_DIR" -type f -print0)

if [[ "$VALIDATION_FAILED" -eq 1 ]]; then
    echo "ERROR: Sanitization validation failed — residual PII/secrets detected" >&2
    exit 3
fi

echo "[sanitize] Validation passed — no residual PII detected"
exit 0
