"""
Tail and extreme violation testing (Lemma 1.2 — expected bounds do not imply hard safety).

Expectation-based alignment (e.g. RLHF average reward) can drive E[loss] down while
support-level bad events remain with positive probability; benchmarks probe that gap.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SupportLevelConfig:
    """Configuration for long-tail / extreme event probes."""

    min_samples: int = 1


def lemma_1_2_bernoulli(p_unsafe: float) -> tuple[float, float]:
    """
    Bernoulli X with P(X=1)=p_unsafe: E[X]=p_unsafe but hard safety requires P(X=1)=0.
    Returns (expectation, point_mass_on_unsafe).
    """
    return p_unsafe, p_unsafe


def support_level_exclusion_mass(p_unsafe: float) -> float:
    """
    Residual probability mass on the unsafe atom (Lemma 1.2). Hard safety needs this to be 0;
    expectation-only training can leave it positive even when E[loss] is small.
    """
    _e, mass = lemma_1_2_bernoulli(p_unsafe)
    return mass
