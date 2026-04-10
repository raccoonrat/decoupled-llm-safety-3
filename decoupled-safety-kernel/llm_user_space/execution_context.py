"""Python mirror of RFC §5.0 `ExecutionContext` (request-scoped)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ExecutionContext:
    trace_id: str
    policy_revision: int
    sanitized_prompt: bytes
