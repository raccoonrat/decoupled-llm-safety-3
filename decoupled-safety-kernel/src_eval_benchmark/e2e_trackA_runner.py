#!/usr/bin/env python3
"""Track A end-to-end experiment runner.

Protocol (P0-1 / P0-2):
  - Dataset:  JBB-Behaviors 100 misuse + 100 benign queries, 3 random seeds.
  - System:   Full decoupled safety kernel pipeline (Gateway → DCBF → Judge → Axiom Hive → Degrade).
  - Baselines: (a) no defence, (b) system-prompt-only, (c) Llama-Guard standalone.
  - Metrics:  ASR, RSR, benign FPR, F1_extraction, mean query cost, p95 latency.

This script defines the experimental harness and output format.
Actual LLM API calls require API keys and are gated behind --execute.
"""
from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


SEEDS = [42, 137, 2025]
N_MISUSE = 100
N_BENIGN = 100
BASELINES = ["no_defence", "system_prompt_only", "llama_guard_standalone", "decoupled_kernel"]

OUTPUT_DIR = Path(__file__).parent / "results" / "trackA"


@dataclass
class TrackAConfig:
    model_id: str = "gpt-4o-mini"
    seeds: list[int] = field(default_factory=lambda: list(SEEDS))
    n_misuse: int = N_MISUSE
    n_benign: int = N_BENIGN
    baselines: list[str] = field(default_factory=lambda: list(BASELINES))
    max_turns: int = 5
    timeout_per_query_s: float = 60.0


@dataclass
class QueryResult:
    query_id: str = ""
    seed: int = 0
    baseline: str = ""
    category: str = ""  # "misuse" or "benign"
    query_text: str = ""
    response_text: str = ""
    asr: bool = False
    rsr: bool = False
    fpr: bool = False
    latency_ms: float = 0.0
    query_cost: int = 0
    error: str | None = None


@dataclass
class AggregatedMetrics:
    baseline: str = ""
    seed: int = 0
    asr: float = 0.0
    rsr: float = 0.0
    benign_fpr: float = 0.0
    f1_extraction: float = 0.0
    mean_query_cost: float = 0.0
    p95_latency_ms: float = 0.0
    n_total: int = 0


def load_jbb_behaviors(split: str = "misuse", n: int = 100) -> list[dict[str, Any]]:
    """Load JBB-Behaviors dataset (stub — returns placeholder queries)."""
    return [
        {"id": f"{split}_{i:03d}", "text": f"[{split} query {i}]", "category": split}
        for i in range(n)
    ]


def run_single_query(
    query: dict[str, Any],
    baseline: str,
    config: TrackAConfig,
    seed: int,
) -> QueryResult:
    """Run a single query through the specified baseline.

    In --execute mode, this would:
      1. Initialize the safety kernel (or baseline) with the given seed.
      2. Send the query to the LLM API via the kernel pipeline.
      3. Evaluate the response using StrongREJECT / Llama-Guard.
      4. Return the structured result.
    """
    return QueryResult(
        query_id=query["id"],
        seed=seed,
        baseline=baseline,
        category=query["category"],
        query_text=query["text"],
        response_text="[requires LLM API]",
        latency_ms=0.0,
        query_cost=1,
        error="dry_run",
    )


def compute_metrics(results: list[QueryResult], baseline: str, seed: int) -> AggregatedMetrics:
    """Aggregate per-query results into metrics."""
    misuse = [r for r in results if r.category == "misuse"]
    benign = [r for r in results if r.category == "benign"]
    latencies = sorted(r.latency_ms for r in results)

    asr = sum(1 for r in misuse if r.asr) / max(len(misuse), 1)
    rsr = sum(1 for r in misuse if r.rsr) / max(len(misuse), 1)
    fpr = sum(1 for r in benign if r.fpr) / max(len(benign), 1)
    mean_cost = sum(r.query_cost for r in results) / max(len(results), 1)
    p95_idx = int(0.95 * len(latencies)) if latencies else 0
    p95 = latencies[p95_idx] if latencies else 0.0

    return AggregatedMetrics(
        baseline=baseline,
        seed=seed,
        asr=asr,
        rsr=rsr,
        benign_fpr=fpr,
        f1_extraction=0.0,
        mean_query_cost=mean_cost,
        p95_latency_ms=p95,
        n_total=len(results),
    )


def run_experiment(config: TrackAConfig, execute: bool = False) -> list[AggregatedMetrics]:
    """Run the full Track A experiment matrix."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    all_metrics: list[AggregatedMetrics] = []
    misuse_queries = load_jbb_behaviors("misuse", config.n_misuse)
    benign_queries = load_jbb_behaviors("benign", config.n_benign)
    all_queries = misuse_queries + benign_queries

    for seed in config.seeds:
        for baseline in config.baselines:
            print(f"[Track A] seed={seed}  baseline={baseline}  queries={len(all_queries)}")
            results: list[QueryResult] = []
            for q in all_queries:
                r = run_single_query(q, baseline, config, seed)
                results.append(r)

            metrics = compute_metrics(results, baseline, seed)
            all_metrics.append(metrics)

            out_file = OUTPUT_DIR / f"{baseline}_seed{seed}.json"
            out_file.write_text(json.dumps({
                "config": asdict(config),
                "metrics": asdict(metrics),
                "results": [asdict(r) for r in results],
            }, indent=2, ensure_ascii=False))
            print(f"  → saved {out_file}")

    summary_file = OUTPUT_DIR / "summary.json"
    summary_file.write_text(json.dumps(
        [asdict(m) for m in all_metrics], indent=2, ensure_ascii=False
    ))
    print(f"\n[Track A] Summary saved to {summary_file}")
    return all_metrics


def main() -> None:
    parser = argparse.ArgumentParser(description="Track A E2E experiment runner")
    parser.add_argument("--execute", action="store_true", help="Actually call LLM APIs (requires keys)")
    parser.add_argument("--model", default="gpt-4o-mini", help="Target model ID")
    parser.add_argument("--seeds", nargs="+", type=int, default=SEEDS)
    args = parser.parse_args()

    config = TrackAConfig(model_id=args.model, seeds=args.seeds)
    run_experiment(config, execute=args.execute)


if __name__ == "__main__":
    main()
