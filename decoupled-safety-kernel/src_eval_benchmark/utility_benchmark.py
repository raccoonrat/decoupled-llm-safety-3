"""
Utility Benchmark — Projection Over-Blocking Rate（RFC §12.2 / Gap 2 度量）

**定义**：
    projection_over_blocking_rate =
        |{benign 输入且系统返回 page_fault=true 或 feasible=false}|
        ───────────────────────────────────────────────────────────
                    |{benign 输入总数}|

    > 0 说明存在"虚假过度阻断（false Page-Fault）"，可能由：
    >   - QP timeout（qp_budget_exceeded=true）频繁触发；
    >   - dcbf_margin 过于保守导致候选集坍缩；
    >   - Warm-start 缓存命中率过低、每步冷启动重解。

**指标集合**（输出 JSON）：
    total_cases            总测试用例数
    blocked_cases          page_fault=true 或 feasible=false 的数量
    qp_timeout_cases       qp_budget_exceeded=true 的数量
    cache_hit_cases        profile.cache_hit=true 的数量
    projection_over_blocking_rate
    qp_timeout_rate
    cache_hit_rate
    mean_qp_elapsed_us     平均 QP 耗时（微秒）
    mean_rust_total_us     平均 Rust 进程耗时（微秒）
    per_case               per-case 详情数组

**离线可运行**：不需要 LLM API。所有 payload 由脚本直接构造。

运行方式：
    cd decoupled-safety-kernel && cargo build --bin e2e_ring3_json
    PYTHONPATH=. python3 src_eval_benchmark/utility_benchmark.py
    # 或带压力参数（触发 QP 超时观测）：
    UTILITY_INJECT_QP_STRESS=1 python3 src_eval_benchmark/utility_benchmark.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

KERNEL_ROOT = Path(__file__).resolve().parents[1]
if str(KERNEL_ROOT) not in sys.path:
    sys.path.insert(0, str(KERNEL_ROOT))

# ─────────────────────────────────────────────────────────────────────────────
# 测试用例库（绝对安全的良性序列）
# ─────────────────────────────────────────────────────────────────────────────

def _benign_cases() -> list[dict[str, Any]]:
    """
    构造一组"绝对良性"的 Ring-0 输入 payload。

    每个 payload 模拟 Ring-3 top-k logprob 经 DCBF 过滤后的结果：
    - logits 为正值（候选分数高，不应被 argmin 拒绝）
    - topk_indices 连续（结构合法）
    - dcbf_margin > 0（势能满足 Theorem 2.2 前向不变性）
    - 不含任何 Deny verdicts（Judge Ensemble 全 Allow）

    若系统对这些输入返回 page_fault=true，说明存在"过度阻断"。
    """
    cases: list[dict[str, Any]] = []

    # Case 1: 单候选，高概率
    cases.append({
        "id": "single_high_logit",
        "payload": {
            "logits": [0.0],
            "topk_indices": [0],
            "trace_id": "bench-001",
            "policy_revision": 1,
            "dcbf_margin": 0.5,
        },
    })

    # Case 2: 五候选，均匀分布
    cases.append({
        "id": "five_uniform_candidates",
        "payload": {
            "logits": [-1.0, -2.0, -3.0, -4.0, -5.0],
            "topk_indices": [0, 1, 2, 3, 4],
            "trace_id": "bench-002",
            "policy_revision": 1,
            "dcbf_margin": 0.3,
        },
    })

    # Case 3: 二候选，logit 差异显著（argmin 应快速收敛）
    cases.append({
        "id": "two_candidates_sharp",
        "payload": {
            "logits": [0.0, -10.0],
            "topk_indices": [0, 1],
            "trace_id": "bench-003",
            "policy_revision": 1,
            "dcbf_margin": 0.8,
        },
    })

    # Case 4: 最大合法 top-k（k=20，与 DeepSeek API 上限对齐）
    top20_logits = [float(-i) for i in range(20)]
    cases.append({
        "id": "top20_max_k",
        "payload": {
            "logits": top20_logits,
            "topk_indices": list(range(20)),
            "trace_id": "bench-004",
            "policy_revision": 1,
            "dcbf_margin": 0.1,
        },
    })

    # Case 5: dcbf_margin 临界值（刚好满足 Theorem 2.2 > 0）
    cases.append({
        "id": "margin_boundary",
        "payload": {
            "logits": [-1.0, -2.0],
            "topk_indices": [0, 1],
            "trace_id": "bench-005",
            "policy_revision": 1,
            "dcbf_margin": 1e-7,  # 极小但正值
        },
    })

    # Case 6: 重复 payload（验证 Tier-1 缓存命中路径）
    repeated_payload = {
        "logits": [0.0, -1.0, -2.0],
        "topk_indices": [0, 1, 2],
        "trace_id": "bench-006",
        "policy_revision": 2,
        "dcbf_margin": 0.4,
    }
    cases.append({"id": "cache_cold_miss", "payload": repeated_payload.copy()})
    # 相同 trace_id 但二进制是独立进程，缓存在同一测试 session 内不跨进程持久；
    # 若实现为长运行服务，第二次调用应命中。
    cases.append({"id": "cache_warm_repeat", "payload": repeated_payload.copy()})

    # Case 7: 全负 logits（正常对数概率范围）
    cases.append({
        "id": "all_negative_logits",
        "payload": {
            "logits": [-0.1, -0.5, -1.2, -3.0],
            "topk_indices": [0, 1, 2, 3],
            "trace_id": "bench-007",
            "policy_revision": 1,
            "dcbf_margin": 0.25,
        },
    })

    # Case 8: 政策版本为 0（边界值）
    cases.append({
        "id": "policy_revision_zero",
        "payload": {
            "logits": [0.0, -1.0],
            "topk_indices": [0, 1],
            "trace_id": "bench-008",
            "policy_revision": 0,
            "dcbf_margin": 0.5,
        },
    })

    return cases


def _stress_cases() -> list[dict[str, Any]]:
    """
    压力用例：故意制造极端场景逼迫 QP 接近 4ms 临界点（在当前 O(k) argmin 实现中需 k 很大才触发）。
    UTILITY_INJECT_QP_STRESS=1 启用。
    """
    import random
    rng = random.Random(42)

    cases = []
    for i in range(8):
        k = 20  # 最大候选数
        logits = [rng.gauss(0, 2) for _ in range(k)]
        cases.append({
            "id": f"stress_k20_{i}",
            "payload": {
                "logits": logits,
                "topk_indices": list(range(k)),
                "trace_id": f"bench-stress-{i:03d}",
                "policy_revision": 1,
                "dcbf_margin": rng.uniform(0.05, 0.9),
            },
        })
    return cases


# ─────────────────────────────────────────────────────────────────────────────
# 执行单个 Ring-0 调用
# ─────────────────────────────────────────────────────────────────────────────

def _find_e2e_bin() -> Path:
    for sub in ("release", "debug"):
        p = KERNEL_ROOT / "target" / sub / "e2e_ring3_json"
        if p.is_file():
            return p
    raise FileNotFoundError(
        "e2e_ring3_json not built; run: cd decoupled-safety-kernel && cargo build --bin e2e_ring3_json"
    )


@dataclass
class CaseResult:
    case_id: str
    blocked: bool          # page_fault=true OR feasible=false
    page_fault: bool
    feasible: bool
    qp_budget_exceeded: bool
    hard_budget_exceeded: bool
    cache_hit: bool
    cache_key_hex: str
    qp_elapsed_us: int
    rust_total_us: int
    wall_ms: float
    error: str | None = None


def _run_case(case: dict[str, Any], exe: Path) -> CaseResult:
    payload_json = json.dumps(case["payload"])
    t0 = time.perf_counter()
    proc = subprocess.run(
        [str(exe), "-"],
        input=payload_json,
        text=True,
        capture_output=True,
        cwd=str(KERNEL_ROOT),
        check=False,
    )
    wall_ms = (time.perf_counter() - t0) * 1000.0

    if proc.returncode != 0:
        return CaseResult(
            case_id=case["id"],
            blocked=True,
            page_fault=True,
            feasible=False,
            qp_budget_exceeded=False,
            hard_budget_exceeded=False,
            cache_hit=False,
            cache_key_hex="",
            qp_elapsed_us=0,
            rust_total_us=0,
            wall_ms=wall_ms,
            error=f"exit={proc.returncode}: {(proc.stderr or proc.stdout)[:200]}",
        )

    try:
        out = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return CaseResult(
            case_id=case["id"],
            blocked=True,
            page_fault=True,
            feasible=False,
            qp_budget_exceeded=False,
            hard_budget_exceeded=False,
            cache_hit=False,
            cache_key_hex="",
            qp_elapsed_us=0,
            rust_total_us=0,
            wall_ms=wall_ms,
            error=f"JSON parse error: {exc}",
        )

    profile = out.get("profile", {})
    page_fault = bool(out.get("page_fault", False))
    feasible = bool(out.get("feasible", True))
    blocked = page_fault or (not feasible)

    return CaseResult(
        case_id=case["id"],
        blocked=blocked,
        page_fault=page_fault,
        feasible=feasible,
        qp_budget_exceeded=bool(profile.get("qp_budget_exceeded", False)),
        hard_budget_exceeded=bool(profile.get("hard_budget_exceeded", False)),
        cache_hit=bool(profile.get("cache_hit", False)),
        cache_key_hex=str(profile.get("cache_key_hex", "")),
        qp_elapsed_us=int(profile.get("qp_elapsed_us", 0)),
        rust_total_us=int(profile.get("rust_total_us", 0)),
        wall_ms=wall_ms,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 聚合度量
# ─────────────────────────────────────────────────────────────────────────────

def _aggregate(results: list[CaseResult]) -> dict[str, Any]:
    n = len(results)
    if n == 0:
        return {"error": "no results"}

    blocked = sum(1 for r in results if r.blocked)
    qp_timeout = sum(1 for r in results if r.qp_budget_exceeded)
    cache_hits = sum(1 for r in results if r.cache_hit)
    mean_qp = sum(r.qp_elapsed_us for r in results) / n
    mean_rust = sum(r.rust_total_us for r in results) / n

    return {
        "total_cases": n,
        "blocked_cases": blocked,
        "qp_timeout_cases": qp_timeout,
        "cache_hit_cases": cache_hits,
        "projection_over_blocking_rate": round(blocked / n, 6),
        "qp_timeout_rate": round(qp_timeout / n, 6),
        "cache_hit_rate": round(cache_hits / n, 6),
        "mean_qp_elapsed_us": round(mean_qp, 2),
        "mean_rust_total_us": round(mean_rust, 2),
        "per_case": [asdict(r) for r in results],
    }


# ─────────────────────────────────────────────────────────────────────────────
# 警报判定（首席科学家要求：过度阻断率必须 < 容忍阈值）
# ─────────────────────────────────────────────────────────────────────────────

# 工程底线：良性序列的过度阻断率不得超过此值。
OVER_BLOCKING_RATE_THRESHOLD = float(os.environ.get("UTILITY_OBR_THRESHOLD", "0.05"))
# QP 超时率底线（> 此值说明 warm-start 命中率过低，或负载过重）。
QP_TIMEOUT_RATE_THRESHOLD = float(os.environ.get("UTILITY_QP_TIMEOUT_THRESHOLD", "0.10"))


def _print_report(report: dict[str, Any]) -> None:
    obr = report["projection_over_blocking_rate"]
    qp_rate = report["qp_timeout_rate"]
    cache_rate = report["cache_hit_rate"]
    n = report["total_cases"]
    blocked = report["blocked_cases"]

    lines = [
        "",
        "=== Utility Benchmark — Projection Over-Blocking Report ===",
        f"  total_cases:                    {n}",
        f"  blocked (page_fault/infeasible): {blocked}",
        f"  projection_over_blocking_rate:  {obr:.4%}  "
        f"(threshold: {OVER_BLOCKING_RATE_THRESHOLD:.1%})",
        f"  qp_timeout_rate:                {qp_rate:.4%}  "
        f"(threshold: {QP_TIMEOUT_RATE_THRESHOLD:.1%})",
        f"  cache_hit_rate:                 {cache_rate:.4%}",
        f"  mean_qp_elapsed_us:             {report['mean_qp_elapsed_us']:.1f} µs",
        f"  mean_rust_total_us:             {report['mean_rust_total_us']:.1f} µs",
    ]

    if obr > OVER_BLOCKING_RATE_THRESHOLD:
        lines.append(
            f"  WARN: over-blocking rate {obr:.2%} > threshold {OVER_BLOCKING_RATE_THRESHOLD:.1%} — "
            "检查 dcbf_margin 参数或增大 warm-start 缓存容量 (RFC Gap 2)"
        )
    if qp_rate > QP_TIMEOUT_RATE_THRESHOLD:
        lines.append(
            f"  WARN: QP timeout rate {qp_rate:.2%} > {QP_TIMEOUT_RATE_THRESHOLD:.1%} — "
            "求解器频繁超时；可优化能量约束或升级为 Protobuf/gRPC 长连接服务（下一阶段）"
        )
    if cache_rate == 0.0:
        lines.append(
            "  INFO: cache_hit_rate=0 — 在当前子进程模型中每次调用都是冷启动；"
            "长运行服务模式下此率应 > 0"
        )

    lines.append(
        f"  QP_INNER_BUDGET (RFC §10): 4000 µs；"
        f"HARD_LATENCY_BUDGET: 20000 µs"
    )
    print("\n".join(lines), file=sys.stderr)


def main() -> int:
    exe = _find_e2e_bin()

    cases = _benign_cases()
    if os.environ.get("UTILITY_INJECT_QP_STRESS", "0") in ("1", "true", "yes"):
        cases += _stress_cases()
        print(
            json.dumps({"event": "stress_mode_enabled", "extra_cases": len(cases) - len(_benign_cases())}),
            file=sys.stderr,
        )

    results: list[CaseResult] = []
    for case in cases:
        r = _run_case(case, exe)
        results.append(r)

    report = _aggregate(results)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    _print_report(report)

    obr = report["projection_over_blocking_rate"]
    return 0 if obr <= OVER_BLOCKING_RATE_THRESHOLD else 1


if __name__ == "__main__":
    raise SystemExit(main())
