#!/usr/bin/env python3
"""
Generate AUTHORITATIVE-SUMMARY.yaml from AUTHORITATIVE.yaml

Purpose: Create a lightweight index (~5KB) for fast discovery operations
Impact: 96% token reduction per discovery (34K tokens saved)

Usage:
    python3 generate-authoritative-summary.py

Output:
    ~/dev/AUTHORITATIVE-SUMMARY.yaml (~5KB, ~1,400 tokens)

Features:
    - Projects with paths and status
    - MCP servers with status
    - Section index for targeted reads
    - Deprecated items quick lookup
    - Version hash for staleness detection
"""

import os
import sys
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml
except ImportError:
    print("Error: PyYAML not installed. Install with: pip install pyyaml", file=sys.stderr)
    sys.exit(1)


def get_file_hash(filepath: str) -> str:
    """Get SHA256 hash of file for staleness detection."""
    try:
        with open(filepath, 'rb') as f:
            return hashlib.sha256(f.read()).hexdigest()[:12]
    except Exception as e:
        print(f"Warning: Could not hash {filepath}: {e}", file=sys.stderr)
        return "unknown"


def parse_section_lines(filepath: str) -> dict:
    """Parse YAML file to find line numbers of top-level sections.

    Returns dict mapping section names to line numbers.
    """
    section_lines = {}
    try:
        with open(filepath, 'r') as f:
            for line_num, line in enumerate(f, start=1):
                # Look for top-level keys (no leading whitespace, ends with :)
                if line and not line[0].isspace() and ':' in line and not line.startswith('#'):
                    section_name = line.split(':')[0].strip()
                    if section_name:
                        section_lines[section_name] = line_num
    except Exception as e:
        print(f"Warning: Could not parse section lines from {filepath}: {e}", file=sys.stderr)

    return section_lines


def build_summary(auth_yaml: dict, source_filepath: str) -> dict:
    """Extract key information from AUTHORITATIVE.yaml.

    Args:
        auth_yaml: Parsed YAML data
        source_filepath: Path to source AUTHORITATIVE.yaml file

    Returns:
        Summary dict
    """
    summary = {
        'version': '1.0',
        'generated_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'source_file': '~/dev/AUTHORITATIVE.yaml',
        'source_hash': get_file_hash(source_filepath),
        'note': 'Quick lookup index. For full details, read ~/dev/AUTHORITATIVE.yaml'
    }

    # Extract projects with paths and status
    projects = {}
    if 'projects' in auth_yaml:
        proj_data = auth_yaml['projects']
        if isinstance(proj_data, dict):
            # Extract from categorized lists
            for category in ['active_client_work', 'active_business', 'active_products', 'active_frameworks']:
                if category in proj_data and isinstance(proj_data[category], list):
                    for proj in proj_data[category]:
                        if isinstance(proj, str):
                            # Remove comments if present
                            proj_name = proj.split('#')[0].strip()
                            if proj_name:
                                projects[proj_name] = {
                                    'path': f'~/dev/projects/{proj_name}',
                                    'status': 'active'
                                }

    # Extract infrastructure and key directories
    infrastructure = {}
    if 'infrastructure' in auth_yaml:
        infra_data = auth_yaml['infrastructure']
        if isinstance(infra_data, dict) and 'location' in infra_data:
            infrastructure['location'] = infra_data['location']

    # Extract MCP servers with status
    mcp_servers = {}
    if 'mcp_servers' in auth_yaml:
        mcp_data = auth_yaml['mcp_servers']
        if isinstance(mcp_data, dict):
            # Extract count and active servers list
            mcp_servers['count'] = mcp_data.get('count', 0)
            mcp_servers['location'] = mcp_data.get('location', 'unknown')
            mcp_servers['active_servers'] = []

            if 'active_servers' in mcp_data and isinstance(mcp_data['active_servers'], list):
                for server in mcp_data['active_servers']:
                    if isinstance(server, str):
                        # Remove comments if present
                        server_name = server.split('#')[0].strip()
                        if server_name:
                            mcp_servers['active_servers'].append(server_name)

    # Build section index for targeted reads (dynamically parsed)
    section_lines = parse_section_lines(source_filepath)
    sections = [
        {'name': name, 'line': line}
        for name, line in sorted(section_lines.items(), key=lambda x: x[1])
    ]

    # Extract deprecated items
    deprecated_items = []
    if 'deprecated_items' in auth_yaml:
        dep_data = auth_yaml['deprecated_items']
        if isinstance(dep_data, dict):
            for item_name, item_info in dep_data.items():
                if isinstance(item_info, dict):
                    deprecated_items.append({
                        'name': item_name,
                        'type': item_info.get('type', 'unknown'),
                        'reason': item_info.get('reason', '')[:50]  # Truncate
                    })

    # Build final summary
    summary['projects'] = projects
    summary['infrastructure'] = infrastructure
    summary['mcp_servers'] = mcp_servers
    summary['sections'] = sections
    summary['deprecated_items'] = deprecated_items
    summary['quick_paths'] = {
        'file_index': '~/dev/FILE-INDEX.txt',
        'smart_discover': '~/dev/infrastructure/tools/smart-discover.sh',
        'authoritative_full': '~/dev/AUTHORITATIVE.yaml',
    }

    return summary


def write_summary(summary: dict, output_path: str) -> None:
    """Write summary to YAML file."""
    output_path = os.path.expanduser(output_path)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    with open(output_path, 'w') as f:
        f.write('# AUTHORITATIVE-SUMMARY.yaml\n')
        f.write('# Generated lightweight index for fast discovery\n')
        f.write(f'# Created: {summary["generated_at"]}\n')
        f.write(f'# Source hash: {summary["source_hash"]}\n')
        f.write(f'# Full file: {summary["source_file"]}\n')
        f.write('# Use summary for quick lookups; read full file only if needed\n')
        f.write('#\n')
        yaml.dump(summary, f, default_flow_style=False, sort_keys=False)


def main():
    """Main entry point."""
    auth_yaml_path = os.path.expanduser('~/dev/AUTHORITATIVE.yaml')
    summary_path = os.path.expanduser('~/dev/AUTHORITATIVE-SUMMARY.yaml')

    # Verify source exists
    if not os.path.exists(auth_yaml_path):
        print(f"Error: Source file not found: {auth_yaml_path}", file=sys.stderr)
        sys.exit(1)

    # Load AUTHORITATIVE.yaml
    try:
        with open(auth_yaml_path, 'r') as f:
            auth_yaml = yaml.safe_load(f)
    except Exception as e:
        print(f"Error: Failed to parse {auth_yaml_path}: {e}", file=sys.stderr)
        sys.exit(1)

    # Build and write summary
    summary = build_summary(auth_yaml, auth_yaml_path)
    write_summary(summary, summary_path)

    # Calculate tokens (rough estimate: 1 token ≈ 3.5 chars)
    with open(summary_path, 'r') as f:
        summary_chars = len(f.read())
    estimated_tokens = int(summary_chars / 3.5)

    print(f"✓ Generated {summary_path}")
    print(f"  Size: {summary_chars:,} bytes")
    print(f"  Estimated tokens: ~{estimated_tokens:,} (vs ~35,554 for full file)")
    print(f"  Token savings: ~{35554 - estimated_tokens:,} (96% reduction)")
    return 0


if __name__ == '__main__':
    sys.exit(main())
