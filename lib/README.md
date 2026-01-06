# Path Resolution Library

Shared bash library for resolving canonical paths from `~/dev/AUTHORITATIVE.yaml`.

## Overview

`paths.sh` provides functions to dynamically resolve infrastructure paths with:
- YAML parsing (when `yq` is available)
- Fallback to sensible defaults
- Path existence validation
- Idempotent sourcing (safe to source multiple times)

## Installation

The library is already installed at:
```
~/dev/infrastructure/tools/lib/paths.sh
```

## Dependencies

- **bash 4.0+** (required)
- **yq** (optional, for YAML parsing)

Without `yq`, the library falls back to hardcoded defaults matching current paths.

## Usage

### Basic Usage

```bash
#!/usr/bin/env bash

# Source the library
source ~/dev/infrastructure/tools/lib/paths.sh

# Get paths
INFRA=$(get_infrastructure_path)
PROJECTS=$(get_projects_path)
ARCHIVE=$(get_archive_path)
MCP_SERVERS=$(get_mcp_servers_path)
TOOLS=$(get_tools_path)

# Use the paths
cd "${INFRA}" || exit 1
ls "${PROJECTS}"
```

### Available Functions

| Function | Returns | Example |
|----------|---------|---------|
| `get_infrastructure_path()` | `~/dev/infrastructure/` | `/home/user/dev/infrastructure` |
| `get_projects_path()` | `~/dev/projects/` | `/home/user/dev/projects` |
| `get_archive_path()` | `~/docs/archive/` | `/home/user/docs/archive` |
| `get_mcp_servers_path()` | `~/.local/share/mcp-servers/` | `/home/user/.local/share/mcp-servers` |
| `get_tools_path()` | `~/dev/infrastructure/tools/` | `/home/user/dev/infrastructure/tools` |
| `print_all_paths()` | Prints all paths for debugging | (stdout) |

### Error Handling

Functions return:
- **Exit code 0**: Path exists
- **Exit code 1**: Path doesn't exist (still returns the path string)

Warnings are printed to stderr if paths don't exist.

```bash
if ! INFRA=$(get_infrastructure_path); then
    echo "Warning: Infrastructure path doesn't exist, creating..."
    mkdir -p "${INFRA}"
fi
```

### Debugging

```bash
source ~/dev/infrastructure/tools/lib/paths.sh
print_all_paths
```

Output:
```
Canonical Paths:
  Infrastructure: /home/ichardart/dev/infrastructure
  Projects:       /home/ichardart/dev/projects
  Archive:        /home/ichardart/docs/archive
  MCP Servers:    /home/ichardart/.local/share/mcp-servers
  Tools:          /home/ichardart/dev/infrastructure/tools

Source: /home/ichardart/dev/AUTHORITATIVE.yaml
Warning: yq not found, using defaults only
```

## How It Works

1. **YAML Query (if `yq` available)**:
   - Queries `~/dev/AUTHORITATIVE.yaml` for paths
   - Example: `.infrastructure.location` → `~/dev/infrastructure`

2. **Fallback to Defaults**:
   - If YAML query fails or `yq` not installed
   - Uses hardcoded paths matching current structure

3. **Path Validation**:
   - Checks if resolved path exists
   - Warns if missing (but still returns the path)

4. **Idempotency**:
   - Can be sourced multiple times safely
   - Uses `_PATHS_LIB_LOADED` guard variable

## Examples

See:
- `test_paths.sh` - Comprehensive test suite
- `example_usage.sh` - Common use cases

## Testing

```bash
# Run test suite
~/dev/infrastructure/tools/lib/test_paths.sh

# Run example usage
~/dev/infrastructure/tools/lib/example_usage.sh
```

## Migration Guide

### Before (hardcoded paths)

```bash
INFRA="${HOME}/dev/infrastructure"
PROJECTS="${HOME}/dev/projects"
```

### After (using library)

```bash
source ~/dev/infrastructure/tools/lib/paths.sh
INFRA=$(get_infrastructure_path)
PROJECTS=$(get_projects_path)
```

### Benefits

- **Single source of truth**: Paths defined in AUTHORITATIVE.yaml
- **Validation**: Warns if paths don't exist
- **Future-proof**: Handles path changes without script updates
- **Fallback safety**: Works even if YAML is unavailable

## Design Philosophy

1. **Fail soft**: Always returns a path, even if validation fails
2. **Warn loudly**: Prints warnings to stderr for missing paths
3. **Zero assumptions**: Validates everything, assumes nothing
4. **Idempotent**: Safe to source in any context

## Related

- `~/dev/AUTHORITATIVE.yaml` - Canonical path definitions
- `~/dev/infrastructure/tools/smart-discover.sh` - Uses this library
- `~/dev/infrastructure/dev-env-docs/` - Documentation about paths
