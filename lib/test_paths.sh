#!/usr/bin/env bash
#
# test_paths.sh - Test script for paths.sh library
#
# Demonstrates usage and validates all functions

set -euo pipefail

# Source the library
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/paths.sh"

echo "========================================"
echo "Testing paths.sh Library"
echo "========================================"
echo ""

# Test individual functions
echo "1. Testing get_infrastructure_path():"
INFRA=$(get_infrastructure_path)
echo "   Result: ${INFRA}"
echo "   Exists: $([[ -d "${INFRA}" ]] && echo "YES" || echo "NO")"
echo ""

echo "2. Testing get_projects_path():"
PROJECTS=$(get_projects_path)
echo "   Result: ${PROJECTS}"
echo "   Exists: $([[ -d "${PROJECTS}" ]] && echo "YES" || echo "NO")"
echo ""

echo "3. Testing get_archive_path():"
ARCHIVE=$(get_archive_path)
echo "   Result: ${ARCHIVE}"
echo "   Exists: $([[ -d "${ARCHIVE}" ]] && echo "YES" || echo "NO")"
echo ""

echo "4. Testing get_mcp_servers_path():"
MCP_SERVERS=$(get_mcp_servers_path)
echo "   Result: ${MCP_SERVERS}"
echo "   Exists: $([[ -d "${MCP_SERVERS}" ]] && echo "YES" || echo "NO")"
echo ""

echo "5. Testing get_tools_path():"
TOOLS=$(get_tools_path)
echo "   Result: ${TOOLS}"
echo "   Exists: $([[ -d "${TOOLS}" ]] && echo "YES" || echo "NO")"
echo ""

# Test idempotency
echo "6. Testing idempotency (sourcing twice):"
source "${SCRIPT_DIR}/paths.sh"
INFRA2=$(get_infrastructure_path)
if [[ "${INFRA}" == "${INFRA2}" ]]; then
    echo "   PASS: Same result after second source"
else
    echo "   FAIL: Different results"
    exit 1
fi
echo ""

# Test print_all_paths function
echo "7. Testing print_all_paths():"
print_all_paths
echo ""

echo "========================================"
echo "All tests completed successfully"
echo "========================================"
