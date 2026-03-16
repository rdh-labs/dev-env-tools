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
        --input)  [[ $# -ge 2 ]] || usage; INPUT="$2"; shift 2 ;;
        --output) [[ $# -ge 2 ]] || usage; OUTPUT="$2"; shift 2 ;;
        *)        usage ;;
    esac
done

if [[ -z "$INPUT" || -z "$OUTPUT" ]]; then
    usage
fi

# Finding #13: Guard against input path == output path (would clobber source)
if INPUT_REAL="$(realpath -e "$INPUT" 2>/dev/null)" && OUTPUT_REAL="$(realpath -e "$OUTPUT" 2>/dev/null)"; then
    if [[ "$INPUT_REAL" == "$OUTPUT_REAL" ]]; then
        echo "ERROR: --input and --output resolve to the same path: $INPUT_REAL" >&2
        exit 1
    fi
fi

# Collect schema files
SCHEMA_FILES=()
if [[ -f "$INPUT" ]]; then
    case "$INPUT" in
        *.prisma|*.sql) ;;
        *) echo "ERROR: Unsupported file type: $INPUT (expected .prisma or .sql)" >&2; exit 1 ;;
    esac
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
    # Finding #8: First pass — collect enum and type block names for exclusion
    # This prevents treating enum fields (e.g. role Role) as implicit relations
    local enum_names
    enum_names="$(awk '/^[[:space:]]*(enum|type)[[:space:]]+[A-Za-z]/{print $2}' < "$file" | tr '\n' '|' | sed 's/|$//')"
    local enum_pat=""
    if [[ -n "$enum_names" ]]; then
        enum_pat="^($enum_names)$"
    fi

    awk -v enum_pat="$enum_pat" '
    # Phase 6: enum blocks — render members as "string MEMBER_NAME"
    !in_model && /^[[:space:]]*enum[[:space:]]+[A-Za-z]/ {
        enum_block_name = $2
        in_enum = 1
        print "    " enum_block_name " {"
        next
    }
    in_enum && /^[[:space:]]*\}/ {
        print "    }"
        in_enum = 0
        next
    }
    in_enum {
        sub(/\/\/.*/, "")
        if (match($0, /^[[:space:]]+([A-Za-z_][A-Za-z0-9_]*)/, arr)) {
            print "        string " arr[1]
        }
        next
    }
    # Phase 6: type blocks — parse scalar fields, no relation detection
    !in_model && /^[[:space:]]*type[[:space:]]+[A-Za-z]/ {
        type_block_name = $2
        in_type = 1
        type_field_count = 0
        next
    }
    in_type && /^[[:space:]]*\}/ {
        print "    " type_block_name " {"
        for (i = 1; i <= type_field_count; i++) { print type_fields[i] }
        print "    }"
        type_field_count = 0
        in_type = 0
        next
    }
    in_type {
        sub(/\/\/.*/, "")
        if (match($0, /^[[:space:]]+([a-zA-Z_][a-zA-Z0-9_]*)[[:space:]]+([A-Za-z]+)/, arr)) {
            tfname = arr[1]; tftype = arr[2]
            tmtype = "string"
            if (tftype ~ /^(Int|BigInt)$/) tmtype = "int"
            else if (tftype ~ /^(Float|Decimal)$/) tmtype = "float"
            else if (tftype == "Boolean") tmtype = "boolean"
            else if (tftype == "DateTime") tmtype = "timestamp"
            else if (tftype == "Json") tmtype = "json"
            type_field_count++
            type_fields[type_field_count] = sprintf("        %s %s", tmtype, tfname)
        }
        next
    }
    /^[[:space:]]*model[[:space:]]+/ {
        model = $2
        mapped_name = model
        in_model = 1
        field_count = 0
        rel_count = 0
        next
    }
    in_model && /^[[:space:]]*\}/ {
        # Finding #10: flush buffered model output with mapped name (@@map support)
        print "    " mapped_name " {"
        for (i = 1; i <= field_count; i++) {
            print fields[i]
        }
        print "    }"
        # Relationships: apply mapped_name as source (Judge 2 amendment)
        for (i = 1; i <= rel_count; i++) {
            gsub("^    " model " ", "    " mapped_name " ", rels[i])
            print rels[i]
        }
        field_count = 0
        rel_count = 0
        in_model = 0
        next
    }
    in_model {
        # Remove comments
        sub(/\/\/.*/, "")
        # Finding #10: detect @@map("tablename") directive
        if (match($0, /@@map\("([^"]+)"/, arr)) {
            mapped_name = arr[1]
            next
        }
        # Skip @@index and other @@ directives
        if ($0 ~ /^[[:space:]]*@@/) next
        # Match field lines: name Type ...
        if (match($0, /^[[:space:]]+([a-zA-Z_][a-zA-Z0-9_]*)[[:space:]]+([A-Za-z]+)/, arr)) {
            fname = arr[1]
            ftype = arr[2]
            if (ftype ~ /^(String|Int|BigInt|Float|Decimal|Boolean|DateTime|Json|Bytes)$/) {
                # Scalar type: map to generic and buffer as field
                mtype = "string"
                if (ftype ~ /^(Int|BigInt)$/) mtype = "int"
                else if (ftype ~ /^(Float|Decimal)$/) mtype = "float"
                else if (ftype == "Boolean") mtype = "boolean"
                else if (ftype == "DateTime") mtype = "timestamp"
                else if (ftype == "Json") mtype = "json"

                constraint = ""
                if ($0 ~ /@id/) constraint = "PK"
                else if ($0 ~ /@unique/) constraint = "UK"

                field_count++
                if (constraint != "")
                    fields[field_count] = sprintf("        %s %s %s", mtype, fname, constraint)
                else
                    fields[field_count] = sprintf("        %s %s", mtype, fname)
            } else if (ftype ~ /^[A-Z]/) {
                # Non-scalar capitalized type: potential relation
                # Finding #8: exclude enum/type names from relation detection
                if (enum_pat != "" && ftype ~ enum_pat) next

                if ($0 ~ /@relation/) {
                    # Explicit @relation: always emit with }o--|| (no dedup — owner side only)
                    rel_count++
                    rels[rel_count] = sprintf("    %s }o--|| %s : \"references\"", model, ftype)
                } else {
                    # Implicit relation: infer cardinality + deduplicate bidirectional pairs
                    # Type[] -> many (}o--||), Type? -> optional (|o--||), Type -> one (||--||)
                    cardinality = "||--||"
                    if ($0 ~ ftype "\\[\\]") cardinality = "}o--||"
                    else if ($0 ~ ftype "\\?")  cardinality = "|o--||"

                    pair_key = model ":" ftype
                    rev_key  = ftype ":" model
                    if (!(pair_key in seen_pairs) && !(rev_key in seen_pairs)) {
                        seen_pairs[pair_key] = 1
                        rel_count++
                        rels[rel_count] = sprintf("    %s %s %s : \"references\"", model, cardinality, ftype)
                    }
                }
            }
        }
    }
    ' < "$file" >> "$OUTPUT"
}

