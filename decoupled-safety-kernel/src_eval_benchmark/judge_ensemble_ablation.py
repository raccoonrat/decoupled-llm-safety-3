#!/usr/bin/env python3
"""Judge Ensemble ablation study framework.

Protocol (P0-2 complement):
  Systematically compare Judge Ensemble configurations to quantify
  the marginal value of each design choice:

  Ablation axes:
    A1. Single guard (Llama-Guard only) vs. dual (+ StrongREJECT) vs. triple (+ regex rules).
    A2. Unweighted majority vote vs. confidence-weighted vote.
    A3. Deny-priority invariant ON vs. OFF.
    A4. With / without BreakGlass policy.

  Dataset: JBB-Behaviors (100 misuse + 100 benign), 3 seeds.
  Metrics: ASR, benign FPR, F1, mean confidence spread, conflict rate.

This script defines the ablation matrix and output format.
Actual LLM API calls require API keys and are gated behind --execute.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any


OUTPUT_DIR = Path(__file__).parent / "results" / "ablation"
SEEDS = [42, 137, 2025]


class VoteMode(str, Enum):
    UNWEIGHTED = "unweighted"
    WEIGHTED = "weighted"


@dataclass
class AblationConfig:
    """Single ablation cell configuration."""
    config_id: str = ""
    guards: list[str] = field(default_factory=lambda: ["llama_guard"])
    vote_mode: VoteMode = VoteMode.UNWEIGHTED
    deny_priority: bool = True
    break_glass: bool = False


ABLATION_MATRIX: list[AblationConfig] = [
    AblationConfig(
        config_id="single_unweighted",
        guards=["llama_guard"],
        vote_mode=VoteMode.UNWEIGHTED,
    ),
    AblationConfig(
        config_id="dual_unweighted",
        guards=["llama_guard", "strongreject"],
        vote_mode=VoteMode.UNWEIGHTED,
    ),
    AblationConfig(
        config_id="triple_unweighted",
        guards=["llama_guard", "strongreject", "regex_rules"],
        vote_mode=VoteMode.UNWEIGHTED,
    ),
    AblationConfig(
        config_id="triple_weighted",
        guards=["llama_guard", "strongreject", "regex_rules"],
        vote_mode=VoteMode.WEIGHTED,
    ),
    AblationConfig(
        config_id="triple_weighted_no_deny_priority",
        guards=["llama_guard", "strongreject", "regex_rules"],
        vote_mode=VoteMode.WEIGHTED,
        deny_priority=False,
    ),
    AblationConfig(
        config_id="triple_weighted_breakglass",
        guards=["llama_guard", "strongreject", "regex_rules"],
        vote_mode=VoteMode.WEIGHTED,
        break_glass=True,
    ),
]


@dataclass
class Vote:
    guard_id: str = ""
    vote: str = ""      # Allow / Revise / Deny / Abstain
    confidence: float = 1.0


@dataclass
class AblationResult:
    query_id: str = ""
    seed: int = 0
    config_id: str = ""
    category: str = ""
    votes: list[dict[str, Any]] = field(default_factory=list)
    final_action: str = ""
    conflict: bool = False
    asr: bool = False
    fpr: bool = False
    error: str | None = None


@dataclass
class AblationMetrics:
    config_id: str = ""
    seed: int = 0
    asr: float = 0.0
    benign_fpr: float = 0.0
    f1: float = 0.0
    conflict_rate: float = 0.0
    mean_confidence_spread: float = 0.0
    n_total: int = 0


def load_queries(n_misuse: int = 100, n_benign: int = 100) -> list[dict[str, Any]]:
    """Stub loader for JBB-Behaviors."""
    queries = []
    for i in range(n_misuse):
        queries.append({"id": f"misuse_{i:03d}", "text": f"[misuse query {i}]", "category": "misuse"})
    for i in range(n_benign):
        queries.append({"id": f"benign_{i:03d}", "text": f"[benign query {i}]", "category": "benign"})
    return queries


def simulate_guard(guard_id: str, query: dict[str, Any]) -> Vote:
    """Simulate a guard's vote (stub — returns placeholder)."""
    return Vote(guard_id=guard_id, vote="Allow", confidence=0.9)


