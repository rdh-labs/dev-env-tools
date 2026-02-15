#!/usr/bin/env python3
"""
Governance File Editor - Reusable module for governance item operations.

Provides safe, reliable file operations for IDEAS-BACKLOG.md, ISSUES-TRACKER.md,
and DECISIONS-LOG.md with:
- Automatic ID collision detection and avoidance
- Comprehensive error handling
- Atomic file operations
- Metadata updates
- Scope detection (global vs project)
"""

import re
import sys
import os
import tempfile
import fcntl
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import date


class GovernanceFileError(Exception):
    """Base exception for governance file operations."""
    pass


class IDCollisionError(GovernanceFileError):
    """Raised when attempting to use an existing ID."""
    pass


class GovernanceFileNotFoundError(GovernanceFileError):
    """Raised when governance file doesn't exist."""
    pass


def atomic_write(file_path: Path, content: str) -> None:
    """
    Write file atomically to prevent corruption.

    Uses tempfile + os.replace() for atomic write operation.
    Safe for concurrent access and crash recovery.

    Args:
        file_path: Path to file
        content: Content to write

    Raises:
        GovernanceFileError: On write failure
    """
    dir_path = file_path.parent

    # Create temp file in same directory (required for atomic replace)
    fd, temp_path = tempfile.mkstemp(dir=str(dir_path), text=True)

    try:
        # Write to temp file
        os.write(fd, content.encode('utf-8'))
        os.close(fd)

        # Atomic replace (POSIX guarantees atomicity)
        os.replace(temp_path, str(file_path))
    except Exception as e:
        # Clean up temp file on failure
        try:
            os.unlink(temp_path)
        except:
            pass
        raise GovernanceFileError(f"Atomic write failed: {e}")


