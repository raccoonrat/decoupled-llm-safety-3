#!/usr/bin/env python3
"""
Full-chain E2E: Ring-3 DeepSeek top logprobs → JSON → Ring-0 `e2e_ring3_json` (projection + I6).

支持 **自回归多步**：在 while 循环中反复调用 `fetch_next_token_topk` 与子进程，直到 EOS 或步数上限。

DCBF 候选过滤有两个互斥模式（--dcbf-v2 旗标）：

  v1（默认）：`h(x_t)` = 累积 logprob 势能（API 提供）。快速、零延迟，但依赖目标模型提供真实 logprobs。
  v2（--dcbf-v2）：`h(x_t)` 由本地独立 ProxyEnsemble 计算几何距离，完全不依赖 target logprobs。
               即使 API 退化或 logprobs 被伪造，v2 仍能独立判断前向不变性（Gap 1 fix）。

Prereq: `cargo build --bin e2e_ring3_json`, repo `env` with `DEEPSEEK_API_KEY`, `pip install openai`.

Latency: 埋点覆盖 LLM、Python JSON 序列化、子进程 IPC 墙钟、Rust `profile`（与 RFC §10
`HARD_LATENCY_BUDGET` / `QP_INNER_BUDGET` 对照）。`E2E_LATENCY=0` 关闭 stderr 报告。

Usage:
  PYTHONPATH=decoupled-safety-kernel python decoupled-safety-kernel/scripts/e2e_full_chain.py [options] [user_prompt]
  # 启用 v2 独立几何探针（Gap 1 修复）：
  PYTHONPATH=decoupled-safety-kernel python decoupled-safety-kernel/scripts/e2e_full_chain.py --dcbf-v2 [user_prompt]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

KERNEL_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = KERNEL_ROOT.parent
if str(KERNEL_ROOT) not in sys.path:
    sys.path.insert(0, str(KERNEL_ROOT))

from llm_user_space.env_loader import load_env_file
from llm_user_space.execution_context import ExecutionContext
from llm_user_space.gateway_mirror import GatewayHardReject, sanitize_input
from llm_user_space.next_token_adapter import NextTokenTopK, fetch_next_token_topk
from src_observability.dcbf_evaluator import LatentStateProxy, check_forward_invariance
from src_observability.dcbf_evaluator_v2 import DCBFEvaluatorV2, build_default_v2
from src_observability.proxy_ensemble import LatentState

# RFC §10 推荐值（与 `axiom_hive_solver::HARD_LATENCY_BUDGET` / `QP_INNER_BUDGET` 一致）
HARD_LATENCY_MS = 20.0
QP_INNER_MS = 4.0

# 常见 EOS 串（Ring-3 仅见 token 文本）；亦可设环境变量 E2E_EOS_EXTRA 为逗号分隔额外串
_DEFAULT_EOS = (
    "</s>",
    "<|endoftext|>",
    "<|EOT|>",
    "<|im_end|>",
    "<|end|>",
)


def _eos_set() -> frozenset[str]:
    extra = os.environ.get("E2E_EOS_EXTRA", "")
    parts = {s.strip() for s in extra.split(",") if s.strip()}
    return frozenset(_DEFAULT_EOS) | parts


def _find_e2e_bin() -> Path:
    for sub in ("release", "debug"):
        p = KERNEL_ROOT / "target" / sub / "e2e_ring3_json"
        if p.is_file():
            return p
    raise FileNotFoundError(
        "e2e_ring3_json not built; run: cd decoupled-safety-kernel && cargo build --bin e2e_ring3_json"
    )


def _s_to_ms(s: float) -> float:
    return s * 1000.0


def _us_to_ms(us: float) -> float:
    return us / 1000.0


def _emit_latency_report(
    *,
    step: int,
    llm_s: float,
    py_json_serialize_s: float,
    subprocess_wall_s: float,
    profile: dict[str, object] | None,
) -> None:
    lines = [
        "",
        f"=== E2E latency profile (step {step}) ===",
        f"  Ring-3 LLM (next_token_topk):     {_s_to_ms(llm_s):.2f} ms",
        f"  Python json.dumps(payload):       {_s_to_ms(py_json_serialize_s):.2f} ms",
        f"  subprocess.run(e2e_ring3_json):   {_s_to_ms(subprocess_wall_s):.2f} ms  (IPC wall)",
    ]
    if profile:
        lines.extend(
            [
                "  --- Rust binary (e2e_ring3_json) profile ---",
                f"  json_deserialize_us:             {profile.get('json_deserialize_us')} µs",
                f"  prep_before_deadline_us:         {profile.get('prep_before_deadline_us')} µs",
                f"  projection_us (Axiom Hive):      {profile.get('projection_us')} µs",
                f"  audit_i6_us:                     {profile.get('audit_i6_us')} µs",
                f"  rust_total_us:                   {profile.get('rust_total_us')} µs",
                f"  qp_elapsed_us (inner clock):    {profile.get('qp_elapsed_us')} µs",
                f"  qp_budget_exceeded:             {profile.get('qp_budget_exceeded')}",
                f"  hard_budget_exceeded:           {profile.get('hard_budget_exceeded')}",
            ]
        )
        rust_total_ms = _us_to_ms(float(profile.get("rust_total_us", 0)))
        proj_ms = _us_to_ms(float(profile.get("projection_us", 0)))
        qp_us = float(profile.get("qp_elapsed_us", 0))
        qp_ms = _us_to_ms(qp_us)
        lines.append(
            f"  对照: Ring-0 单步硬预算 (投影窗口) ≈ {HARD_LATENCY_MS:.0f} ms；QP 内层 ≈ {QP_INNER_MS:.0f} ms"
        )
        if rust_total_ms > HARD_LATENCY_MS:
            lines.append(
                f"  WARN: Rust 进程总耗时 {rust_total_ms:.2f} ms > {HARD_LATENCY_MS:.0f} ms "
                "(JSON/准备路径占用；投影仍可在独立 deadline 内完成，见 projection_us)"
            )
        if proj_ms > HARD_LATENCY_MS:
            lines.append(
                f"  WARN: projection_us {proj_ms:.2f} ms > {HARD_LATENCY_MS:.0f} ms — 检查负载或逻辑"
            )
        if qp_ms > QP_INNER_MS:
            lines.append(
                f"  WARN: qp_elapsed {qp_ms:.2f} ms > {QP_INNER_MS:.0f} ms (Axiom Hive 内层)"
            )
    else:
        lines.append("  (no Rust profile in stdout JSON — rebuild e2e_ring3_json)")
    lines.append(
        "  提示: 若 IPC+JSON 持续吃掉大量预算，可评估 Protobuf/gRPC 或 mmap 共享缓冲（下一阶段）。"
    )
    print("\n".join(lines), file=sys.stderr)


def _filter_dcbf_v1(
    nt: NextTokenTopK,
    *,
    h_cumulative: float,
    trace_id: str,
    alpha: float,
) -> tuple[list[float], list[int], list[float], float]:
    """
    v1 候选过滤：`h(x_t)` = 累积 logprob 势能（依赖目标 LLM 提供的 logprobs）。
    返回 (logits_filtered, original_indices, margins_kept, margin_for_kernel_min)。
    """
    logits_f: list[float] = []
    orig_idx: list[int] = []
    margins_kept: list[float] = []
    for i, (_tok, lp) in enumerate(nt.raw_top_logprobs):
        h_t1 = h_cumulative + lp
        st = LatentStateProxy(trace_id=trace_id, h=h_cumulative)
        s1 = LatentStateProxy(trace_id=trace_id, h=h_t1)
        rep = check_forward_invariance(st, s1, alpha)
        if not rep.interrupt:
            logits_f.append(nt.logits[i])
            orig_idx.append(i)
            margins_kept.append(rep.margin)
    if not margins_kept:
        return [], [], [], 0.0
    m_kernel = min(margins_kept)
    return logits_f, orig_idx, margins_kept, m_kernel


def _filter_dcbf_v2(
    nt: NextTokenTopK,
    *,
    state_t: LatentState,
    trace_id: str,
    alpha: float,
    evaluator: DCBFEvaluatorV2,
    prefix: str,
) -> tuple[list[float], list[int], list[float], float, LatentState]:
    """
    v2 候选过滤（Gap 1 fix）：`h(x_t)` 由本地 ProxyEnsemble 独立计算，完全不依赖 logprobs。
    即使 logprobs 被 API 剥夺或被对抗伪造，v2 仍可正确判断前向不变性。

    返回 (logits_filtered, original_indices, margins_kept, margin_for_kernel_min, best_state_t1)。
    best_state_t1：surviving 候选中 margin 最大者的 LatentState（用于下一步 state_t）。
    """
    logits_f: list[float] = []
    orig_idx: list[int] = []
    margins_kept: list[float] = []
    state_t1_candidates: list[LatentState] = []

    for i, (tok, _lp) in enumerate(nt.raw_top_logprobs):
        prefix_t1 = f"{prefix} {tok}"
        result = evaluator.step(state_t, tok, trace_id, alpha, prefix_for_t1=prefix_t1)
        if not result.interrupt:
            logits_f.append(nt.logits[i])
            orig_idx.append(i)
            margins_kept.append(result.dominant_margin)
            state_t1_candidates.append(result.state_t1)

    if not margins_kept:
        return [], [], [], 0.0, state_t
    m_kernel = min(margins_kept)
    best_idx = margins_kept.index(max(margins_kept))
    return logits_f, orig_idx, margins_kept, m_kernel, state_t1_candidates[best_idx]


def main() -> int:
    ap = argparse.ArgumentParser(description="E2E: DeepSeek → JSON IPC → Ring-0 (autoregressive optional).")
    ap.add_argument(
        "user_prompt",
        nargs="?",
        default="Reply with one word only: OK",
        help="User message (sanitized via gateway).",
    )
    ap.add_argument(
        "--max-steps",
        type=int,
        default=int(os.environ.get("E2E_MAX_STEPS", "32")),
        metavar="N",
        help="Autoregressive upper bound (default: env E2E_MAX_STEPS or 32).",
    )
    ap.add_argument(
        "--alpha",
        type=float,
        default=float(os.environ.get("E2E_DCBF_ALPHA", "0.2")),
        help="DCBF Theorem 2.2 alpha in (0,1] (default: env E2E_DCBF_ALPHA or 0.2).",
    )
    ap.add_argument(
        "--single-step",
        action="store_true",
        help="Only one Ring-0 step (legacy behavior).",
    )
    ap.add_argument(
        "--top-k",
        type=int,
        default=5,
        metavar="K",
        help="Top-K logprobs per step (default 5).",
    )
    ap.add_argument(
        "--dcbf-v2",
        action="store_true",
        default=os.environ.get("E2E_DCBF_V2", "0") in ("1", "true", "yes"),
        help=(
            "启用 DCBF v2 ProxyEnsemble（Gap 1 修复）：用本地独立几何探针替代 logprob 累积势能。"
            " 即使 API 退化/对抗伪造 logprobs，势能计算仍独立可信。"
            " (env: E2E_DCBF_V2=1)"
        ),
    )
    ap.add_argument(
        "--dcbf-v2-dim",
        type=int,
        default=int(os.environ.get("E2E_DCBF_V2_DIM", "128")),
        help="v2 ProxyEnsemble 嵌入维度（默认 128）。(env: E2E_DCBF_V2_DIM)",
    )
    ap.add_argument(
        "--system",
        default=os.environ.get("E2E_SYSTEM_PROMPT", "You are a helpful assistant."),
        help="System prompt (env: E2E_SYSTEM_PROMPT). Used by extraction protocol.",
    )
    args = ap.parse_args()
    alpha = args.alpha
    if not (0.0 < alpha <= 1.0):
        print("alpha must satisfy 0 < alpha <= 1 (Theorem 2.2)", file=sys.stderr)
        return 2

    load_env_file(REPO_ROOT / "env")

    user_text = args.user_prompt
    try:
        sp = sanitize_input(user_text.encode("utf-8"))
    except GatewayHardReject as e:
        print("HARD_REJECT", e, file=sys.stderr)
        return 2

    ctx = ExecutionContext(
        trace_id=str(uuid.uuid4()),
        policy_revision=1,
        sanitized_prompt=sp.canonical,
    )

    messages: list[dict[str, str]] = [
        {"role": "system", "content": args.system},
        {
            "role": "user",
            "content": ctx.sanitized_prompt.decode("utf-8", errors="replace"),
        },
    ]

    exe = _find_e2e_bin()
    eos_markers = _eos_set()

    max_steps = 1 if args.single_step else max(1, args.max_steps)
    latency_on = os.environ.get("E2E_LATENCY", "1") not in ("0", "false", "no")
    use_v2 = args.dcbf_v2

    # ── v1 状态（logprob 累积势能）
    h_cumulative = 0.0

    # ── v2 状态（本地独立几何探针）
    v2_evaluator: DCBFEvaluatorV2 | None = None
    v2_state_t: LatentState | None = None
    if use_v2:
        v2_evaluator = build_default_v2(dim=args.dcbf_v2_dim, embedder_preference="sklearn")
        initial_text = ctx.sanitized_prompt.decode("utf-8", errors="replace")
        v2_state_t = v2_evaluator.embed(initial_text, ctx.trace_id)
        print(
            json.dumps({
                "event": "dcbf_v2_init",
                "source_layer": v2_state_t.source_layer,
                "dimension": v2_state_t.dimension,
                "probe_version": v2_state_t.probe_version,
            }),
            file=sys.stderr,
        )

    # 滚动前缀文本（仅 v2 模式使用，用于精确拼接候选）
    rolling_prefix = ctx.sanitized_prompt.decode("utf-8", errors="replace")

    for step in range(max_steps):
        t_llm0 = time.perf_counter()
        nt = fetch_next_token_topk(messages, top_k=args.top_k, load_env=False)
        llm_s = time.perf_counter() - t_llm0

        if use_v2 and v2_evaluator is not None and v2_state_t is not None:
            logits_f, orig_idx, margins_kept, m_kernel, next_v2_state = _filter_dcbf_v2(
                nt,
                state_t=v2_state_t,
                trace_id=ctx.trace_id,
                alpha=alpha,
                evaluator=v2_evaluator,
                prefix=rolling_prefix,
            )
        else:
            logits_f, orig_idx, margins_kept, m_kernel = _filter_dcbf_v1(
                nt, h_cumulative=h_cumulative, trace_id=ctx.trace_id, alpha=alpha
            )
            next_v2_state = None

        if not logits_f:
            print(
                json.dumps(
                    {
                        "event": "dcbf_interrupt",
                        "dcbf_mode": "v2-proxy-ensemble" if use_v2 else "v1-logprob",
                        "step": step,
                        "h_t": h_cumulative if not use_v2 else None,
                        "reason": "no candidate satisfies forward invariance (all margin < 0)",
                    },
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
            return 3

        t_ser0 = time.perf_counter()
        payload = {
            "logits": logits_f,
            "topk_indices": list(range(len(logits_f))),
            "trace_id": ctx.trace_id,
            "policy_revision": ctx.policy_revision,
            "dcbf_margin": float(m_kernel),
        }
        raw_json = json.dumps(payload)
        py_json_serialize_s = time.perf_counter() - t_ser0

        t_sub0 = time.perf_counter()
        proc = subprocess.run(
            [str(exe), "-"],
            input=raw_json,
            text=True,
            capture_output=True,
            cwd=str(KERNEL_ROOT),
            check=False,
        )
        subprocess_wall_s = time.perf_counter() - t_sub0

        if proc.returncode != 0:
            print(proc.stderr or proc.stdout, file=sys.stderr)
            return proc.returncode or 1

        try:
            out = json.loads(proc.stdout)
        except json.JSONDecodeError:
            print(proc.stdout, end="")
            return 1

        profile = out.get("profile") if isinstance(out, dict) else None
        if latency_on:
            _emit_latency_report(
                step=step,
                llm_s=llm_s,
                py_json_serialize_s=py_json_serialize_s,
                subprocess_wall_s=subprocess_wall_s,
                profile=profile if isinstance(profile, dict) else None,
            )

        chosen = out.get("chosen_index")
        if chosen is None or not isinstance(chosen, int) or chosen < 0 or chosen >= len(orig_idx):
            print(
                json.dumps({"event": "protocol_fault", "step": step, "chosen_index": chosen}),
                file=sys.stderr,
            )
            return 4

        if args.single_step:
            # 与历史行为一致：stdout 仅为 Ring-0 二进制 JSON（便于 jq / 对照）。
            print(proc.stdout, end="")
            return 0

        orig = orig_idx[chosen]
        token_text, lp_chosen = nt.raw_top_logprobs[orig]
        margin_chosen = margins_kept[chosen]

        # ── 更新各 DCBF 模式的状态
        h_before = h_cumulative
        h_cumulative = h_cumulative + lp_chosen
        rolling_prefix = f"{rolling_prefix} {token_text}"
        if use_v2 and next_v2_state is not None:
            v2_state_t = next_v2_state

        step_record: dict[str, Any] = {
            "step": step,
            "dcbf_mode": "v2-proxy-ensemble" if use_v2 else "v1-logprob",
            "alpha": alpha,
            "dcbf_margin_kernel": m_kernel,
            "dcbf_margin_chosen": margin_chosen,
            "chosen_original_index": orig,
            "token": token_text,
            "ring0": out,
        }
        if use_v2:
            step_record["v2_source_layer"] = (v2_state_t.source_layer if v2_state_t else None)
            step_record["v2_fingerprint_t1"] = (v2_state_t.fingerprint() if v2_state_t else None)
        else:
            step_record["h_t"] = h_before
            step_record["h_t1"] = h_cumulative
        print(json.dumps(step_record, ensure_ascii=False))

        messages.append({"role": "assistant", "content": token_text})

        if token_text in eos_markers:
            break

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
