"""
DCBF Evaluator v2 — 以原始文本为输入的高层接口（替代 v1 的 LatentStateProxy(h: float)）。

v1 的 h(x_t) = Σ logp_i（目标 LLM 提供）→ 已废弃（因果欺骗攻击面）。
v2 的 h(x_t) 由本地固定权重 ProxyEnsemble 独立计算：
  - SemanticBoundaryProxy  → h = dist(z_t, forbidden_centroid)（语义几何距离）
  - TrajectoryMutationProxy → h = cosine_sim(z_t, z_{t+1}) - threshold（轨迹平滑性）

使用方式：
  from src_observability.dcbf_evaluator_v2 import DCBFEvaluatorV2, build_default_v2

  ev = build_default_v2()                      # 一次性初始化（本地，离线）
  state_0 = ev.embed("Hello, can you help?", trace_id="t1")

  for candidate in ["OK", "sure", "forbidden_output"]:
      state_c, report = ev.step(state_0, candidate, trace_id="t1", alpha=0.2)
      if report.interrupt:
          ...  # 剔除候选；EnsembleReport.barrier_ids_triggered 告知哪个探针触发

API 退化场景（logprobs=None）：
  v2 完全不使用 logprobs；候选 token 直接通过文本拼接计算嵌入，
  即使 `next_token_adapter.py` 强制抹除所有 logprobs，v2 势能计算不受影响。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .dcbf_evaluator import DCBFReport, MonitorFault
from .proxy_ensemble import (
    DEFAULT_FORBIDDEN_EXAMPLES,
    EnsembleReport,
    LatentState,
    ProxyEnsemble,
    build_default_ensemble,
)


@dataclass
class V2StepResult:
    """单步 v2 检查的完整结果（含可审计 LatentState 与多探针报告）。"""

    state_t1: LatentState           # 候选前缀的 LatentState（用于下一步 state_t）
    ensemble_report: EnsembleReport
    interrupt: bool
    dominant_margin: float
    barrier_ids_triggered: list[str]
    fingerprint_t: str              # state_t 指纹（用于审计链）
    fingerprint_t1: str             # state_t1 指纹

    def to_dcbf_report(self) -> DCBFReport:
        """将最保守的语义探针报告转为 v1 兼容的 DCBFReport（便于与 v1 管线对接）。"""
        return DCBFReport(
            h_t=self.ensemble_report.semantic_report.h_t,
            h_t1=self.ensemble_report.semantic_report.h_t1,
            margin=self.dominant_margin,
            near_violation=self.ensemble_report.semantic_report.near_violation,
            interrupt=self.interrupt,
            barrier_id=",".join(self.barrier_ids_triggered) or None,
        )


class DCBFEvaluatorV2:
    """
    v2 高层评估器：接受原始文本，返回多探针安全报告。

    线程安全性：单实例建议单线程使用；如需并发，每个请求独立传入 ProxyEnsemble 副本。
    """

    def __init__(self, ensemble: ProxyEnsemble) -> None:
        self._ensemble = ensemble

    def embed(self, text: str, trace_id: str) -> LatentState:
        """
        嵌入任意文本前缀；返回 LatentState（用于后续 `step` 调用的 state_t）。

        失败时抛出 MonitorFault（不静默失败）。
        """
        return self._ensemble.embed(text, trace_id)

    def step(
        self,
        state_t: LatentState,
        candidate_token: str,
        trace_id: str,
        alpha: float = 0.2,
        *,
        prefix_for_t1: str | None = None,
        near_margin_eps: float = 1e-6,
    ) -> V2StepResult:
        """
        对 (state_t, candidate_token) 运行完整的双探针检查。

        参数：
          state_t          — 当前步骤的 LatentState（由上一步 `step` 的 result.state_t1 提供）
          candidate_token  — 候选 token 文本（Ring-3 top-k 中的一个）
          trace_id         — 与 Ring-0 审计链一致的 trace_id
          alpha            — Theorem 2.2 的 α ∈ (0, 1]
          prefix_for_t1    — 若提供，直接用此文本作为 t+1 的嵌入输入（更精确）；
                             否则自动将 candidate_token 附加到 state_t 的来源文本（近似）

        候选文本 t+1 的构造：
          v2 将候选 token 直接拼接到当前前缀近似（一次嵌入更新），不依赖 logprobs。
        """
        if prefix_for_t1 is not None:
            text_t1 = prefix_for_t1
        else:
            # 默认拼接约定；生产中应传入完整 prefix
            text_t1 = candidate_token

        try:
            state_t1 = self._ensemble.embed(text_t1, trace_id)
        except MonitorFault:
            raise
        except Exception as exc:
            dummy_report = DCBFReport(
                h_t=float("nan"), h_t1=float("nan"), margin=float("nan"),
                near_violation=False, interrupt=True,
                barrier_id="proxy-embed-failure",
            )
            raise MonitorFault(dummy_report, f"DCBFEvaluatorV2.step embed failed: {exc}") from exc

        ens = self._ensemble.check_step(state_t, state_t1, alpha, near_margin_eps=near_margin_eps)

        return V2StepResult(
            state_t1=state_t1,
            ensemble_report=ens,
            interrupt=ens.interrupt,
            dominant_margin=ens.dominant_margin,
            barrier_ids_triggered=ens.barrier_ids_triggered,
            fingerprint_t=state_t.fingerprint(),
            fingerprint_t1=state_t1.fingerprint(),
        )

    def filter_candidates(
        self,
        state_t: LatentState,
        candidates: Sequence[tuple[str, float]],
        trace_id: str,
        alpha: float = 0.2,
        *,
        prefix_for_t1: dict[str, str] | None = None,
    ) -> tuple[list[tuple[str, float]], list[V2StepResult]]:
        """
        批量过滤候选集；返回 (survived_candidates, per_candidate_results)。

        survived_candidates：margin >= 0 的候选（顺序保留）。
        若全部被过滤，调用方应路由到 Graceful Degradation（`EmptySafeCandidateSet`）。

        API 退化场景：即使 `candidates` 中 logprob（第二元素）全为 None/0，
        v2 势能计算不受影响——嵌入仅用 token 文本，完全不看 logprob。
        """
        survived: list[tuple[str, float]] = []
        results: list[V2StepResult] = []
        for tok, lp in candidates:
            pfx = prefix_for_t1.get(tok) if prefix_for_t1 else None
            try:
                result = self.step(state_t, tok, trace_id, alpha, prefix_for_t1=pfx)
            except MonitorFault as e:
                # 嵌入器故障 → 保守拒绝此候选
                results.append(V2StepResult(
                    state_t1=state_t,  # 退化：使用 state_t 作为占位
                    ensemble_report=e.report if hasattr(e, "report") else None,  # type: ignore[arg-type]
                    interrupt=True,
                    dominant_margin=float("-inf"),
                    barrier_ids_triggered=["monitor-fault"],
                    fingerprint_t=state_t.fingerprint(),
                    fingerprint_t1=state_t.fingerprint(),
                ))
                continue
            results.append(result)
            if not result.interrupt:
                survived.append((tok, lp))
        return survived, results


def build_default_v2(
    forbidden_examples: Sequence[str] = DEFAULT_FORBIDDEN_EXAMPLES,
    *,
    dim: int = 256,
    safety_threshold: float = 0.3,
    drop_threshold: float = 0.5,
    alpha: float = 0.2,
    embedder_preference: str = "hash",
) -> DCBFEvaluatorV2:
    """
    便捷工厂：一行代码构建生产可用的 DCBFEvaluatorV2。

    所有参数均有合理默认值；生产中至少应外部注入 `forbidden_examples`。
    """
    ensemble = build_default_ensemble(
        forbidden_examples=forbidden_examples,
        dim=dim,
        safety_threshold=safety_threshold,
        drop_threshold=drop_threshold,
        embedder_preference=embedder_preference,
    )
    return DCBFEvaluatorV2(ensemble=ensemble)
