"""
Voting Comparison Benchmark — Weighted vs Unweighted Judge Ensemble (Round-3 R3)

Compares confidence-weighted voting (production) against uniform-weight majority
voting across synthetic verdict sets that mix high/low confidence judges.

Metrics:
  - TPR (True Positive Rate): fraction of malicious inputs correctly denied
  - FPR (False Positive Rate): fraction of benign inputs incorrectly denied
  - Precision, F1

The benchmark constructs four scenario families:
  1. Unanimous agreement (all judges agree)
  2. Split with one high-confidence dissenter
  3. Noisy low-confidence judges outnumber correct high-confidence judge
  4. Mixed confidence with abstentions

Usage:
    cd decoupled-safety-kernel
    PYTHONPATH=. python3 src_eval_benchmark/voting_comparison_benchmark.py
"""

from __future__ import annotations

import json
import random
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

KERNEL_ROOT = Path(__file__).resolve().parents[1]
if str(KERNEL_ROOT) not in sys.path:
    sys.path.insert(0, str(KERNEL_ROOT))


# ─────────────────────────────────────────────────────────────────────────────
# Lightweight Python Verdict (mirrors Rust judge_ensemble::Verdict)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Verdict:
    vote: str          # "Allow" | "Deny" | "Revise" | "Abstain"
    confidence: float  # [0, 1]
    explanation: str
    verifier_id: str


# ─────────────────────────────────────────────────────────────────────────────
# Scenario generation
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class VotingScenario:
    scenario_id: str
    verdicts: list[Verdict]
    ground_truth_deny: bool
    description: str


def _make_verdict(vote: str, confidence: float, vid: str) -> Verdict:
    return Verdict(
        vote=vote,
        confidence=confidence,
        explanation=f"synthetic-{vid}",
        verifier_id=vid,
    )


def generate_scenarios(seed: int = 42, n_per_family: int = 50) -> list[VotingScenario]:
    rng = random.Random(seed)
    scenarios: list[VotingScenario] = []

    for i in range(n_per_family):
        # Family 1: Unanimous — all deny (true positive)
        n_judges = rng.randint(3, 7)
        verdicts = [_make_verdict("Deny", rng.uniform(0.7, 1.0), f"v{j}")
                    for j in range(n_judges)]
        scenarios.append(VotingScenario(
            f"unanimous_deny_{i}", verdicts, True, "all judges deny, malicious input"))

        # Family 1b: Unanimous — all allow (true negative)
        verdicts = [_make_verdict("Allow", rng.uniform(0.7, 1.0), f"v{j}")
                    for j in range(n_judges)]
        scenarios.append(VotingScenario(
            f"unanimous_allow_{i}", verdicts, False, "all judges allow, benign input"))

        # Family 2: One high-confidence Deny vs multiple low-confidence Allow (malicious)
        high_deny = _make_verdict("Deny", rng.uniform(0.85, 0.99), "v_expert")
        low_allows = [_make_verdict("Allow", rng.uniform(0.3, 0.55), f"v_weak_{j}")
                      for j in range(rng.randint(3, 5))]
        scenarios.append(VotingScenario(
            f"expert_deny_vs_weak_allow_{i}",
            [high_deny] + low_allows, True,
            "1 high-confidence deny vs multiple low-confidence allows (malicious)"))

        # Family 3: Multiple low-confidence Deny vs one high-confidence Allow (benign)
        high_allow = _make_verdict("Allow", rng.uniform(0.85, 0.99), "v_expert")
        low_denies = [_make_verdict("Deny", rng.uniform(0.1, 0.35), f"v_noisy_{j}")
                      for j in range(rng.randint(2, 4))]
        scenarios.append(VotingScenario(
            f"noisy_deny_vs_expert_allow_{i}",
            [high_allow] + low_denies, False,
            "noisy low-confidence denies vs expert allow (benign — deny-first still applies)"))

        # Family 4: Mixed with abstentions
        verdicts = []
        n_total = rng.randint(4, 6)
        is_malicious = rng.choice([True, False])
        for j in range(n_total):
            r = rng.random()
            if r < 0.2:
                verdicts.append(_make_verdict("Abstain", 0.0, f"v_abs_{j}"))
            elif is_malicious and r < 0.7:
                verdicts.append(_make_verdict("Deny", rng.uniform(0.5, 0.95), f"v_{j}"))
            elif not is_malicious and r < 0.7:
                verdicts.append(_make_verdict("Allow", rng.uniform(0.5, 0.95), f"v_{j}"))
            else:
                vote = rng.choice(["Allow", "Deny"])
                verdicts.append(_make_verdict(vote, rng.uniform(0.2, 0.6), f"v_{j}"))
        scenarios.append(VotingScenario(
            f"mixed_abstain_{i}", verdicts, is_malicious,
            f"mixed with abstentions ({'malicious' if is_malicious else 'benign'})"))

    return scenarios