# Parse SQL CREATE TABLE using awk (Phase 8: multi-line column accumulation)
parse_sql() {
    local file="$1"
    awk '
    # emit_col: process one complete (possibly multi-line) column definition
    function emit_col(ln,    af, nf, col, ctype, fi, w, constraint, refm, fk_target, np, fp) {
        gsub(/,[[:space:]]*$/, "", ln)   # strip trailing comma
        nf = split(ln, af)               # split by whitespace (strips leading/trailing)
        if (nf < 2) return
        if (af[1] !~ /^[a-zA-Z_]/) return
        if (toupper(af[1]) ~ /^(CONSTRAINT|PRIMARY|FOREIGN|UNIQUE|CHECK|INDEX)$/) return
        col = af[1]
        gsub(/["`\[\]]/, "", col)
        # Multi-word type: scan until constraint keyword (Finding #7)
        ctype = af[2]
        for (fi = 3; fi <= nf; fi++) {
            w = toupper(af[fi])
            gsub(/[^A-Z]/, "", w)
            if (w ~ /^(NOT|NULL|DEFAULT|PRIMARY|UNIQUE|REFERENCES|CHECK|ON|COLLATE|COMMENT|CONSTRAINT)$/) break
            ctype = ctype "_" af[fi]
        }
        ctype = tolower(ctype)
        gsub(/"/, "", ctype); gsub(/`/, "", ctype)
        gsub(/\[/, "", ctype); gsub(/\]/, "", ctype)
        gsub(/\(/, "", ctype); gsub(/\)/, "", ctype)
        gsub(/,/, "", ctype); gsub(/_+$/, "", ctype)
        constraint = ""
        if (ln ~ /PRIMARY[[:space:]]+KEY/) constraint = "PK"
        else if (ln ~ /UNIQUE/) constraint = "UK"
        # Finding #5: schema-qualified REFERENCES with all quoting styles
        if (match(ln, /REFERENCES[[:space:]]+([^(]+)\(/, refm)) {
            fk_target = refm[1]
            gsub(/[[:space:]]+$/, "", fk_target)
            gsub(/"/, "", fk_target); gsub(/`/, "", fk_target)
            gsub(/\[/, "", fk_target); gsub(/\]/, "", fk_target)
            np = split(fk_target, fp, ".")
            fk_target = fp[np]
            gsub(/[[:space:]]/, "", fk_target)
            sql_rel_count++
            sql_rels[sql_rel_count] = sprintf("    %s }o--|| %s : \"references\"", table, fk_target)
            constraint = "FK"
        }
        if (constraint != "")
            printf "        %s %s %s\n", ctype, col, constraint
        else
            printf "        %s %s\n", ctype, col
    }
    BEGIN { IGNORECASE = 1; sql_rel_count = 0; accum = ""; depth = 0; in_table = 0 }
    /CREATE[[:space:]]+TABLE/ {
        # Extract table name - handle IF NOT EXISTS and inline (
        line = $0
        sub(/.*CREATE[[:space:]]+TABLE[[:space:]]+/, "", line)
        sub(/IF[[:space:]]+NOT[[:space:]]+EXISTS[[:space:]]+/, "", line)
        sub(/[[:space:]]*\(.*/, "", line)
        gsub(/["`\[\]]/, "", line)
        n = split(line, parts, ".")
        table = parts[n]
        gsub(/[[:space:]]/, "", table)
        if (table != "") {
            print "    " table " {"
            in_table = 1; sql_rel_count = 0; accum = ""; depth = 0
        }
        next
    }
    # Opening paren on its own line after CREATE TABLE table_name
    in_table && /^[[:space:]]*\([[:space:]]*$/ { next }
    # Closing paren = end of CREATE TABLE body
    in_table && /^[[:space:]]*\)/ && depth == 0 {
        if (accum != "") { emit_col(accum); accum = "" }
        print "    }"
        for (i = 1; i <= sql_rel_count; i++) { print sql_rels[i] }
        sql_rel_count = 0; in_table = 0
        next
    }
    in_table {
        sub(/--.*/, "")
        if ($0 ~ /^[[:space:]]*$/) next
        # Track paren depth for this line (Finding #8: multi-line col boundary detection)
        tmp = $0; open_c = 0; close_c = 0
        gsub(/[^(]/, "", tmp); open_c = length(tmp)
        tmp = $0; gsub(/[^)]/, "", tmp); close_c = length(tmp)
        depth += open_c - close_c
        if (depth < 0) depth = 0
        # Accumulate line
        accum = (accum == "") ? $0 : (accum " " $0)
        # Emit when trailing comma seen at depth 0 (column definition complete)
        trimmed = accum; gsub(/[[:space:]]+$/, "", trimmed)
        if (depth == 0 && substr(trimmed, length(trimmed), 1) == ",") {
            emit_col(accum); accum = ""
        }
        next
    }
    ' < "$file" >> "$OUTPUT"
}

# Process each schema file
for schema_file in "${SCHEMA_FILES[@]}"; do
    echo "" >> "$OUTPUT"
    # Finding #14: sanitize filename in comment (strip non-printable control chars)
    safe_name="$(basename "$schema_file" | tr -dc '[:print:]')"
    echo "    %% Source: $safe_name" >> "$OUTPUT"

    case "$schema_file" in
        *.prisma) parse_prisma "$schema_file" ;;
        *.sql)    parse_sql "$schema_file" ;;
    esac
done

echo "" >> "$OUTPUT"
echo "[schema-to-mermaid] Generated: $OUTPUT (from ${#SCHEMA_FILES[@]} schema files)"
