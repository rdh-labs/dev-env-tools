#!/usr/bin/env bash
# schema-to-mermaid.sh - Convert Prisma/SQL schemas to Mermaid ER diagrams
#
# Usage: schema-to-mermaid.sh --input <dir-or-file> --output <file.mmd>
#
# Exit codes: 0=success, 1=invalid args, 2=no schema files found
#
# Source: Adapted from Gemini Q1 2026 Visual Retrospective - Workflow A
# Ecosystem: DEC-224, Pattern 19, WF-002

set -euo pipefail

INPUT=""
OUTPUT=""

usage() {
    echo "Usage: $0 --input <dir-or-file> --output <file.mmd>"
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --input)  INPUT="$2"; shift 2 ;;
        --output) OUTPUT="$2"; shift 2 ;;
        *)        usage ;;
    esac
done

if [[ -z "$INPUT" || -z "$OUTPUT" ]]; then
    usage
fi

# Collect schema files
SCHEMA_FILES=()
if [[ -f "$INPUT" ]]; then
    SCHEMA_FILES+=("$INPUT")
elif [[ -d "$INPUT" ]]; then
    while IFS= read -r -d '' f; do
        SCHEMA_FILES+=("$f")
    done < <(find "$INPUT" \( -name "*.prisma" -o -name "*.sql" \) -type f -print0 2>/dev/null)
else
    echo "ERROR: Input not found: $INPUT" >&2
    exit 1
fi

if [[ ${#SCHEMA_FILES[@]} -eq 0 ]]; then
    echo "ERROR: No .prisma or .sql files found in $INPUT" >&2
    exit 2
fi

# Write Mermaid header
{
    echo "%% Auto-generated ER diagram from schema files"
    echo "%% Source: ${SCHEMA_FILES[*]}"
    echo "%% Generated: $(date -Iseconds)"
    echo "%% Re-run: schema-to-mermaid.sh --input $INPUT --output $OUTPUT"
    echo ""
    echo "erDiagram"
} > "$OUTPUT"

# Parse Prisma models using awk (more robust than bash regex)
parse_prisma() {
    local file="$1"
    awk '
    /^[[:space:]]*model[[:space:]]+/ {
        model = $2
        print "    " model " {"
        in_model = 1
        next
    }
    in_model && /^[[:space:]]*\}/ {
        print "    }"
        # Print buffered relationships after closing brace
        for (i = 1; i <= rel_count; i++) {
            print rels[i]
        }
        rel_count = 0
        in_model = 0
        next
    }
    in_model {
        # Remove comments
        sub(/\/\/.*/, "")
        # Match field lines: name Type ...
        if (match($0, /^[[:space:]]+([a-zA-Z_][a-zA-Z0-9_]*)[[:space:]]+([A-Za-z]+)/, arr)) {
            fname = arr[1]
            ftype = arr[2]
            # Skip relation fields (capitalized, not a built-in type)
            if (ftype ~ /^(String|Int|BigInt|Float|Decimal|Boolean|DateTime|Json|Bytes)$/) {
                # Map to generic types
                mtype = "string"
                if (ftype ~ /^(Int|BigInt)$/) mtype = "int"
                else if (ftype ~ /^(Float|Decimal)$/) mtype = "float"
                else if (ftype == "Boolean") mtype = "boolean"
                else if (ftype == "DateTime") mtype = "timestamp"
                else if (ftype == "Json") mtype = "json"

                constraint = ""
                if ($0 ~ /@id/) constraint = "PK"
                else if ($0 ~ /@unique/) constraint = "UK"

                printf "        %s %s %s\n", mtype, fname, constraint
            } else if (ftype ~ /^[A-Z]/ && $0 ~ /@relation/) {
                # Buffer relationship for output after entity block
                rel_count++
                rels[rel_count] = sprintf("    %s }o--|| %s : \"references\"", model, ftype)
            }
        }
    }
    ' "$file" >> "$OUTPUT"
}

# Parse SQL CREATE TABLE using awk
parse_sql() {
    local file="$1"
    awk '
    BEGIN { IGNORECASE = 1 }
    /CREATE[[:space:]]+TABLE/ {
        # Extract table name
        line = $0
        sub(/.*CREATE[[:space:]]+TABLE[[:space:]]+/, "", line)
        sub(/[[:space:]]*\(.*/, "", line)
        gsub(/["`\[\]]/, "", line)  # Remove quoting
        # Remove schema prefix
        n = split(line, parts, ".")
        table = parts[n]
        gsub(/[[:space:]]/, "", table)
        if (table != "") {
            print "    " table " {"
            in_table = 1
        }
        next
    }
    in_table && /^\)/ {
        print "    }"
        in_table = 0
        next
    }
    in_table {
        # Remove comments
        sub(/--.*/, "")
        # Skip pure constraint lines
        if ($1 ~ /^(CONSTRAINT|PRIMARY|FOREIGN|UNIQUE|CHECK|INDEX)$/i) next
        # Match column: name type ...
        if (NF >= 2 && $1 ~ /^[a-zA-Z_]/) {
            col = $1
            gsub(/["`\[\]]/, "", col)
            ctype = tolower($2)
            gsub(/["`\[\](,]/, "", ctype)

            constraint = ""
            if ($0 ~ /PRIMARY[[:space:]]+KEY/i) constraint = "PK"
            else if ($0 ~ /UNIQUE/i) constraint = "UK"

            # Detect inline FK
            if (match($0, /REFERENCES[[:space:]]+([A-Za-z_][A-Za-z0-9_]*)/, ref)) {
                printf "    %s }o--|| %s : \"references\"\n", table, ref[1]
                constraint = "FK"
            }

            printf "        %s %s %s\n", ctype, col, constraint
        }
    }
    ' "$file" >> "$OUTPUT"
}

# Process each schema file
for schema_file in "${SCHEMA_FILES[@]}"; do
    echo "" >> "$OUTPUT"
    echo "    %% Source: $(basename "$schema_file")" >> "$OUTPUT"

    case "$schema_file" in
        *.prisma) parse_prisma "$schema_file" ;;
        *.sql)    parse_sql "$schema_file" ;;
    esac
done

echo "" >> "$OUTPUT"
echo "[schema-to-mermaid] Generated: $OUTPUT (from ${#SCHEMA_FILES[@]} schema files)"
