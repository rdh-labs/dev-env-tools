#!/usr/bin/env python3
"""
Research Trigger Matcher (IDEA-536, Pattern 18)

Analyzes error messages, tool output, and context to determine whether
external research should be conducted before internal investigation.

Implements the 7 automatic research triggers from Pattern 18:
1. Version/dependency mismatch
2. Third-party library in error message
3. Searchable error digest/code
4. Prior occurrence in memory (requires external check)
5. Error after dependency update (requires context)
6. Framework-specific behavior
7. Investigation stall (>3 steps, requires external context)

Usage:
    # Analyze error text
    python3 research_trigger_matcher.py "npm ERR! peer dep requires next <15.5.0"

    # JSON output for automation
    python3 research_trigger_matcher.py --json "Error: ECONNREFUSED"

    # Check from stdin (pipe error output)
    some_command 2>&1 | python3 research_trigger_matcher.py --stdin

    # With context flags
    python3 research_trigger_matcher.py --after-update --steps 5 "TypeError in @payloadcms/next"
"""

import re
import sys
import json
import argparse
from dataclasses import dataclass, field, asdict
from typing import List, Optional


# =============================================================================
# Trigger Definitions
# =============================================================================

@dataclass
class TriggerMatch:
    """A single matched research trigger."""
    trigger_id: int
    trigger_name: str
    confidence: float  # 0.0 - 1.0
    matched_patterns: List[str]
    research_actions: List[str]
    search_terms: List[str]


@dataclass
class AnalysisResult:
    """Result of analyzing error text for research triggers."""
    research_recommended: bool
    triggers_fired: List[TriggerMatch]
    confidence: float  # Overall confidence that research is needed
    summary: str
    raw_text: str


# --- Trigger 1: Version/Dependency Mismatch ---

VERSION_PATTERNS = [
    # npm peer dependency conflicts
    r'peer\s+dep(?:endency)?\s+requires?\s+',
    r'ERESOLVE\s+unable\s+to\s+resolve',
    r'npm\s+ERR!\s+peer\s+dep',
    r'Could\s+not\s+resolve\s+dependency',
    r'npm\s+warn\s+ERESOLVE',
    # Version range violations
    r'requires\s+[\w@/.-]+\s*[<>=!]+\s*[\d.]+',
    r'but\s+(?:version\s+)?[\d.]+\s+(?:was\s+)?installed',
    r'incompatible\s+(?:with\s+)?version',
    r'version\s+mismatch',
    r'invalid\s+peer\s+dep',
    # Python dependency conflicts
    r'pip.*(?:conflict|incompatible)',
    r'requires\s+[\w-]+\s*[<>=!]+\s*[\d.]+.*but\s+[\d.]+',
    r'(?:Could|Cannot)\s+install.*incompatible',
    # General version issues
    r'unsupported\s+(?:engine|version|platform)',
    r'requires\s+node\s+[<>=!]+\s*[\d.]+',
    r'minimum\s+(?:version|node|python)',
]

# --- Trigger 2: Third-Party Library Name ---

# Common npm/python package name patterns (scoped and unscoped)
LIBRARY_PATTERNS = [
    # npm scoped packages (@org/package)
    r'@[\w-]+/[\w.-]+',
    # Common framework-specific modules (must be 3+ chars to avoid noise)
    r'(?:from|in|at)\s+[\'"]?(?:node_modules/)?(@[\w-]+/[\w.-]+|[\w-]{3,}(?:/[\w.-]+))',
    # Python import errors
    r'ModuleNotFoundError:\s+No\s+module\s+named\s+[\'"]?([\w.-]+)',
    r'ImportError:\s+cannot\s+import\s+name',
    # Explicit library error prefixes
    r'(?:Error|Warning)\s+in\s+[@\w/.-]+',
]

