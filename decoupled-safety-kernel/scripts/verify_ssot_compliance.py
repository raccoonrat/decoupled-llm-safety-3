#!/usr/bin/env python3
"""
Governance linter: scan comments and markdown (excluding SSOT docs) for phrases that
contradict Decoupled Safety Kernel theory + RFC_v0.2_r2 (fail-safe, audit, no implicit global safety).

Usage:
  python3 decoupled-safety-kernel/scripts/verify_ssot_compliance.py
  python3 decoupled-safety-kernel/scripts/verify_ssot_compliance.py --root /path/to/repo

Exit code: 0 = clean, 1 = violations found.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Paths relative to repo root; SSOT documents are excluded from *pattern* rules (they must define terms).
KERNEL_PREFIX = Path("decoupled-safety-kernel")
ALLOW_PRAGMA = re.compile(r"ssot-lint:\s*allow\b", re.IGNORECASE)

# Lines containing any of these substrings skip rule matching (quotes SSOT / negation).
ALLOW_CONTEXT_MARKERS = (
    "MUST NOT",
    "must not",
    "不得",
    "禁止",
    "SSOT",
    "RFC ",
    "Theorem ",
    "Corollary ",
    "Lemma ",
    "contradict",
    "undecidable",
    "non-composition",
    "non-compositional",
)

# (rule_id, regex, human-readable hint)
RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "SSOT-001",
        re.compile(
            r"(?i)\bimplicit\s+safety\s+is\s+(?:guaranteed|sufficient|enough|complete|decidable)\b"
        ),
        "Global/implicit safety is not a decidable guarantee in the base model (Theorem 1); do not claim otherwise.",
    ),
    (
        "SSOT-002",
        re.compile(r"(?i)\bimplicit\s+safety\s+guarantees\b"),
        "Avoid implying implicit safety 'guarantees'; prefer decoupled / external verification language.",
    ),
    (
        "SSOT-003",
        re.compile(
            r"(?i)\b(?:globally\s+)?verify\s+(?:that\s+)?(?:the\s+)?(?:base\s+)?(?:model|llm)\s+is\s+(?:always\s+)?safe\b"
        ),
        "Non-trivial global semantic safety is undecidable; cite decoupled instance checks instead.",
    ),
    (
        "SSOT-004",
        re.compile(r"(?i)\b(?:audit|evidence)\s+is\s+optional\b|\bno\s+audit\s+required\b|\bskip\s+audit\b"),
        "RFC requires an append-only evidence chain for token steps unless explicitly exempted and audited.",
    ),
    (
        "SSOT-005",
        re.compile(r"(?i)\bsilent(?:ly)?\s+approv\w*|\b静默放行\b"),
        "Invariant I6 forbids visible output before required audit commit (RFC Section 8).",
    ),
    (
        "SSOT-006",
        re.compile(r"(?i)\btrust\s+(?:the\s+)?(?:base\s+)?(?:model|llm)\s+(?:for\s+)?(?:safety|security)\b"),
        "Kernel MUST NOT treat base model as trusted for safety (RFC threat model).",
    ),
    (
        "SSOT-007",
        re.compile(r"(?i)\bdefine\s+implicit\s+safety\s+as\b"),
        "Do not normatively redefine 'implicit safety' outside SSOT docs; reference Step1 or RFC instead.",
    ),
)


def _repo_root(start: Path) -> Path:
    p = start.resolve()
    for _ in range(20):
        if (p / ".git").is_dir():
            return p
        if p.parent == p:
            break
        p = p.parent
    return start.resolve()


def _should_scan_path(path: Path, kernel_root: Path) -> bool:
    try:
        rel = path.relative_to(kernel_root)
    except ValueError:
        return False
    parts = rel.parts
    if parts and parts[0] == "target":
        return False
    if len(parts) >= 2 and parts[0] == "docs" and parts[1] in ("theory", "rfc"):
        return False
    return path.suffix in {".rs", ".py", ".md"}


def _strip_block_noise(line: str, suffix: str) -> str | None:
    """Return comment/markdown fragment to scan, or None to skip the line."""
    if suffix == ".md":
        return line
    if suffix == ".py":
        st = line.lstrip()
        if st.startswith("#"):
            return st[1:]
        return None
    if suffix == ".rs":
        st = line.lstrip()
        if st.startswith("///"):
            return st[3:]
        if st.startswith("//!"):
            return st[3:]
        if st.startswith("//"):
            return st[2:]
        return None
    return None


def _allowed_context(line: str) -> bool:
    if ALLOW_PRAGMA.search(line):
        return True
    low = line
    return any(m in low for m in ALLOW_CONTEXT_MARKERS)


def scan_file(path: Path) -> list[tuple[int, str, str]]:
    violations: list[tuple[int, str, str]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return violations
    for i, line in enumerate(text.splitlines(), start=1):
        if _allowed_context(line):
            continue
        frag = _strip_block_noise(line, path.suffix)
        if frag is None:
            continue
        if _allowed_context(frag):
            continue
        for rule_id, rx, hint in RULES:
            if rx.search(frag):
                violations.append((i, rule_id, hint))
                break
    return violations


def main() -> int:
    ap = argparse.ArgumentParser(description="SSOT governance linter for decoupled-safety-kernel.")
    ap.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Repository root (default: search upward for .git)",
    )
    args = ap.parse_args()
    root = _repo_root(args.root or Path.cwd())
    kernel_root = root / KERNEL_PREFIX
    if not kernel_root.is_dir():
        print(f"error: missing {kernel_root}", file=sys.stderr)
        return 1

    bad: list[tuple[Path, int, str, str]] = []
    for path in sorted(kernel_root.rglob("*")):
        if not path.is_file():
            continue
        if not _should_scan_path(path, kernel_root):
            continue
        for line_no, rule_id, hint in scan_file(path):
            bad.append((path, line_no, rule_id, hint))

    if bad:
        print("SSOT compliance violations:", file=sys.stderr)
        for path, line_no, rule_id, hint in bad:
            rel = path.relative_to(root)
            print(f"  {rel}:{line_no}: [{rule_id}] {hint}", file=sys.stderr)
        print(
            "\nFix the comment/text, or add an inline exception on the same line:  ssot-lint: allow SSOT-xxx",
            file=sys.stderr,
        )
        return 1
    print("verify_ssot_compliance: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
