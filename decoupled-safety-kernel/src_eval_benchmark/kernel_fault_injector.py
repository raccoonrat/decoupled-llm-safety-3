"""
RFC §12.3 fault injection harness + golden-trace expectations (Fail-safe / degrade paths).

Maps injected faults to mandated degradation semantics aligned with Ring-0 stubs:
- Audit write failure → refuse / visibility blocked (I6 + §11.2)
- Verifier timeout → Deny / ensemble fault path
- EmptySafeCandidateSet → PageFault / template path (§9.1)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable


class FaultKind(str, Enum):
    AUDIT_WRITE_FAILURE = "audit_write_failure"
    VERIFIER_TIMEOUT = "verifier_timeout"
    EMPTY_SAFE_CANDIDATE_SET = "empty_safe_candidate_set"


class ExpectedDegradePath(str, Enum):
    """RFC-aligned labels for golden-trace comparison."""

    REFUSE_FAILSAFE = "Refuse"  # deny / audit failure
    EMIT_SAFE_TEMPLATE = "EmitSafeTemplate"  # PageFault / empty set
    ENSEMBLE_DENY = "EnsembleDeny"  # verifier timeout → deny


@dataclass(frozen=True)
class GoldenTrace:
    """Expected outcome for a named fault injection (RFC §12.2.1 golden traces)."""

    trace_id: str
    fault: FaultKind
    expected_path: ExpectedDegradePath
    expect_i6_blocked: bool


# RFC §12.3 mandatory injections — expected fail-safe defaults.
GOLDEN_TRACES: tuple[GoldenTrace, ...] = (
    GoldenTrace(
        trace_id="golden-audit-fail",
        fault=FaultKind.AUDIT_WRITE_FAILURE,
        expected_path=ExpectedDegradePath.REFUSE_FAILSAFE,
        expect_i6_blocked=True,
    ),
    GoldenTrace(
        trace_id="golden-verifier-timeout",
        fault=FaultKind.VERIFIER_TIMEOUT,
        expected_path=ExpectedDegradePath.ENSEMBLE_DENY,
        expect_i6_blocked=False,
    ),
    GoldenTrace(
        trace_id="golden-empty-candidate",
        fault=FaultKind.EMPTY_SAFE_CANDIDATE_SET,
        expected_path=ExpectedDegradePath.EMIT_SAFE_TEMPLATE,
        expect_i6_blocked=False,
    ),
)


@dataclass
class FaultInjectionOutcome:
    fault: FaultKind
    degrade_path: str
    user_visible_released: bool
    matches_golden: bool


class KernelFaultInjector:
    """
    Minimal simulation of Ring-0 responses (mirrors `evidence_chain_audit` + `graceful_degradation_fsm`).
    Replace with FFI / gRPC to real kernel in integration tests.
    """

    def __init__(self) -> None:
        self._audit_ok: bool = True
        self._verifier_times_out: bool = False
        self._empty_candidates: bool = False

    def reset(self) -> None:
        self._audit_ok = True
        self._verifier_times_out = False
        self._empty_candidates = False

    def inject_audit_write_failure(self) -> None:
        self._audit_ok = False

    def inject_verifier_timeout(self) -> None:
        self._verifier_times_out = True

    def inject_empty_safe_candidate_set(self) -> None:
        self._empty_candidates = True

    def run_injected_fault(self, fault: FaultKind) -> FaultInjectionOutcome:
        self.reset()
        if fault is FaultKind.AUDIT_WRITE_FAILURE:
            self.inject_audit_write_failure()
        elif fault is FaultKind.VERIFIER_TIMEOUT:
            self.inject_verifier_timeout()
        elif fault is FaultKind.EMPTY_SAFE_CANDIDATE_SET:
            self.inject_empty_safe_candidate_set()

        path, visible = self._route()
        golden = _golden_for(fault)
        matches = golden is not None and path == golden.expected_path.value
        return FaultInjectionOutcome(
            fault=fault,
            degrade_path=path,
            user_visible_released=visible,
            matches_golden=matches,
        )

    def _route(self) -> tuple[str, bool]:
        # I6: no user-visible release if audit did not persist.
        if not self._audit_ok:
            return ExpectedDegradePath.REFUSE_FAILSAFE.value, False
        if self._verifier_times_out:
            return ExpectedDegradePath.ENSEMBLE_DENY.value, False
        if self._empty_candidates:
            return ExpectedDegradePath.EMIT_SAFE_TEMPLATE.value, True
        return ExpectedDegradePath.REFUSE_FAILSAFE.value, False


def _golden_for(fault: FaultKind) -> GoldenTrace | None:
    for g in GOLDEN_TRACES:
        if g.fault == fault:
            return g
    return None


def assert_all_golden_traces_pass(
    injector: KernelFaultInjector | None = None,
) -> list[FaultInjectionOutcome]:
    """Test helper: every RFC §12.3 fault maps to golden degrade path."""
    inj = injector or KernelFaultInjector()
    out: list[FaultInjectionOutcome] = []
    for g in GOLDEN_TRACES:
        r = inj.run_injected_fault(g.fault)
        assert r.matches_golden, (
            f"golden mismatch for {g.trace_id}: got {r.degrade_path}, "
            f"expected {g.expected_path.value}"
        )
        if g.expect_i6_blocked:
            assert not r.user_visible_released
        out.append(r)
    return out