def ensemble_tally(
    votes: list[Vote],
    config: AblationConfig,
) -> tuple[str, bool]:
    """Apply ensemble tally logic matching the Rust JudgeEnsemble.

    Returns (final_action, conflict).
    """
    has_allow = any(v.vote == "Allow" for v in votes)
    has_deny = any(v.vote == "Deny" for v in votes)
    conflict = has_allow and has_deny

    if config.vote_mode == VoteMode.WEIGHTED:
        w_allow = sum(v.confidence for v in votes if v.vote == "Allow")
        w_deny = sum(v.confidence for v in votes if v.vote == "Deny")
        w_revise = sum(v.confidence for v in votes if v.vote == "Revise")

        if config.deny_priority and w_deny > 0:
            action = "Deny"
        elif w_deny > max(w_allow, w_revise):
            action = "Deny"
        elif w_revise > w_allow:
            action = "Revise"
        else:
            action = "Allow"
    else:
        n_allow = sum(1 for v in votes if v.vote == "Allow")
        n_deny = sum(1 for v in votes if v.vote == "Deny")
        n_revise = sum(1 for v in votes if v.vote == "Revise")

        if config.deny_priority and n_deny > 0:
            action = "Deny"
        elif n_deny >= max(n_allow, n_revise):
            action = "Deny"
        elif n_revise > n_allow:
            action = "Revise"
        else:
            action = "Allow"

    if conflict and config.break_glass:
        action = "Allow"

    if conflict and not config.break_glass:
        action = "Deny"

    return action, conflict


def run_ablation_cell(
    config: AblationConfig,
    queries: list[dict[str, Any]],
    seed: int,
) -> tuple[list[AblationResult], AblationMetrics]:
    """Run one cell of the ablation matrix."""
    results: list[AblationResult] = []

    for q in queries:
        votes = [simulate_guard(g, q) for g in config.guards]
        final_action, conflict = ensemble_tally(votes, config)
        results.append(AblationResult(
            query_id=q["id"],
            seed=seed,
            config_id=config.config_id,
            category=q["category"],
            votes=[asdict(v) for v in votes],
            final_action=final_action,
            conflict=conflict,
            error="dry_run",
        ))

    misuse = [r for r in results if r.category == "misuse"]
    benign = [r for r in results if r.category == "benign"]
    conflicts = [r for r in results if r.conflict]

    asr = sum(1 for r in misuse if r.asr) / max(len(misuse), 1)
    fpr = sum(1 for r in benign if r.fpr) / max(len(benign), 1)
    conflict_rate = len(conflicts) / max(len(results), 1)

    metrics = AblationMetrics(
        config_id=config.config_id,
        seed=seed,
        asr=asr,
        benign_fpr=fpr,
        conflict_rate=conflict_rate,
        n_total=len(results),
    )
    return results, metrics


def run_full_ablation(execute: bool = False) -> list[AblationMetrics]:
    """Run the complete ablation matrix."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    queries = load_queries()

    all_metrics: list[AblationMetrics] = []
    for seed in SEEDS:
        for config in ABLATION_MATRIX:
            print(f"[Ablation] seed={seed}  config={config.config_id}  guards={config.guards}")
            results, metrics = run_ablation_cell(config, queries, seed)
            all_metrics.append(metrics)

            out_file = OUTPUT_DIR / f"{config.config_id}_seed{seed}.json"
            out_file.write_text(json.dumps({
                "config": asdict(config),
                "metrics": asdict(metrics),
                "results": [asdict(r) for r in results],
            }, indent=2, ensure_ascii=False))
            print(f"  → saved {out_file}")

    summary_file = OUTPUT_DIR / "ablation_summary.json"
    summary_file.write_text(json.dumps(
        [asdict(m) for m in all_metrics], indent=2, ensure_ascii=False
    ))
    print(f"\n[Ablation] Summary saved to {summary_file}")
    return all_metrics


def main() -> None:
    parser = argparse.ArgumentParser(description="Judge Ensemble ablation runner")
    parser.add_argument("--execute", action="store_true", help="Actually call LLM APIs")
    args = parser.parse_args()
    run_full_ablation(execute=args.execute)


if __name__ == "__main__":
    main()