# Well-known libraries and frameworks (presence = trigger 2 fires)
KNOWN_LIBRARIES = {
    # JavaScript/Node
    'next', 'react', 'react-dom', 'webpack', 'vite', 'esbuild', 'turbopack',
    'express', 'fastify', 'koa', 'nest', 'nuxt', 'svelte', 'vue', 'angular',
    'tailwindcss', 'postcss', 'typescript', 'eslint', 'prettier', 'jest',
    'vitest', 'playwright', 'cypress', 'storybook',
    # PayloadCMS specific
    'payload', 'payloadcms',
    # Python
    'django', 'flask', 'fastapi', 'sqlalchemy', 'pandas', 'numpy',
    'tensorflow', 'pytorch', 'transformers',
    # Databases
    'postgres', 'postgresql', 'mysql', 'mongodb', 'redis', 'prisma',
    'drizzle', 'typeorm', 'sequelize',
    # Cloud/Infra
    'docker', 'kubernetes', 'terraform', 'cloudflare', 'vercel', 'railway',
    'aws', 's3', 'lambda',
}

# Scoped packages that are known libraries
KNOWN_SCOPED_PACKAGES = {
    '@payloadcms', '@payloadcms/next', '@payloadcms/richtext-lexical',
    '@payloadcms/db-postgres', '@payloadcms/storage-s3',
    '@next', '@nextjs', '@vercel', '@vercel/next',
    '@types', '@testing-library', '@playwright',
    '@tailwindcss', '@emotion', '@mui',
    '@tanstack', '@trpc', '@prisma',
    '@aws-sdk', '@google-cloud', '@azure',
}

# --- Trigger 3: Searchable Error Digest/Code ---

ERROR_CODE_PATTERNS = [
    # Named error codes (Node.js style)
    r'\b(?:ERR_|E)[A-Z][A-Z_]{3,}\b',
    # Digest/hash references
    r'Digest:\s*\d+',
    r'digest:\s*[a-f0-9]{6,}',
    # HTTP status codes in error context
    r'(?:status|code|HTTP)\s*[:=]?\s*(?:4\d{2}|5\d{2})\b',
    # Exit codes
    r'(?:exit|error)\s+code\s+\d+',
    # Numbered error patterns
    r'(?:Error|Warning)\s*#?\d{3,}',
    r'\[(?:ERROR|WARN|FATAL)\]\s*\d+',
    # Python/JS exception names (searchable, excluding simple syntax errors)
    r'\b(?!Syntax|Reference|Type)(\w+Error):\s+',
    r'\b\w+Exception:\s+',
    # Webpack/bundler error codes
    r'Module\s+(?:not\s+found|build\s+failed)',
    r'BREAKING\s+CHANGE',
]

# --- Trigger 6: Framework-Specific Behavior ---

FRAMEWORK_PATTERNS = [
    # Next.js specific
    r'(?:RSC|React\s+Server\s+Component)',
    r'(?:SSR|SSG|ISR)\s+(?:error|fail)',
    r'getServerSideProps|getStaticProps|generateStaticParams',
    r'app\s+router|pages\s+router',
    r'middleware\.ts',
    r'next\.config',
    r'_app\.|_document\.',
    r'flight\s+data',
    # React specific
    r'hydration\s+(?:error|mismatch|failed)',
    r'use\s*(?:Client|Server)',
    r'Suspense\s+boundary',
    r'concurrent\s+(?:mode|features)',
    # Payload CMS specific
    r'payload\s+(?:config|collection|global)',
    r'importMap',
    r'RootPage|RootLayout',
    r'payload\.config\.ts',
    # Build tool specific
    r'webpack\s+\d+',
    r'(?:tree.?shaking|code.?splitting|chunk)',
    r'(?:hot\s+module|HMR)\s+(?:replacement|reload)',
    # Database/ORM specific
    r'migration\s+(?:failed|error|pending)',
    r'(?:query|schema|table)\s+(?:error|does not exist)',
    r'(?:UNIQUE|FOREIGN\s+KEY|constraint)\s+violation',
]


# =============================================================================
# Matcher Engine
# =============================================================================

