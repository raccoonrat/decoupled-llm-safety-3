#!/usr/bin/env python3
"""
Export Track A summary JSONs → LaTeX table fragment for the paper.

Scans `paper_eval/runs/*.summary.json`, groups by (variant_id, dataset_id),
and emits a booktabs table with columns aligned to the paper's Track A
dimensions: dcbf_mode, judge_mode, dataset, RSR, FPR, mean-F1, max-F1,
Ring-0 µs, QP µs, cache_hit%, qp_exceeded%.

Usage:
  PYTHONPATH=. python3 scripts/export_trackA_table_tex.py
  PYTHONPATH=. python3 scripts/export_trackA_table_tex.py --runs-dir paper_eval/runs --out generated/trackA_main_table_cn.tex
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


KERNEL_ROOT = Path(__file__).resolve().parents[1]


def _fmt(v: float | None, pct: bool = False) -> str:
    if v is None:
        return "---"
    if pct:
        return f"{v * 100:.1f}\\%"
    return f"{v:.4f}"


def _fmt_us(v: float | None) -> str:
    if v is None:
        return "---"
    return f"{v:.0f}"


def main() -> int:
    ap = argparse.ArgumentParser(description="Export Track A summaries to LaTeX.")
    ap.add_argument(
        "--runs-dir",
        default=str(KERNEL_ROOT / "paper_eval" / "runs"),
        help="Directory containing *.summary.json files.",
    )
    ap.add_argument(
        "--out",
        default="",
        help="Output .tex file (default: stdout).",
    )
    args = ap.parse_args()

    runs_dir = Path(args.runs_dir)
    if not runs_dir.is_dir():
        print(f"runs directory not found: {runs_dir}", file=sys.stderr)
        return 2

    summaries: list[dict] = []
    for p in sorted(runs_dir.glob("*.summary.json")):
        try:
            summaries.append(json.loads(p.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue

    if not summaries:
        print("no summary files found", file=sys.stderr)
        return 1

    # Group: (variant_id, dataset_id) → latest summary (last run wins).
    grouped: dict[tuple[str, str], dict] = {}
    for s in summaries:
        key = (s.get("variant_id", "?"), s.get("dataset_id", "?"))
        grouped[key] = s

    lines: list[str] = []
    lines.append(r"\begin{table*}[t]")
    lines.append(r"\centering\small")
    lines.append(r"\setlength{\tabcolsep}{3pt}")
    lines.append(
        r"\begin{tabular}{@{}llllllllll@{}}"
    )
    lines.append(r"\toprule")
    lines.append(
        r"\textbf{DCBF} & \textbf{Judge} & \textbf{Dataset} & "
        r"\textbf{RSR} & \textbf{FPR} & \textbf{mean-$F_1$} & \textbf{max-$F_1$} & "
        r"\textbf{Ring-0 $\mu$s} & \textbf{QP $\mu$s} & \textbf{Cache\%} \\"
    )
    lines.append(r"\midrule")

    for (variant_id, dataset_id), s in sorted(grouped.items()):
        dims = s.get("dimensions", {})
        m = s.get("metrics", {})
        dcbf = dims.get("dcbf_mode", "?").replace("_", r"\_")
        judge = dims.get("judge_mode", "?")
        ds = dataset_id.replace("_", r"\_")
        rsr = _fmt(m.get("harmful_rsr"))
        fpr = _fmt(m.get("benign_refusal_fpr"))
        mf1 = _fmt(m.get("mean_extract_f1"))
        xf1 = _fmt(m.get("max_extract_f1"))
        rust = _fmt_us(m.get("mean_rust_total_us_last_step"))
        qp = _fmt_us(m.get("mean_qp_elapsed_us_last_step"))
        cache = _fmt(m.get("ring0_cache_hit_rate"), pct=True)
        row = f"{dcbf} & {judge} & {ds} & {rsr} & {fpr} & {mf1} & {xf1} & {rust} & {qp} & {cache} \\\\"
        lines.append(row)

    lines.append(r"\bottomrule")
    lines.append(r"\end{tabular}")
    lines.append(
        r"\caption{\textbf{Track A 主表}——按 \texttt{dcbf\_mode}、\texttt{judge\_mode} 与数据集切片。"
        r"RSR = 有害集拒绝成功率；FPR = 良性集误拒率；$F_1$ = 抽取残余（token 级）；"
        r"Ring-0 $\mu$s / QP $\mu$s 为最后一步 profile；Cache\% 为 Tier-1 命中率。}"
    )
    lines.append(r"\label{tab:tracka-main-cn}")
    lines.append(r"\end{table*}")

    tex = "\n".join(lines) + "\n"
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(tex, encoding="utf-8")
        print(f"written: {out_path}", file=sys.stderr)
    else:
        print(tex)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
