"""
DCBF monitoring facade: re-exports strict evaluator (Theorem 2.2).

Prefer importing from `dcbf_evaluator` for Ring-0 integration tests.
"""

from __future__ import annotations

from .dcbf_evaluator import (
    DCBFReport,
    LatentStateProxy,
    MonitorFault,
    check_forward_invariance,
    check_forward_invariance_or_fault,
)

__all__ = [
    "DCBFReport",
    "LatentStateProxy",
    "MonitorFault",
    "check_forward_invariance",
    "check_forward_invariance_or_fault",
    "dcbf_step_ok",
]


def dcbf_step_ok(h_t: float, h_next: float, alpha: float) -> bool:
    """Equivalent to margin >= 0 with margin = h_next - (1-alpha)*h_t."""
    st = LatentStateProxy(trace_id="", h=h_t)
    sn = LatentStateProxy(trace_id="", h=h_next)
    r = check_forward_invariance(st, sn, alpha)
    return not r.interrupt
