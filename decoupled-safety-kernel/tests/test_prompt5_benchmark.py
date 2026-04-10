"""Prompt 5: composability gain, Lemma 1.2 tail mass, RFC §12.3 fault golden traces."""

from __future__ import annotations

import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src_eval_benchmark.compositional_hypergraph_attack import (
    CapabilityHypergraph,
    CompositionalAttackSet,
    CompositionalAttackScenario,
    HyperEdge,
    composition_reaches_forbidden,
    default_attack_set,
    evaluate_scenario,
    individual_seeds_safe_in_isolation,
    theorem_3_1_canonical_attack,
)
from src_eval_benchmark.kernel_fault_injector import (
    ExpectedDegradePath,
    FaultKind,
    KernelFaultInjector,
    assert_all_golden_traces_pass,
)
from src_eval_benchmark.support_level_tester import (
    lemma_1_2_bernoulli,
    support_level_exclusion_mass,
)


class TestComposabilityGain(unittest.TestCase):
    def test_theorem_3_1_canonical(self) -> None:
        s = theorem_3_1_canonical_attack()
        self.assertTrue(individual_seeds_safe_in_isolation(s.seeds, s.graph))
        self.assertTrue(composition_reaches_forbidden(s.seeds, s.graph))

    def test_composability_gain_measured(self) -> None:
        atk = default_attack_set()
        _results, mean_gain = atk.measure_composability_gain()
        self.assertGreater(len(_results), 0)
        self.assertEqual(mean_gain, 1.0)

    def test_broken_kernel_zero_gain(self) -> None:
        s = theorem_3_1_canonical_attack()
        r = evaluate_scenario(s, kernel_blocks_fn=lambda _a, _b: False)
        self.assertEqual(r.composability_gain, 0.0)


class TestLemma12Tail(unittest.TestCase):
    def test_expectation_vs_support(self) -> None:
        e, mass = lemma_1_2_bernoulli(1e-9)
        self.assertAlmostEqual(e, 1e-9)
        self.assertAlmostEqual(support_level_exclusion_mass(1e-9), 1e-9)


class TestFaultInjectionGolden(unittest.TestCase):
    def test_rfc_12_3_all_match(self) -> None:
        assert_all_golden_traces_pass()

    def test_each_fault_path(self) -> None:
        inj = KernelFaultInjector()
        r = inj.run_injected_fault(FaultKind.AUDIT_WRITE_FAILURE)
        self.assertEqual(r.degrade_path, ExpectedDegradePath.REFUSE_FAILSAFE.value)
        self.assertFalse(r.user_visible_released)

        r2 = inj.run_injected_fault(FaultKind.VERIFIER_TIMEOUT)
        self.assertEqual(r2.degrade_path, ExpectedDegradePath.ENSEMBLE_DENY.value)

        r3 = inj.run_injected_fault(FaultKind.EMPTY_SAFE_CANDIDATE_SET)
        self.assertEqual(r3.degrade_path, ExpectedDegradePath.EMIT_SAFE_TEMPLATE.value)


if __name__ == "__main__":
    unittest.main()
