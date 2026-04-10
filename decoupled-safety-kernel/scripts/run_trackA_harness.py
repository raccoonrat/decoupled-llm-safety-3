#!/usr/bin/env python3
"""
CLI wrapper for Track A harness.

Examples:
  # Offline stub (no upstream model; JSONL + summary with N/A metrics)
  PYTHONPATH=. python3 scripts/run_trackA_harness.py --offline --dataset harmful
  PYTHONPATH=. python3 scripts/run_trackA_harness.py --offline --dataset benign
  PYTHONPATH=. python3 scripts/run_trackA_harness.py --offline --dataset extract

  # Online (requires DEEPSEEK_API_KEY in repo env)
  PYTHONPATH=. python3 scripts/run_trackA_harness.py --dataset harmful --dcbf v2 --judge heuristic
  PYTHONPATH=. python3 scripts/run_trackA_harness.py --dataset extract  --dcbf v2 --judge heuristic

  # Online with HTTP judge service (start judge_service.py first)
  PYTHONPATH=. python3 scripts/run_trackA_harness.py --dataset harmful --dcbf v2 --judge http

  # Full paper matrix (all three datasets × single variant)
  for ds in harmful benign extract; do
    PYTHONPATH=. python3 scripts/run_trackA_harness.py --dataset "$ds" --dcbf v2 --judge heuristic
  done
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src_paper_eval.trackA_harness import (
    TrackAVariant,
    load_extract_items,
    load_items_from_txt,
    run_trackA,
    write_json,
    write_jsonl,
)


KERNEL_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = KERNEL_ROOT.parent


def _load_repo_env() -> None:
    """Load repo-root `env` so API keys / proxy config are in os.environ."""
    try:
        parent = str(KERNEL_ROOT)
        if parent not in sys.path:
            sys.path.insert(0, parent)
        from llm_user_space.env_loader import load_env_file
        load_env_file(REPO_ROOT / "env")
    except ImportError:
        pass


def main() -> int:
    _load_repo_env()
    ap = argparse.ArgumentParser(description="Track A harness runner (paper-oriented).")
    ap.add_argument(
        "--dataset",
        choices=["harmful", "benign", "extract"],
        default="harmful",
        help="Which dataset to run.",
    )
    ap.add_argument(
        "--offline",
        action="store_true",
        help="Do not call upstream LLM; emit stub records (metrics become N/A).",
    )
    ap.add_argument("--dcbf", choices=["v1", "v2"], default="v2", help="DCBF mode.")
    ap.add_argument(
        "--judge",
        choices=["none", "heuristic", "http"],
        default="heuristic",
        help="Judge mode (heuristic=regex MVP, http=external judge_service).",
    )
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--alpha", type=float, default=0.2)
    ap.add_argument("--max-steps", type=int, default=32)
    ap.add_argument(
        "--seeds",
        default="42,43,44",
        help="Comma-separated seed list (recorded for stratification).",
    )
    ap.add_argument(
        "--out-dir",
        default=str(KERNEL_ROOT / "paper_eval" / "runs"),
        help="Output directory for JSONL + summary JSON.",
    )
    args = ap.parse_args()

    data_dir = KERNEL_ROOT / "paper_eval" / "data"
    dataset_id = f"trackA_{args.dataset}"

    if args.dataset == "extract":
        prompts_path = data_dir / "extract_prompts_v1.txt"
        secrets_path = data_dir / "extract_secrets_v1.json"
        if not prompts_path.is_file() or not secrets_path.is_file():
            print(
                json.dumps({
                    "error": "missing_extract_files",
                    "prompts": str(prompts_path),
                    "secrets": str(secrets_path),
                }),
                file=sys.stderr,
            )
            return 2
        items = load_extract_items(prompts_path, secrets_path, dataset_id=dataset_id)
    else:
        txt = data_dir / f"{args.dataset}_prompts_v1.txt"
        if not txt.is_file():
            print(
                json.dumps({"error": "missing_dataset_file", "path": str(txt)}),
                file=sys.stderr,
            )
            return 2
        items = load_items_from_txt(txt, label=args.dataset, dataset_id=dataset_id)

    dcbf_mode = "v2-proxy-ensemble" if args.dcbf == "v2" else "v1-logprob"
    variant = TrackAVariant(
        dcbf_mode=dcbf_mode,
        judge_mode=args.judge,
        action_space_guard="off",
    )
    seeds = [int(s.strip()) for s in str(args.seeds).split(",") if s.strip()]

    records, summary = run_trackA(
        dataset_id=dataset_id,
        items=items,
        variant=variant,
        seeds=seeds,
        top_k=args.top_k,
        alpha=args.alpha,
        max_steps=args.max_steps,
        offline=bool(args.offline),
    )

    out_dir = Path(args.out_dir)
    out_jsonl = out_dir / f"{summary['run_id']}.jsonl"
    out_summary = out_dir / f"{summary['run_id']}.summary.json"
    write_jsonl(out_jsonl, records)
    write_json(out_summary, summary)

    print(json.dumps({"jsonl": str(out_jsonl), "summary": str(out_summary)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
