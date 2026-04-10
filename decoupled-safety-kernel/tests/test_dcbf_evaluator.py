"""DCBF margin (Theorem 2.2) and MonitorFault mapping — unittest (no pytest required)."""

from __future__ import annotations

import os
import sys
import unittest

# Repo layout: decoupled-safety-kernel/{src_observability,tests}
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src_observability.dcbf_evaluator import (
    LatentStateProxy,
    MonitorFault,
    check_forward_invariance,
    check_forward_invariance_or_fault,
)


class TestDCBFMargin(unittest.TestCase):
    def test_margin_formula(self) -> None:
        alpha = 0.1
        h_t, h_t1 = 1.0, 0.5
        expected = h_t1 - (1.0 - alpha) * h_t
        st = LatentStateProxy(trace_id="t1", h=h_t)
        s1 = LatentStateProxy(trace_id="t1", h=h_t1)
        r = check_forward_invariance(st, s1, alpha)
        self.assertAlmostEqual(r.margin, expected)
        self.assertTrue(r.interrupt)

    def test_satisfied_step_no_interrupt(self) -> None:
        alpha = 0.2
        st = LatentStateProxy(trace_id="t", h=1.0)
        s1 = LatentStateProxy(trace_id="t", h=1.0)
        r = check_forward_invariance(st, s1, alpha)
        self.assertGreaterEqual(r.margin, 0.0)
        self.assertFalse(r.interrupt)

    def test_monitor_fault_on_violation(self) -> None:
        st = LatentStateProxy(trace_id="t", h=2.0)
        s1 = LatentStateProxy(trace_id="t", h=0.0)
        with self.assertRaises(MonitorFault) as ctx:
            check_forward_invariance_or_fault(st, s1, 0.5)
        self.assertTrue(ctx.exception.report.interrupt)

    def test_alpha_bounds(self) -> None:
        st = LatentStateProxy(trace_id="t", h=1.0)
        s1 = LatentStateProxy(trace_id="t", h=1.0)
        with self.assertRaises(ValueError):
            check_forward_invariance(st, s1, 0.0)
        with self.assertRaises(ValueError):
            check_forward_invariance(st, s1, 1.5)


if __name__ == "__main__":
    unittest.main()
