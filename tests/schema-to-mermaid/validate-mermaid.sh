#!/usr/bin/env bash
# validate-mermaid.sh -- Structural Mermaid erDiagram validator
# Usage: ./validate-mermaid.sh <file.mmd>
# Exit:  0=valid, 1=invalid

set -euo pipefail

FILE="${1:-}"
[[ -f "$FILE" ]] || { echo "ERROR: Usage: $0 <file.mmd>"; exit 1; }

ERRORS=0
err() { echo "  STRUCTURAL ERROR: $1"; ERRORS=$((ERRORS + 1)); }

# 1. erDiagram header present
grep -q "^erDiagram" "$FILE" || err "Missing erDiagram header"

# 2. Balanced entity braces
OPEN="$(grep -cE '^    [A-Za-z][A-Za-z0-9_]+ \{$' "$FILE" 2>/dev/null || echo 0)"
CLOSE="$(grep -cE '^    \}$' "$FILE" 2>/dev/null || echo 0)"
[[ "$OPEN" -eq "$CLOSE" ]] || err "Unbalanced entity braces: $OPEN open, $CLOSE close"

# 3. No bare ( in type names on field lines (8-space indent)
if grep -E "^        [a-z][a-z0-9_]*\(" "$FILE" >/dev/null 2>&1; then
    err "Bare ( in field type name (unstripped parenthesis)"
fi

# 4. No bare ) in type names on field lines
if grep -E "^        [a-z0-9_]+[0-9]\)" "$FILE" >/dev/null 2>&1; then
    err "Bare ) in field type name (unstripped closing paren)"
fi

# 5. Relationship lines must be outside entity blocks
# Track: inside entity when we see {, outside when we see }
IN_ENTITY=0
LINENO=0
while IFS= read -r line; do
    LINENO=$((LINENO + 1))
    if echo "$line" | grep -qE '^    [A-Za-z][A-Za-z0-9_]+ \{$'; then
        IN_ENTITY=1
    fi
    if echo "$line" | grep -qE '^    \}$'; then
        IN_ENTITY=0
    fi
    if [[ $IN_ENTITY -eq 1 ]]; then
        if echo "$line" | grep -qE '\}o--|o--|o\|--|--\|'; then
            err "Relationship line inside entity block at line $LINENO: $line"
        fi
    fi
done < "$FILE"

if [[ $ERRORS -gt 0 ]]; then
    echo "INVALID: $FILE ($ERRORS structural errors)"
    exit 1
fi
echo "VALID: $FILE"
exit 0