def match_trigger_1(text: str) -> Optional[TriggerMatch]:
    """Trigger 1: Version/dependency mismatch."""
    matches = []
    for pattern in VERSION_PATTERNS:
        found = re.findall(pattern, text, re.IGNORECASE)
        if found:
            matches.append(pattern)

    if not matches:
        return None

    # Extract version numbers and package names for search terms
    versions = re.findall(r'[\d]+\.[\d]+(?:\.[\d]+)?', text)
    packages = re.findall(r'@?[\w-]+(?:/[\w.-]+)?(?=\s*[<>=@])', text)

    search_terms = []
    if packages and versions:
        search_terms.append(f"{packages[0]} version {versions[0]} compatibility")
    search_terms.append("npm peer dependency conflict resolution")

    return TriggerMatch(
        trigger_id=1,
        trigger_name="Version/dependency mismatch",
        confidence=min(0.5 + len(matches) * 0.15, 0.95),
        matched_patterns=matches[:3],
        research_actions=[
            "Search GitHub issues for the package + version conflict",
            "Check package changelog/release notes for breaking changes",
            "Search npm for peer dependency requirements",
        ],
        search_terms=search_terms,
    )


def match_trigger_2(text: str) -> Optional[TriggerMatch]:
    """Trigger 2: Third-party library in error message."""
    text_lower = text.lower()
    matches = []

    # Check for scoped packages
    scoped = re.findall(r'@[\w-]+/[\w.-]+', text)
    for pkg in scoped:
        prefix = pkg.split('/')[0]
        if prefix in KNOWN_SCOPED_PACKAGES or pkg in KNOWN_SCOPED_PACKAGES:
            matches.append(f"scoped: {pkg}")

    # Check for known library names
    words = set(re.findall(r'\b[\w.-]+\b', text_lower))
    for lib in KNOWN_LIBRARIES:
        if lib in words:
            matches.append(f"library: {lib}")

    # Check regex patterns for library references
    for pattern in LIBRARY_PATTERNS:
        found = re.findall(pattern, text, re.IGNORECASE)
        if found:
            matches.append(f"pattern: {found[0]}")

    if not matches:
        return None

    # Build search terms from matched libraries
    lib_names = []
    for m in matches:
        name = m.split(': ', 1)[1] if ': ' in m else m
        lib_names.append(name)

    search_terms = [f"{lib_names[0]} error" if lib_names else "library error"]
    if len(lib_names) > 1:
        search_terms.append(f"{lib_names[0]} {lib_names[1]} compatibility")

    return TriggerMatch(
        trigger_id=2,
        trigger_name="Third-party library in error",
        confidence=min(0.4 + len(matches) * 0.15, 0.9),
        matched_patterns=[m for m in matches[:5]],
        research_actions=[
            f"Search GitHub issues: {lib_names[0]}",
            f"Check {lib_names[0]} documentation for this error",
            "Search Stack Overflow for the error message",
        ],
        search_terms=search_terms,
    )


def match_trigger_3(text: str) -> Optional[TriggerMatch]:
    """Trigger 3: Searchable error digest/code."""
    matches = []
    codes = []

    for pattern in ERROR_CODE_PATTERNS:
        found = re.findall(pattern, text)
        if found:
            matches.append(pattern)
            codes.extend(found[:2])

    if not matches:
        return None

    search_terms = [f'"{code}"' for code in codes[:3]]

    return TriggerMatch(
        trigger_id=3,
        trigger_name="Searchable error code/digest",
        confidence=min(0.5 + len(codes) * 0.1, 0.85),
        matched_patterns=codes[:5],
        research_actions=[
            f"Search for error code: {codes[0]}" if codes else "Search for the error code",
            "Check official documentation for this error code",
            "Search GitHub issues with the exact error code",
        ],
        search_terms=search_terms,
    )