class GovernanceFileEditor:
    """Safe editor for governance tracking files."""

    # Default locations for global scope
    DEFAULT_PATHS = {
        "IDEAS": Path.home() / "dev/infrastructure/dev-env-docs/IDEAS-BACKLOG.md",
        "ISSUES": Path.home() / "dev/infrastructure/dev-env-docs/ISSUES-TRACKER.md",
        "DECISIONS": Path.home() / "dev/infrastructure/dev-env-docs/DECISIONS-LOG.md",
    }

    def __init__(self, scope: str = "global"):
        """
        Initialize editor.

        Args:
            scope: "global" for ~/dev/infrastructure/dev-env-docs/
                   or path to project directory for project-scoped
        """
        self.scope = scope
        self._detect_paths()

    def _detect_paths(self):
        """Detect file paths based on scope."""
        if self.scope == "global":
            self.paths = self.DEFAULT_PATHS.copy()
        else:
            # Project-scoped: use docs/ directory
            project_root = Path(self.scope)
            docs_dir = project_root / "docs"
            self.paths = {
                "IDEAS": docs_dir / "ideas.md",
                "ISSUES": docs_dir / "issues.md",
                "DECISIONS": docs_dir / "decisions.md",
            }

    def get_next_id(self, item_type: str) -> str:
        """
        Get next available ID for item type.

        Uses exclusive file lock to prevent race conditions in multi-agent environment.

        Args:
            item_type: "IDEAS", "ISSUES", or "DECISIONS"

        Returns:
            Next ID (e.g., "IDEA-414")

        Raises:
            FileNotFoundError: If governance file doesn't exist
        """
        file_path = self.paths[item_type]

        if not file_path.exists():
            raise GovernanceFileNotFoundError(f"File not found: {file_path}")

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                # Acquire exclusive lock (blocks until available)
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    content = f.read()
                finally:
                    # Release lock
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except PermissionError as e:
            raise GovernanceFileError(f"Permission denied reading {file_path}: {e}")
        except UnicodeDecodeError as e:
            raise GovernanceFileError(f"Encoding error in {file_path}: {e}")

        # Extract prefix (IDEA, ISSUE, DEC)
        prefix_map = {
            "IDEAS": "IDEA",
            "ISSUES": "ISSUE",
            "DECISIONS": "DEC",
        }
        prefix = prefix_map[item_type]

        # Find all existing IDs
        pattern = rf'{prefix}-(\d+)'
        matches = re.findall(pattern, content)

        if not matches:
            return f"{prefix}-001"

        highest = max(int(num) for num in matches)
        next_num = highest + 1

        return f"{prefix}-{next_num:03d}"

    def check_id_collision(self, item_type: str, item_id: str) -> bool:
        """
        Check if ID already exists.

        Args:
            item_type: "IDEAS", "ISSUES", or "DECISIONS"
            item_id: ID to check (e.g., "IDEA-414")

        Returns:
            True if ID exists, False otherwise
        """
        file_path = self.paths[item_type]

        if not file_path.exists():
            return False

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            # Use word boundary regex to avoid false positives (IDEA-1 shouldn't match IDEA-10)
            pattern = r'\b' + re.escape(item_id) + r'\b'
            return bool(re.search(pattern, content))
        except Exception as e:
            # Fail-safe: assume collision on error (safer)
            print(f"⚠️  Error checking ID collision: {e}", file=sys.stderr)
            return True

    def insert_idea(
        self,
        idea_id: str,
        title: str,
        category: str,
        priority: str,
        description: str,
        why_needed: str = "",
        blocker: str = "None",
        effort: str = "MEDIUM",
        related: List[str] = None,
        validation: List[str] = None,
        source: str = "User capture"
    ) -> None:
        """
        Insert new IDEA into IDEAS-BACKLOG.md.

        Args:
            idea_id: IDEA ID (e.g., "IDEA-414")
            title: Short title
            category: Category (e.g., "Automation")
            priority: HIGH/MEDIUM/LOW
            description: Full description
            why_needed: Justification
            blocker: Blocking item or "None"
            effort: LOW/MEDIUM/HIGH
            related: List of related item IDs
            validation: List of validation checklist items
            source: How this was captured

        Raises:
            IDCollisionError: If ID already exists
            GovernanceFileError: On file operation failure
        """
        if self.check_id_collision("IDEAS", idea_id):
            raise IDCollisionError(f"ID already exists: {idea_id}")

        file_path = self.paths["IDEAS"]

        # Build content
        related_str = ", ".join(related) if related else "None"
        validation_str = "\n".join(
            f"- [ ] {item}" for item in (validation or ["Implementation complete"])
        )

        today = date.today().strftime("%Y-%m-%d")

        new_idea = f"""
### {idea_id}: {title}

**Category:** {category}
**Priority:** {priority}
**Status:** Parking
**Added:** {today}
**Source:** {source}

**Description:** {description}

**Why Needed:** {why_needed or "See description"}

**Blocker:** {blocker}

**Effort:** {effort}

**Related:** {related_str}

**Validation:**
{validation_str}

---

"""

        # Read-modify-write with exclusive lock
        try:
            with open(file_path, 'r+', encoding='utf-8') as f:
                # Acquire exclusive lock for entire read-modify-write sequence
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    content = f.read()

                    # Insert after "## Active Ideas" header
                    if "## Active Ideas" not in content:
                        raise GovernanceFileError("Malformed file: '## Active Ideas' header not found")

                    content = content.replace(
                        "## Active Ideas\n",
                        f"## Active Ideas\n{new_idea}"
                    )

                    # Update metadata
                    content = self._update_ideas_metadata(content, idea_id)

                    # Write atomically (still using atomic write for crash safety)
                    # Note: lock is held during atomic write
                    atomic_write(file_path, content)
                finally:
                    # Release lock
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except FileNotFoundError:
            raise GovernanceFileNotFoundError(f"File not found: {file_path}")
        except PermissionError as e:
            raise GovernanceFileError(f"Permission denied: {e}")

    def insert_issue(
        self,
        issue_id: str,
        title: str,
        severity: str,
        category: str,
        description: str,
        impact: str,
        resolution: List[str],
        related: List[str] = None,
        source: str = "User capture"
    ) -> None:
        """
        Insert new ISSUE into ISSUES-TRACKER.md.

        Args:
            issue_id: ISSUE ID (e.g., "ISSUE-247")
            title: Short title
            severity: CRITICAL/HIGH/MEDIUM/LOW
            category: Category (e.g., "Bug")
            description: Full description
            impact: What fails if not fixed
            resolution: List of resolution steps
            related: List of related item IDs
            source: How this was captured

        Raises:
            IDCollisionError: If ID already exists
            GovernanceFileError: On file operation failure
        """
        if self.check_id_collision("ISSUES", issue_id):
            raise IDCollisionError(f"ID already exists: {issue_id}")

        file_path = self.paths["ISSUES"]

        # Build content
        related_str = ", ".join(related) if related else "None"
        resolution_str = "\n".join(
            f"{i+1}. {step}" for i, step in enumerate(resolution)
        )

        today = date.today().strftime("%Y-%m-%d")

        new_issue = f"""
### {issue_id} | {today} | OPEN | {severity} | {title}

**Severity:** {severity}
**Category:** {category}
**Discovered:** {today}
**Source:** {source}

**Description:** {description}

**Impact:** {impact}

**Resolution Required:**
{resolution_str}

**Related:** {related_str}

**Status:** OPEN

---

"""

        # Read-modify-write with exclusive lock
        try:
            with open(file_path, 'r+', encoding='utf-8') as f:
                # Acquire exclusive lock for entire read-modify-write sequence
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    content = f.read()

                    # Insert after "## Open Issues" header
                    if "## Open Issues" not in content:
                        raise GovernanceFileError("Malformed file: '## Open Issues' header not found")

                    content = content.replace(
                        "## Open Issues\n",
                        f"## Open Issues\n{new_issue}"
                    )

                    # Update metadata
                    content = self._update_issues_metadata(content)

                    # Write atomically (still using atomic write for crash safety)
                    # Note: lock is held during atomic write
                    atomic_write(file_path, content)
                finally:
                    # Release lock
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except FileNotFoundError:
            raise GovernanceFileNotFoundError(f"File not found: {file_path}")
        except PermissionError as e:
            raise GovernanceFileError(f"Permission denied: {e}")

    def _update_ideas_metadata(self, content: str, new_id: str) -> str:
        """Update IDEAS-BACKLOG.md metadata header."""
        # Extract current count
        match = re.search(r'\*\*Total Ideas:\*\* (\d+)', content)
        if match:
            current_total = int(match.group(1))
            new_total = current_total + 1

            # Extract next ID from new_id (e.g., IDEA-414 -> 415)
            id_num = int(new_id.split('-')[1])
            next_id = f"IDEA-{id_num + 1:03d}"

            # Update Last Updated
            today = date.today().strftime("%Y-%m-%d")

            # Replace metadata
            content = re.sub(
                r'\*\*Last Updated:\*\* \d{4}-\d{2}-\d{2}',
                f'**Last Updated:** {today}',
                content
            )

            content = re.sub(
                r'\*\*Total Ideas:\*\* \d+ \(next available: IDEA-\d+\)',
                f'**Total Ideas:** {new_total} (next available: {next_id})',
                content
            )

        return content

    def _update_issues_metadata(self, content: str) -> str:
        """Update ISSUES-TRACKER.md metadata header."""
        # Count open issues
        open_count = len(re.findall(r'\| OPEN \|', content))

        # Update Last Updated
        today = date.today().strftime("%Y-%m-%d")

        content = re.sub(
            r'\*\*Last Updated:\*\* \d{4}-\d{2}-\d{2}',
            f'**Last Updated:** {today}',
            content
        )

        content = re.sub(
            r'\*\*Summary:\*\* \d+ Open',
            f'**Summary:** {open_count} Open',
            content
        )

        return content


def main():
    """CLI interface for testing."""
    import argparse

    parser = argparse.ArgumentParser(description="Governance file editor")
    parser.add_argument('--get-next-id', choices=['IDEAS', 'ISSUES', 'DECISIONS'],
                       help='Get next available ID')
    parser.add_argument('--check-id', help='Check if ID exists (format: IDEA-414)')

    args = parser.parse_args()

    editor = GovernanceFileEditor()

    if args.get_next_id:
        try:
            next_id = editor.get_next_id(args.get_next_id)
            print(next_id)
        except GovernanceFileError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    elif args.check_id:
        # Determine type from ID prefix
        if args.check_id.startswith('IDEA'):
            item_type = 'IDEAS'
        elif args.check_id.startswith('ISSUE'):
            item_type = 'ISSUES'
        elif args.check_id.startswith('DEC'):
            item_type = 'DECISIONS'
        else:
            print("Error: Invalid ID format", file=sys.stderr)
            sys.exit(1)

        exists = editor.check_id_collision(item_type, args.check_id)
        print("EXISTS" if exists else "AVAILABLE")


if __name__ == '__main__':
    main()
