#!/usr/bin/env bash
#
# paths.sh - Dynamic Path Resolution Library
#
# Sources canonical paths from ~/dev/AUTHORITATIVE.yaml with fallback defaults.
# Provides validated path resolution for infrastructure scripts.
#
# USAGE:
#   source ~/dev/infrastructure/tools/lib/paths.sh
#   INFRA=$(get_infrastructure_path)
#   PROJECTS=$(get_projects_path)
#   ARCHIVE=$(get_archive_path)
#   MCP_SERVERS=$(get_mcp_servers_path)
#   TOOLS=$(get_tools_path)
#
# FEATURES:
#   - Idempotent sourcing (safe to source multiple times)
#   - Dynamic YAML parsing with yq
#   - Path existence validation
#   - Fallback to sensible defaults
#   - Warning messages for missing paths
#
# DEPENDENCIES:
#   - yq (for YAML parsing)
#   - bash 4.0+
#

# Idempotency guard - only initialize once
if [[ "${_PATHS_LIB_LOADED:-}" == "true" ]]; then
    return 0
fi

# Mark library as loaded
readonly _PATHS_LIB_LOADED=true

# Constants
readonly AUTHORITATIVE_YAML="${HOME}/dev/AUTHORITATIVE.yaml"

# Fallback defaults (used if AUTHORITATIVE.yaml is missing or path not found)
readonly DEFAULT_INFRASTRUCTURE_PATH="${HOME}/dev/infrastructure"
readonly DEFAULT_PROJECTS_PATH="${HOME}/dev/projects"
readonly DEFAULT_ARCHIVE_PATH="${HOME}/docs/archive"
readonly DEFAULT_MCP_SERVERS_PATH="${HOME}/.local/share/mcp-servers"
readonly DEFAULT_TOOLS_PATH="${HOME}/dev/infrastructure/tools"

#######################################
# Check if yq is available
# Globals:
#   None
# Returns:
#   0 if yq is available, 1 otherwise
#######################################
_has_yq() {
    command -v yq &>/dev/null
}

#######################################
# Expand tilde in path to $HOME
# Arguments:
#   $1 - Path with potential tilde
# Outputs:
#   Expanded path to stdout
#######################################
_expand_tilde() {
    local path="$1"
    echo "${path/#\~/$HOME}"
}

#######################################
# Query AUTHORITATIVE.yaml for a path
# Arguments:
#   $1 - YQ query expression (e.g., '.infrastructure.location')
# Outputs:
#   Path from YAML or empty string if not found
#######################################
_query_yaml_path() {
    local query="$1"

    if [[ ! -f "${AUTHORITATIVE_YAML}" ]]; then
        return 1
    fi

    if ! _has_yq; then
        return 1
    fi

    local result
    result=$(yq eval "${query}" "${AUTHORITATIVE_YAML}" 2>/dev/null)

    if [[ -z "${result}" || "${result}" == "null" ]]; then
        return 1
    fi

    echo "${result}"
}

#######################################
# Validate that a path exists
# Arguments:
#   $1 - Path to validate
# Returns:
#   0 if path exists, 1 otherwise
#######################################
_validate_path() {
    local path="$1"
    [[ -d "${path}" ]]
}

#######################################
# Get path with fallback and validation
# Arguments:
#   $1 - YQ query expression
#   $2 - Fallback default path
#   $3 - Path description (for warnings)
# Outputs:
#   Validated path to stdout
# Returns:
#   0 if path exists, 1 if using fallback
#######################################
_get_path() {
    local query="$1"
    local default="$2"
    local description="$3"
    local path
    local source="default"

    # Try to get path from YAML
    if path=$(_query_yaml_path "${query}"); then
        path=$(_expand_tilde "${path}")
        source="AUTHORITATIVE.yaml"
    else
        path="${default}"
    fi

    # Validate path exists
    if _validate_path "${path}"; then
        echo "${path}"
        return 0
    else
        # Path doesn't exist, warn and try fallback if we got path from YAML
        if [[ "${source}" == "AUTHORITATIVE.yaml" ]]; then
            echo "Warning: ${description} from YAML not found: ${path}" >&2
            echo "Warning: Falling back to default: ${default}" >&2
            path="${default}"

            # Validate fallback
            if _validate_path "${path}"; then
                echo "${path}"
                return 1
            else
                echo "Warning: ${description} fallback not found: ${path}" >&2
                echo "${path}"
                return 1
            fi
        else
            echo "Warning: ${description} not found: ${path}" >&2
            echo "${path}"
            return 1
        fi
    fi
}