def match_trigger_6(text: str) -> Optional[TriggerMatch]:
    """Trigger 6: Framework-specific behavior."""
    matches = []

    for pattern in FRAMEWORK_PATTERNS:
        found = re.findall(pattern, text, re.IGNORECASE)
        if found:
            matches.append(f"{found[0]}")

    if not matches:
        return None

    # Identify which framework
    framework = "unknown framework"
    text_lower = text.lower()
    if any(kw in text_lower for kw in ['next', 'rsc', 'ssr', 'ssg', 'app router']):
        framework = "Next.js"
    elif any(kw in text_lower for kw in ['payload', 'rootpage', 'importmap']):
        framework = "Payload CMS"
    elif any(kw in text_lower for kw in ['react', 'hydration', 'suspense']):
        framework = "React"
    elif any(kw in text_lower for kw in ['webpack', 'hmr', 'chunk']):
        framework = "Webpack"
    elif any(kw in text_lower for kw in ['migration', 'query', 'schema', 'constraint']):
        framework = "Database/ORM"

    search_terms = [f"{framework} {matches[0]}"]

    return TriggerMatch(
        trigger_id=6,
        trigger_name=f"Framework-specific behavior ({framework})",
        confidence=min(0.5 + len(matches) * 0.15, 0.9),
        matched_patterns=matches[:5],
        research_actions=[
            f"Check {framework} documentation for this behavior",
            f"Search {framework} GitHub issues",
            f"Search for {framework} known issues with this pattern",
        ],
        search_terms=search_terms,
    )


# =============================================================================
# Context-Based Triggers (require flags, not pattern matching)
# =============================================================================

def trigger_4_memory_check() -> TriggerMatch:
    """Trigger 4: Prior occurrence in memory. Caller must check engram."""
    return TriggerMatch(
        trigger_id=4,
        trigger_name="Prior occurrence in memory",
        confidence=0.8,
        matched_patterns=["--memory-hit flag provided"],
        research_actions=[
            "Review prior occurrence details from engram/memory",
            "Check if the same fix applies",
            "Determine if this is a recurring pattern (escalate if >=2 occurrences)",
        ],
        search_terms=["(use terms from prior occurrence)"],
    )


def trigger_5_after_update() -> TriggerMatch:
    """Trigger 5: Error after dependency update. Caller provides context."""
    return TriggerMatch(
        trigger_id=5,
        trigger_name="Error after dependency update",
        confidence=0.85,
        matched_patterns=["--after-update flag provided"],
        research_actions=[
            "Check changelog/release notes for the updated package",
            "Search for breaking changes in the new version",
            "Search GitHub issues for migration guides",
        ],
        search_terms=["(package name) changelog breaking changes"],
    )


def trigger_7_stall(steps: int) -> TriggerMatch:
    """Trigger 7: Investigation stall (>3 steps without progress)."""
    return TriggerMatch(
        trigger_id=7,
        trigger_name=f"Investigation stall ({steps} steps without progress)",
        confidence=min(0.4 + steps * 0.1, 0.9),
        matched_patterns=[f"--steps {steps} (>{3} threshold)"],
        research_actions=[
            "STOP internal investigation",
            "Search externally for the error message or symptoms",
            "Consider asking the user for additional context",
            "Review approach — may be investigating the wrong thing",
        ],
        search_terms=["(use the original error message for search)"],
    )


# =============================================================================
# Main Analysis Function
# =============================================================================

def analyze(
    text: str,
    after_update: bool = False,
    memory_hit: bool = False,
    steps: int = 0,
) -> AnalysisResult:
    """
    Analyze error text for research triggers.

    Args:
        text: Error message or tool output to analyze
        after_update: Whether error occurred after a dependency update
        memory_hit: Whether engram/memory search found prior occurrence
        steps: Number of investigation steps taken so far
    """
    triggers: List[TriggerMatch] = []

    # Pattern-based triggers (1, 2, 3, 6)
    for matcher in [match_trigger_1, match_trigger_2, match_trigger_3, match_trigger_6]:
        result = matcher(text)
        if result:
            triggers.append(result)

    # Context-based triggers (4, 5, 7)
    if memory_hit:
        triggers.append(trigger_4_memory_check())
    if after_update:
        triggers.append(trigger_5_after_update())
    if steps > 3:
        triggers.append(trigger_7_stall(steps))

    # Calculate overall confidence
    if not triggers:
        overall_confidence = 0.0
        research_recommended = False
        summary = "No research triggers detected. Internal investigation is appropriate."
    else:
        # Combine confidence: 1 - product of (1 - individual confidences)
        overall_confidence = 1.0
        for t in triggers:
            overall_confidence *= (1.0 - t.confidence)
        overall_confidence = 1.0 - overall_confidence
        research_recommended = overall_confidence >= 0.4  # Low bar — research is cheap

        trigger_names = [t.trigger_name for t in triggers]
        summary = (
            f"Research {'REQUIRED' if overall_confidence >= 0.7 else 'RECOMMENDED'}: "
            f"{len(triggers)} trigger(s) fired — {', '.join(trigger_names)}. "
            f"Overall confidence: {overall_confidence:.0%}."
        )

    return AnalysisResult(
        research_recommended=research_recommended,
        triggers_fired=triggers,
        confidence=overall_confidence,
        summary=summary,
        raw_text=text[:200],
    )


