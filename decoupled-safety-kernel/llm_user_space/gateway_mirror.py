"""
Mirror of `gateway_filter.rs` rules for Python-side E2E (Ring-1 parity, no extra policy).

Critical marker `__CRITICAL_TEST__` matches Rust `scan_findings` test hook.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Severity(str, Enum):
    INFO = "info"
    WARN = "warn"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class Finding:
    rule_id: str
    start: int
    end: int
    severity: Severity


@dataclass
class SanitizedPrompt:
    canonical: bytes
    findings: list[Finding]


class GatewayHardReject(Exception):
    """Matches Rust `GatewayHardReject` — no downstream generation."""


def sanitize_input(raw: bytes) -> SanitizedPrompt:
    """Same contract as `GatewayFilter::sanitize_input` Ok branch; raises on Critical."""
    findings: list[Finding] = []
    if not raw:
        findings.append(
            Finding("gateway.empty_input", 0, 0, Severity.HIGH)
        )
    needle = b"__CRITICAL_TEST__"
    if needle in raw:
        i = raw.index(needle)
        findings.append(
            Finding(
                "gateway.critical_marker",
                i,
                i + len(needle),
                Severity.CRITICAL,
            )
        )
    if any(f.severity == Severity.CRITICAL for f in findings):
        raise GatewayHardReject(findings)
    return SanitizedPrompt(canonical=bytes(raw), findings=findings)
