#!/usr/bin/env bash
#
# example_usage.sh - Example usage of paths.sh library
#
# Demonstrates common use cases for the path resolution library

set -euo pipefail

# Source the paths library
source ~/dev/infrastructure/tools/lib/paths.sh

echo "Example 1: Basic path resolution"
echo "=================================="
INFRA=$(get_infrastructure_path)
echo "Infrastructure directory: ${INFRA}"
echo "Listing contents: $(ls -1 "${INFRA}" | head -5 | xargs)"
echo ""

echo "Example 2: Using paths in file operations"
echo "=========================================="
PROJECTS=$(get_projects_path)
echo "Projects directory: ${PROJECTS}"
if [[ -d "${PROJECTS}" ]]; then
    echo "Number of projects: $(find "${PROJECTS}" -maxdepth 1 -type d | wc -l)"
fi
echo ""

echo "Example 3: Checking MCP server installations"
echo "============================================="
MCP_SERVERS=$(get_mcp_servers_path)
echo "MCP servers directory: ${MCP_SERVERS}"
if [[ -d "${MCP_SERVERS}" ]]; then
    echo "Installed servers:"
    find "${MCP_SERVERS}" -maxdepth 1 -type d -not -name "mcp-servers" | while read -r server; do
        echo "  - $(basename "${server}")"
    done | head -5
fi
echo ""

echo "Example 4: Building derived paths"
echo "=================================="
TOOLS=$(get_tools_path)
echo "Tools directory: ${TOOLS}"
LIBS_DIR="${TOOLS}/lib"
echo "Libraries directory: ${LIBS_DIR}"
echo "This script location: $(realpath "${BASH_SOURCE[0]}")"
echo ""

echo "Example 5: Error handling"
echo "========================="
# Try to get a path that might not exist
ARCHIVE=$(get_archive_path)
if [[ -d "${ARCHIVE}" ]]; then
    echo "Archive directory exists: ${ARCHIVE}"
else
    echo "Archive directory not found (will be created when needed): ${ARCHIVE}"
fi
echo ""

echo "Example 6: Using paths in script logic"
echo "======================================="
INFRA=$(get_infrastructure_path)
CONFIG_FILE="${INFRA}/../AUTHORITATIVE.yaml"
if [[ -f "${CONFIG_FILE}" ]]; then
    echo "Found configuration file: ${CONFIG_FILE}"
    echo "File size: $(stat -c %s "${CONFIG_FILE}") bytes"
else
    echo "Configuration file not found"
fi