# =============================================================================
# Presentation
# =============================================================================

def present(result: AnalysisResult) -> str:
    """Format analysis result for human-readable output."""
    lines = []

    if not result.research_recommended:
        lines.append("✅ No research triggers detected.")
        lines.append("   Internal investigation is appropriate for this error.")
        return '\n'.join(lines)

    icon = "🔴" if result.confidence >= 0.7 else "🟡"
    label = "REQUIRED" if result.confidence >= 0.7 else "RECOMMENDED"
    lines.append(f"{icon} External research {label} (confidence: {result.confidence:.0%})")
    lines.append("")

    for t in result.triggers_fired:
        lines.append(f"  Trigger {t.trigger_id}: {t.trigger_name} ({t.confidence:.0%})")
        if t.matched_patterns:
            patterns_str = ', '.join(str(p) for p in t.matched_patterns[:3])
            lines.append(f"    Matched: {patterns_str}")
        lines.append(f"    Actions:")
        for action in t.research_actions:
            lines.append(f"      → {action}")
        if t.search_terms and t.search_terms[0] != "(use terms from prior occurrence)":
            lines.append(f"    Search: {' | '.join(t.search_terms[:2])}")
        lines.append("")

    return '\n'.join(lines)


def to_json(result: AnalysisResult) -> str:
    """Format analysis result as JSON."""
    data = {
        'research_recommended': result.research_recommended,
        'confidence': round(result.confidence, 3),
        'summary': result.summary,
        'triggers': [
            {
                'id': t.trigger_id,
                'name': t.trigger_name,
                'confidence': round(t.confidence, 3),
                'matched_patterns': t.matched_patterns,
                'research_actions': t.research_actions,
                'search_terms': t.search_terms,
            }
            for t in result.triggers_fired
        ],
    }
    return json.dumps(data, indent=2)


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Research Trigger Matcher (IDEA-536, Pattern 18)",
        epilog="Analyzes error text to determine if external research should precede investigation.",
    )
    parser.add_argument(
        'text', nargs='?', default=None,
        help='Error message or text to analyze',
    )
    parser.add_argument(
        '--stdin', action='store_true',
        help='Read error text from stdin',
    )
    parser.add_argument(
        '--json', action='store_true',
        help='Output as JSON',
    )
    parser.add_argument(
        '--after-update', action='store_true',
        help='Error occurred after a dependency update (trigger 5)',
    )
    parser.add_argument(
        '--memory-hit', action='store_true',
        help='Prior occurrence found in engram/memory (trigger 4)',
    )
    parser.add_argument(
        '--steps', type=int, default=0,
        help='Number of investigation steps taken (trigger 7 if >3)',
    )

    args = parser.parse_args()

    if args.stdin:
        text = sys.stdin.read()
    elif args.text:
        text = args.text
    else:
        parser.print_help()
        sys.exit(1)

    if not text.strip():
        print("No text provided.")
        sys.exit(1)

    result = analyze(
        text=text,
        after_update=args.after_update,
        memory_hit=args.memory_hit,
        steps=args.steps,
    )

    if args.json:
        print(to_json(result))
    else:
        print(present(result))

    # Exit code: 0 if no research needed, 1 if research recommended
    sys.exit(0 if not result.research_recommended else 1)


if __name__ == '__main__':
    main()
