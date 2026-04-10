"""Legacy name — use `compositional_hypergraph_attack` for Theorem 3.1 benchmarks."""

from __future__ import annotations

from dataclasses import dataclass, field

from .compositional_hypergraph_attack import (
    CapabilityHypergraph,
    CompositionalAttackSet,
    CompositionalAttackScenario,
    ComposabilityGainResult,
    HyperEdge,
    closure_capabilities,
    composition_reaches_forbidden,
    default_attack_set,
    evaluate_scenario,
    individual_seeds_safe_in_isolation,
    theorem_3_1_canonical_attack,
)


@dataclass
class HypergraphAttackSpec:
    """Early scaffold — prefer `CompositionalAttackScenario` + `CapabilityHypergraph`."""

    step_ids: list[str] = field(default_factory=list)


__all__ = [
    "CapabilityHypergraph",
    "CompositionalAttackSet",
    "CompositionalAttackScenario",
    "ComposabilityGainResult",
    "HyperEdge",
    "HypergraphAttackSpec",
    "closure_capabilities",
    "composition_reaches_forbidden",
    "default_attack_set",
    "evaluate_scenario",
    "individual_seeds_safe_in_isolation",
    "theorem_3_1_canonical_attack",
]
