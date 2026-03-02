#!/usr/bin/env python3
"""
Governance File Editor - Reusable module for governance item operations.

Provides safe, reliable file operations for IDEAS-BACKLOG.md, ISSUES-TRACKER.md,
DECISIONS-LOG.md, and LESSONS-LOG.md with:
- Automatic ID collision detection and avoidance
- Comprehensive error handling
- Atomic file operations
- Metadata updates
- Scope detection (global vs project)
"""

import re
import sys
import os
import time
import json
import tempfile
import fcntl
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import date, datetime, timezone


# Metrics infrastructure
METRICS_DIR = Path.home() / ".metrics/capture-analyze"
METRICS_DIR.mkdir(parents=True, exist_ok=True)


def log_metric(metric_type: str, data: Dict) -> None:
    """
    Log metric to JSONL file.

    Args:
        metric_type: "performance" or "accuracy"
        data: Metric data dict
    """
    metric_file = METRICS_DIR / f"{metric_type}.jsonl"

    # Add timestamp if not present
    if "timestamp" not in data:
        data["timestamp"] = datetime.now(timezone.utc).isoformat()

    try:
        with open(metric_file, 'a') as f:
            # Acquire lock for append
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                f.write(json.dumps(data) + '\n')
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except Exception:
        # Fail-safe: don't crash on metrics failure
        pass


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
        "LESSONS": Path.home() / "dev/infrastructure/dev-env-docs/LESSONS-LOG.md",
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
                "LESSONS": docs_dir / "lessons.md",
            }

    def get_next_id(self, item_type: str) -> str:
        """
        Get next available ID for item type.

        Uses exclusive file lock to prevent race conditions in multi-agent environment.

        Args:
            item_type: "IDEAS", "ISSUES", "DECISIONS", or "LESSONS"

        Returns:
            Next ID (e.g., "IDEA-414", "L-058")

        Raises:
            FileNotFoundError: If governance file doesn't exist
        """
        file_path = self.paths[item_type]

        if not file_path.exists():
            raise GovernanceFileNotFoundError(f"File not found: {file_path}")

        # Track lock wait time for contention metrics
        lock_wait_start = time.time()

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                # Acquire exclusive lock (blocks until available)
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                lock_wait_ms = int((time.time() - lock_wait_start) * 1000)

                try:
                    content = f.read()
                finally:
                    # Release lock
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)

                # Log lock contention if significant wait
                if lock_wait_ms > 10:  # >10ms indicates contention
                    log_metric("performance", {
                        "operation": "get_next_id",
                        "item_type": item_type,
                        "lock_wait_ms": lock_wait_ms,
                        "contention_detected": True
                    })
        except PermissionError as e:
            raise GovernanceFileError(f"Permission denied reading {file_path}: {e}")
        except UnicodeDecodeError as e:
            raise GovernanceFileError(f"Encoding error in {file_path}: {e}")

        # Extract prefix (IDEA, ISSUE, DEC, L)
        prefix_map = {
            "IDEAS": "IDEA",
            "ISSUES": "ISSUE",
            "DECISIONS": "DEC",
            "LESSONS": "L",
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
            item_type: "IDEAS", "ISSUES", "DECISIONS", or "LESSONS"
            item_id: ID to check (e.g., "IDEA-414", "L-058")

        Returns:
            True if ID exists, False otherwise
        """
        file_path = self.paths[item_type]

        if not file_path.exists():
            return False

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            # Only match ID as an entry header, not in body text (Related fields, etc.)
            # Header formats: "### IDEA-XXX: Title" or "### ISSUE-XXX | ..." or "### DEC-XXX | ..."
            pattern = r'^### ' + re.escape(item_id) + r'[: |]'
            return bool(re.search(pattern, content, re.MULTILINE))
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

    def insert_decision(
        self,
        decision_id: str,
        title: str,
        category: str,
        status: str,
        context: str,
        decision: str,
        rationale: str,
        related: List[str] = None,
        source: str = "User capture"
    ) -> None:
        """
        Insert new DECISION into DECISIONS-LOG.md.

        Args:
            decision_id: DECISION ID (e.g., "DEC-247")
            title: Short title
            category: Category (e.g., "ARCH", "CONFIG", "TOOL", "PROC", "MAINT")
            status: ACCEPTED/PROPOSED/SUPERSEDED/DEPRECATED/UNDER REVIEW
            context: Background and why this decision was needed
            decision: What was decided
            rationale: Why this decision was made
            related: List of related item IDs
            source: How this was captured

        Raises:
            IDCollisionError: If ID already exists
            GovernanceFileError: On file operation failure
        """
        if self.check_id_collision("DECISIONS", decision_id):
            raise IDCollisionError(f"ID already exists: {decision_id}")

        file_path = self.paths["DECISIONS"]

        # Build content
        related_str = ", ".join(related) if related else "None"
        today = date.today().strftime("%Y-%m-%d")

        new_decision = f"""
### {decision_id} | {today} | {category} | {status} | {title}

**Context:** {context}

**Decision:** {decision}

**Rationale:** {rationale}

**Status:** {status}

**Category:** {category}

**Related:** {related_str}

**Source:** {source}

---

"""

        # Read-modify-write with exclusive lock
        try:
            with open(file_path, 'r+', encoding='utf-8') as f:
                # Acquire exclusive lock for entire read-modify-write sequence
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    content = f.read()

                    # Insert after "## Active Decisions" header
                    if "## Active Decisions" not in content:
                        raise GovernanceFileError("Malformed file: '## Active Decisions' header not found")

                    content = content.replace(
                        "## Active Decisions\n",
                        f"## Active Decisions\n{new_decision}"
                    )

                    # Update metadata
                    content = self._update_decisions_metadata(content)

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

    def insert_lesson(
        self,
        lesson_id: str,
        title: str,
        context: str,
        principle: str = "",
        rule: str = "",
        applies_to: str = "",
        related: List[str] = None,
        source: str = "User capture"
    ) -> None:
        """
        Append new LESSON to LESSONS-LOG.md.

        Args:
            lesson_id: LESSON ID (e.g., "L-058")
            title: Short title
            context: Background — what happened that prompted this lesson
            principle: The underlying principle (use for observations/discoveries)
            rule: The actionable rule (use for behavioral mandates)
            applies_to: Scope string (e.g., "All sessions", "Evaluation tasks")
            related: List of related item IDs
            source: How this was captured

        Raises:
            IDCollisionError: If ID already exists
            GovernanceFileError: On file operation failure
        """
        if self.check_id_collision("LESSONS", lesson_id):
            raise IDCollisionError(f"ID already exists: {lesson_id}")

        file_path = self.paths["LESSONS"]

        related_str = ", ".join(related) if related else "None"
        today = date.today().strftime("%Y-%m-%d")

        # Build entry — include optional sections only when provided
        lines = [f"\n### {lesson_id}: {title}\n", f"\n**Date:** {today}\n"]
        lines.append(f"**Context:** {context}\n")
        if principle:
            lines.append(f"\n**The Principle:** {principle}\n")
        if rule:
            lines.append(f"\n**The Rule:** {rule}\n")
        if applies_to:
            lines.append(f"\n**Applies to:** {applies_to}\n")
        lines.append(f"\n**Related:** {related_str}\n")
        lines.append(f"\n**Source:** {source}\n")
        lines.append("\n---\n")

        new_lesson = "".join(lines)

        # Append to end of file with exclusive lock.
        # Unlike insert_idea/issue/decision (which find a section header and insert after it),
        # LESSONS-LOG.md has no section header — lessons accumulate sequentially. Append is correct.
        try:
            with open(file_path, 'a', encoding='utf-8') as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    f.write(new_lesson)
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except FileNotFoundError:
            raise GovernanceFileNotFoundError(f"File not found: {file_path}")
        except PermissionError as e:
            raise GovernanceFileError(f"Permission denied: {e}")

        # Update LESSONS-SUMMARY.yaml if it exists
        summary_path = file_path.parent / "LESSONS-SUMMARY.yaml"
        if summary_path.exists():
            self._update_lessons_summary(summary_path, lesson_id, title, today)

    def _update_lessons_summary(
        self, summary_path: Path, lesson_id: str, title: str, date_str: str
    ) -> None:
        """Append new lesson entry to LESSONS-SUMMARY.yaml."""
        try:
            with open(summary_path, 'r+', encoding='utf-8') as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    content = f.read()

                    # Update total_count
                    match = re.search(r'^total_count: (\d+)', content, re.MULTILINE)
                    if match:
                        new_count = int(match.group(1)) + 1
                        content = re.sub(
                            r'^total_count: \d+',
                            f'total_count: {new_count}',
                            content,
                            flags=re.MULTILINE
                        )

                    # Append new entry before end
                    entry = (
                        f"  {lesson_id}:\n"
                        f"    date: '{date_str}'\n"
                        f"    title: '{title}'\n"
                    )
                    # Insert after last lesson entry (before trailing newline)
                    content = content.rstrip('\n') + '\n' + entry

                    atomic_write(summary_path, content)
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except Exception:
            # Fail-safe: summary update failure doesn't break lesson insert
            pass

    def format_task_for_dart(
        self,
        title: str,
        description: str,
        priority: str = "MEDIUM",
        dartboard: str = None,
        assignee: str = None,
        related: List[str] = None,
        source: str = "User capture"
    ) -> Dict:
        """
        Format a task for creation via Dart MCP.

        NOTE: Tasks are NOT stored in local markdown files — Dart is the
        canonical task tracker for this ecosystem. This method returns a
        formatted dict suitable for use with the Dart MCP create_task() tool.

        Decision: Tasks → Dart MCP (evaluated in ISSUE-2084 item #3).
        Local markdown fallback was rejected: split tracking creates drift,
        and Dart provides priority, assignee, dartboard, status, and due date
        tracking that flat files cannot replicate without reimplementing a
        task management system.

        Args:
            title: Short task title
            description: Full task description
            priority: HIGH/MEDIUM/LOW (maps to Dart priority)
            dartboard: Target dartboard name (optional)
            assignee: Assignee name or email (optional)
            related: Related governance item IDs
            source: How this task was captured

        Returns:
            Dict with task fields ready for Dart MCP create_task()
            (calling agent should pass these fields to mcp__dart__create_task)
        """
        related_str = ", ".join(related) if related else ""
        today = date.today().strftime("%Y-%m-%d")

        description_body = description
        if related_str:
            description_body += f"\n\n**Related:** {related_str}"
        description_body += f"\n\n**Source:** {source}\n**Captured:** {today}"

        return {
            "dart_action": "create_task",
            "title": title,
            "description": description_body,
            "priority": priority,
            "dartboard": dartboard,
            "assignee": assignee,
            "related": related_str,
        }

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

    def _update_decisions_metadata(self, content: str) -> str:
        """Update DECISIONS-LOG.md metadata header."""
        # Count accepted decisions
        accepted_count = len(re.findall(r'\| ACCEPTED \|', content))

        # Update Last Updated
        today = date.today().strftime("%Y-%m-%d")

        content = re.sub(
            r'\*\*Last Updated:\*\* \d{4}-\d{2}-\d{2}',
            f'**Last Updated:** {today}',
            content
        )

        content = re.sub(
            r'\*\*Summary:\*\* \d+ Accepted',
            f'**Summary:** {accepted_count} Accepted',
            content
        )

        return content


def main():
    """CLI interface for testing."""
    import argparse

    parser = argparse.ArgumentParser(description="Governance file editor")
    parser.add_argument('--get-next-id', choices=['IDEAS', 'ISSUES', 'DECISIONS', 'LESSONS'],
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
        elif args.check_id.startswith('L-'):
            item_type = 'LESSONS'
        else:
            print("Error: Invalid ID format", file=sys.stderr)
            sys.exit(1)

        exists = editor.check_id_collision(item_type, args.check_id)
        print("EXISTS" if exists else "AVAILABLE")


if __name__ == '__main__':
    main()
