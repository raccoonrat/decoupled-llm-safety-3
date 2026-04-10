"""
DCBF evaluator on a latent *proxy* (Theorem 2.2): forward invariance via barrier margin.

Uses the **pointwise** barrier values h(x_t), h(x_{t+1}) — not expectation bounds
(Lemma 1.2: expectation does not imply hard safety).

    margin = h(x_{t+1}) - (1 - alpha) * h(x_t)

If margin < 0, set interrupt=True and map to MonitorFault (RFC §5.2, §9).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LatentStateProxy:
    """Read-only latent proxy; h is the evaluated barrier h(x) for this state (RFC §5.2)."""

    trace_id: str
    h: float


@dataclass
class DCBFReport:
    """Aligned with RFC §5.2 `DCBFReport` (margin matches RFC §7.5)."""

    h_t: float
    h_t1: float
    margin: float
    near_violation: bool
    interrupt: bool
    barrier_id: str | None = None


class MonitorFault(Exception):
    """DCBF barrier violated (margin < 0); corresponds to RFC `MonitorFault` / latent interrupt path."""

    def __init__(self, report: DCBFReport, message: str = "DCBF forward invariance violated") -> None:
        super().__init__(message)
        self.report = report


def check_forward_invariance(
    state_t: LatentStateProxy,
    state_t1: LatentStateProxy,
    alpha: float,
    *,
    near_margin_eps: float = 1e-6,
) -> DCBFReport:
    """
    Theorem 2.2: require h(x_{t+1}) >= (1 - alpha) * h(x_t), alpha in (0, 1].

    margin = h(x_{t+1}) - (1 - alpha) * h(x_t); interrupt iff margin < 0.
    """
    if not (0.0 < alpha <= 1.0):
        raise ValueError("alpha must satisfy 0 < alpha <= 1 (Theorem 2.2 / RFC §7.5)")

    h_t = state_t.h
    h_t1 = state_t1.h
    margin = h_t1 - (1.0 - alpha) * h_t
    interrupt = margin < 0.0
    near_violation = (not interrupt) and (margin < near_margin_eps)

    return DCBFReport(
        h_t=h_t,
        h_t1=h_t1,
        margin=margin,
        near_violation=near_violation,
        interrupt=interrupt,
        barrier_id=None,
    )


def check_forward_invariance_or_fault(
    state_t: LatentStateProxy,
    state_t1: LatentStateProxy,
    alpha: float,
    *,
    near_margin_eps: float = 1e-6,
) -> DCBFReport:
    """
    Same as `check_forward_invariance`, but raises `MonitorFault` when `interrupt` is True
    so Ring-0 can route to degradation FSM.
    """
    report = check_forward_invariance(
        state_t, state_t1, alpha, near_margin_eps=near_margin_eps
    )
    if report.interrupt:
        raise MonitorFault(report)
    return report