#######################################
# Get infrastructure path (~/dev/infrastructure/)
# Globals:
#   DEFAULT_INFRASTRUCTURE_PATH
#   AUTHORITATIVE_YAML
# Outputs:
#   Infrastructure path to stdout
# Returns:
#   0 if path exists, 1 otherwise
#######################################
get_infrastructure_path() {
    _get_path '.infrastructure.location' \
              "${DEFAULT_INFRASTRUCTURE_PATH}" \
              "Infrastructure path"
}

#######################################
# Get projects path (~/dev/projects/)
# Globals:
#   DEFAULT_PROJECTS_PATH
#   AUTHORITATIVE_YAML
# Outputs:
#   Projects path to stdout
# Returns:
#   0 if path exists, 1 otherwise
#######################################
get_projects_path() {
    _get_path '.projects.location' \
              "${DEFAULT_PROJECTS_PATH}" \
              "Projects path"
}

#######################################
# Get archive path (~/docs/archive/)
# Globals:
#   DEFAULT_ARCHIVE_PATH
#   AUTHORITATIVE_YAML
# Outputs:
#   Archive path to stdout
# Returns:
#   0 if path exists, 1 otherwise
#######################################
get_archive_path() {
    # Archive path not currently in AUTHORITATIVE.yaml, use default
    local path="${DEFAULT_ARCHIVE_PATH}"

    if _validate_path "${path}"; then
        echo "${path}"
        return 0
    else
        echo "Warning: Archive path not found: ${path}" >&2
        echo "${path}"
        return 1
    fi
}

#######################################
# Get MCP servers path (~/.local/share/mcp-servers/)
# Globals:
#   DEFAULT_MCP_SERVERS_PATH
#   AUTHORITATIVE_YAML
# Outputs:
#   MCP servers path to stdout
# Returns:
#   0 if path exists, 1 otherwise
#######################################
get_mcp_servers_path() {
    _get_path '.mcp_servers.location' \
              "${DEFAULT_MCP_SERVERS_PATH}" \
              "MCP servers path"
}

#######################################
# Get tools path (~/dev/infrastructure/tools/)
# Globals:
#   DEFAULT_TOOLS_PATH
#   AUTHORITATIVE_YAML
# Outputs:
#   Tools path to stdout
# Returns:
#   0 if path exists, 1 otherwise
#######################################
get_tools_path() {
    # Tools path is derived from infrastructure path
    local infra_path
    infra_path=$(get_infrastructure_path)
    local path="${infra_path}/tools"

    if _validate_path "${path}"; then
        echo "${path}"
        return 0
    else
        echo "Warning: Tools path not found: ${path}" >&2
        echo "${path}"
        return 1
    fi
}

#######################################
# Print all canonical paths (for debugging)
# Outputs:
#   All paths with their sources to stdout
#######################################
print_all_paths() {
    echo "Canonical Paths:"
    echo "  Infrastructure: $(get_infrastructure_path)"
    echo "  Projects:       $(get_projects_path)"
    echo "  Archive:        $(get_archive_path)"
    echo "  MCP Servers:    $(get_mcp_servers_path)"
    echo "  Tools:          $(get_tools_path)"
    echo ""
    echo "Source: ${AUTHORITATIVE_YAML}"
    if ! _has_yq; then
        echo "Warning: yq not found, using defaults only"
    fi
}

# Export functions for use in scripts
export -f get_infrastructure_path
export -f get_projects_path
export -f get_archive_path
export -f get_mcp_servers_path
export -f get_tools_path
export -f print_all_paths
