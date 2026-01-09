#!/usr/bin/env python3
"""
gather-signals.py

Purpose:
    Aggregates signals (lessons, ideas, issues) relevant to a specific
    program or keyword from the ecosystem tracking files.

Usage:
    python3 gather-signals.py --keyword "infrastructure" --days 90
"""

import os
import sys
import argparse
import re
from datetime import datetime, timedelta

# Configuration
SEARCH_PATHS = [
    os.path.expanduser("~/lessons.md"),
    os.path.expanduser("~/dev/infrastructure/dev-env-docs/IDEAS-BACKLOG.md"),
    os.path.expanduser("~/dev/infrastructure/dev-env-docs/ISSUES-TRACKER.md"),
]

def parse_markdown_sections(file_path, keyword, days_lookback):
    signals = []
    if not os.path.exists(file_path):
        return [f"MISSING: {file_path}"]

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    sections = re.split(r"(^##\s+.*)", content, flags=re.MULTILINE)
    current_header = "Intro"
    cutoff_date = datetime.now() - timedelta(days=days_lookback)

    for part in sections:
        if part.startswith("##"):
            current_header = part.strip()
            continue
        
        text = part.strip()
        if not text: continue

        if keyword.lower() in current_header.lower() or keyword.lower() in text.lower():
            signals.append(f"SOURCE: {os.path.basename(file_path)}
SECTION: {current_header}
---
{text[:500]}...\n")
            continue

        date_match = re.search(r"(\d{4}-\d{2}-\d{2})", current_header)
        if date_match:
            try:
                entry_date = datetime.strptime(date_match.group(1), "