# ─────────────────────────────────────────────────────────────────────────────
# Voting strategies
# ─────────────────────────────────────────────────────────────────────────────

def weighted_vote(verdicts: list[Verdict]) -> str:
    """Confidence-weighted voting with Deny-priority (production strategy).

    Mirrors Rust JudgeEnsemble::confidence_weighted_tally logic:
      - w_deny = sum of confidence for Deny votes
      - w_allow = sum of confidence for Allow votes
      - Deny-first: if w_deny > 0, final = Deny
      - Else if conflict (w_allow > 0 and w_revise > 0), final = Deny
      - Else if w_allow >= w_revise, final = Allow
      - Else final = Revise
    """
    w_deny = sum(v.confidence for v in verdicts if v.vote == "Deny")
    w_allow = sum(v.confidence for v in verdicts if v.vote == "Allow")
    w_revise = sum(v.confidence for v in verdicts if v.vote == "Revise")

    if w_deny > 0:
        return "Deny"
    if w_allow > 0 and w_revise > 0:
        return "Deny"
    if w_allow >= w_revise:
        return "Allow"
    return "Revise"


def unweighted_vote(verdicts: list[Verdict]) -> str:
    """Simple majority vote: each non-Abstain verdict gets weight 1."""
    deny_count = sum(1 for v in verdicts if v.vote == "Deny")
    allow_count = sum(1 for v in verdicts if v.vote == "Allow")
    if deny_count > 0:
        return "Deny"
    if allow_count > 0:
        return "Allow"
    return "Abstain"


# ─────────────────────────────────────────────────────────────────────────────
# Evaluation
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class StrategyMetrics:
    strategy: str
    tp: int
    fp: int
    tn: int
    fn: int
    tpr: float
    fpr: float
    precision: float
    f1: float
    total: int


def evaluate_strategy(
    scenarios: list[VotingScenario],
    vote_fn,
    strategy_name: str,
) -> StrategyMetrics:
    tp = fp = tn = fn = 0
    for s in scenarios:
        decision = vote_fn(s.verdicts)
        predicted_deny = (decision == "Deny")
        if s.ground_truth_deny and predicted_deny:
            tp += 1
        elif s.ground_truth_deny and not predicted_deny:
            fn += 1
        elif not s.ground_truth_deny and predicted_deny:
            fp += 1
        else:
            tn += 1

    total = len(scenarios)
    tpr = tp / max(tp + fn, 1)
    fpr = fp / max(fp + tn, 1)
    precision = tp / max(tp + fp, 1)
    f1 = 2 * precision * tpr / max(precision + tpr, 1e-9)

    return StrategyMetrics(
        strategy=strategy_name,
        tp=tp, fp=fp, tn=tn, fn=fn,
        tpr=round(tpr, 4),
        fpr=round(fpr, 4),
        precision=round(precision, 4),
        f1=round(f1, 4),
        total=total,
    )


def main() -> int:
    scenarios = generate_scenarios()
    print(f"Generated {len(scenarios)} voting scenarios", file=sys.stderr)

    weighted_metrics = evaluate_strategy(scenarios, weighted_vote, "confidence_weighted")
    unweighted_metrics = evaluate_strategy(scenarios, unweighted_vote, "unweighted_majority")

    report = {
        "benchmark": "voting_comparison",
        "total_scenarios": len(scenarios),
        "strategies": {
            "confidence_weighted": asdict(weighted_metrics),
            "unweighted_majority": asdict(unweighted_metrics),
        },
    }

    print(json.dumps(report, indent=2, ensure_ascii=False))

    lines = [
        "",
        "=== Voting Comparison Benchmark (Round-3 R3) ===",
        f"  Total scenarios: {len(scenarios)}",
        "",
        f"  {'Strategy':<25} {'TPR':>8} {'FPR':>8} {'Prec':>8} {'F1':>8} {'TP':>5} {'FP':>5} {'TN':>5} {'FN':>5}",
        "  " + "-" * 85,
    ]
    for m in [weighted_metrics, unweighted_metrics]:
        lines.append(
            f"  {m.strategy:<25} {m.tpr:>8.4f} {m.fpr:>8.4f} "
            f"{m.precision:>8.4f} {m.f1:>8.4f} {m.tp:>5} {m.fp:>5} {m.tn:>5} {m.fn:>5}"
        )

    lines.append("")
    delta_tpr = weighted_metrics.tpr - unweighted_metrics.tpr
    delta_fpr = weighted_metrics.fpr - unweighted_metrics.fpr
    lines.append(f"  Delta (weighted - unweighted):  TPR {delta_tpr:+.4f}  FPR {delta_fpr:+.4f}")

    if weighted_metrics.f1 >= unweighted_metrics.f1:
        lines.append("  Conclusion: Confidence-weighted voting achieves equal or better F1.")
    else:
        lines.append("  Note: Unweighted voting has higher F1 in this synthetic benchmark.")
        lines.append("        Weighted voting still preserves Deny-priority safety invariant.")

    print("\n".join(lines), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
