#!/usr/bin/env python3
"""
Sunk-cost resource registry reader — IDEA-10124.

Single source of truth for subscription-covered / sunk-cost resources.
Registry file: ~/.claude/sunk-cost-resources.yaml

Usage (CLI):
    sunk-cost-resources list          # print all resources
    sunk-cost-resources candidates    # print multi_check_model entries as JSON

Usage (library):
    from sunk_cost_resources import load_registry, get_multi_check_candidates
    candidates = get_multi_check_candidates()
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REGISTRY_PATH = Path.home() / ".claude" / "sunk-cost-resources.yaml"

REQUIRED_FIELDS = {"resource_name", "provider", "billing_model"}
REQUIRED_ROUTE_FIELDS = {"type", "key"}


def load_registry() -> dict[str, Any]:
    """Load and validate the sunk-cost resource registry.

    Returns an empty dict (not raises) on any failure — callers must
    treat empty-dict as "no sunk-cost resources registered."
    """
    try:
        import yaml
    except ImportError:
        print(
            "WARNING: pyyaml not installed; sunk-cost registry unavailable. "
            "Install with: pip install pyyaml",
            file=sys.stderr,
        )
        return {}

    try:
        data = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as exc:
        print(
            f"WARNING: sunk-cost-resources.yaml unreadable ({exc}); "
            f"treating as empty registry.",
            file=sys.stderr,
        )
        return {}

    if data is None:
        return {}  # empty file is valid — no resources registered yet

    if not isinstance(data, dict):
        print(
            "WARNING: sunk-cost-resources.yaml is malformed (expected mapping at root); "
            "treating as empty registry.",
            file=sys.stderr,
        )
        return {}

    _validate_registry(data)
    return data


def _validate_registry(data: dict) -> None:
    """Warn loudly on schema issues; never raise (registry is advisory)."""
    version = data.get("schema_version")
    if version != "1.0":
        print(
            f"WARNING: sunk-cost-resources.yaml schema_version={version!r} "
            f"(expected '1.0'); some fields may be misread.",
            file=sys.stderr,
        )

    for i, resource in enumerate(data.get("resources", [])):
        missing = REQUIRED_FIELDS - set(resource.keys())
        if missing:
            name = resource.get("resource_name", f"<resource[{i}]>")
            print(
                f"WARNING: sunk-cost resource {name!r} missing required fields: "
                f"{sorted(missing)}",
                file=sys.stderr,
            )
        for j, route in enumerate(resource.get("covered_routes", [])):
            missing_r = REQUIRED_ROUTE_FIELDS - set(route.keys())
            if missing_r:
                name = resource.get("resource_name", f"<resource[{i}]>")
                print(
                    f"WARNING: route[{j}] of {name!r} missing fields: "
                    f"{sorted(missing_r)} — skipping.",
                    file=sys.stderr,
                )


def get_multi_check_candidates() -> list[dict[str, Any]]:
    """Return covered_routes entries with type='multi_check_model'.

    Each entry includes the parent resource metadata merged in:
        {
            "resource_name": ...,
            "provider": ...,
            "billing_model": ...,
            "type": "multi_check_model",
            "key": "glm-goose",
            "invocation": "goose",
            "model_id": "glm-5-goose",
            ...
        }
    """
    registry = load_registry()
    candidates = []
    seen_keys: set[str] = set()

    for resource in registry.get("resources", []):
        base = {
            "resource_name": resource.get("resource_name", ""),
            "provider": resource.get("provider", ""),
            "billing_model": resource.get("billing_model", ""),
        }
        for route in resource.get("covered_routes", []):
            if route.get("type") != "multi_check_model":
                continue
            key = route.get("key")
            if not key:
                continue
            if key in seen_keys:
                print(
                    f"WARNING: duplicate multi_check_model key {key!r} in registry; "
                    f"first entry wins.",
                    file=sys.stderr,
                )
                continue
            seen_keys.add(key)
            candidates.append({**base, **route})

    return candidates


def _cli_list(registry: dict) -> None:
    resources = registry.get("resources", [])
    if not resources:
        print("No sunk-cost resources registered.")
        return
    for r in resources:
        routes = r.get("covered_routes", [])
        route_summary = (
            ", ".join(rt.get("key", "?") for rt in routes) if routes else "(none)"
        )
        print(
            f"  {r.get('resource_name')} [{r.get('billing_model')}] "
            f"— routes: {route_summary} — verified: {r.get('verified_date', '?')}"
        )


def _cli_candidates(candidates: list) -> None:
    print(json.dumps(candidates, indent=2))


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"

    if cmd == "list":
        _cli_list(load_registry())
    elif cmd == "candidates":
        _cli_candidates(get_multi_check_candidates())
    else:
        print(f"Usage: sunk-cost-resources [list|candidates]", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
