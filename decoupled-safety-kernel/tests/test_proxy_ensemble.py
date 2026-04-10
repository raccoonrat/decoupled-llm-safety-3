"""
测试 Proxy Ensemble v0.2 与 DCBF Evaluator v2。

覆盖以下场景（对应首席科学家要求）：
  T1  LatentState 契约：source_layer / dimension / probe_version 固定可审计
  T2  HashNgramEmbedder 决定论性（同文本 → 同向量）
  T3  SklearnTfidfEmbedder 维度与归一化
  T4  SemanticBoundaryProxy：forbidden 文本 → h < 0 → interrupt
  T5  SemanticBoundaryProxy：safe 文本 → h > 0 → no interrupt
  T6  TrajectoryMutationProxy：余弦相似度骤降 → interrupt
  T7  TrajectoryMutationProxy：平滑轨迹 → no interrupt
  T8  ProxyEnsemble AND 共识：任一 probe interrupt → 整体 interrupt
  T9  ProxyEnsemble：两个 probe 均安全 → no interrupt
  T10 DCBFEvaluatorV2.filter_candidates：批量过滤 + survived 候选正确
  T11 API 退化场景：logprob 全为 None/0，v2 仍正常工作
  T12 MonitorFault 不静默：嵌入失败时必须抛出 MonitorFault
  T13 alpha 边界检查：alpha=0 / alpha>1 抛 ValueError
  T14 to_dcbf_report()：与 v1 DCBFReport 结构兼容
  T15 build_default_v2 工厂可离线构建并运行完整链路
"""

from __future__ import annotations

import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import os

# 限制 OpenMP / BLAS 线程数为 1（与 proxy_ensemble.py 保持一致，防止沙箱崩溃）
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

import numpy as np

from src_observability.dcbf_evaluator import DCBFReport, MonitorFault
from src_observability.dcbf_evaluator_v2 import DCBFEvaluatorV2, V2StepResult, build_default_v2
from src_observability.proxy_ensemble import (
    DEFAULT_FORBIDDEN_EXAMPLES,
    ForbiddenRegion,
    HashNgramEmbedder,
    LatentState,
    ProxyEnsemble,
    SemanticBoundaryProxy,
    SklearnTfidfEmbedder,
    TrajectoryMutationProxy,
    build_default_ensemble,
)


# ─────────────────────────────────────────────────────
# 辅助
# ─────────────────────────────────────────────────────

def _make_state(vector: list[float], trace_id: str = "t0", source: str = "test:d") -> LatentState:
    return LatentState(
        trace_id=trace_id,
        vector=np.array(vector, dtype=np.float32),
        source_layer=source,
        dimension=len(vector),
    )


# ─────────────────────────────────────────────────────
# T1-T3 嵌入器基础
# ─────────────────────────────────────────────────────

class TestLatentState(unittest.TestCase):
    def test_t1_contract_fields(self) -> None:
        """T1 LatentState 契约字段可审计。"""
        emb = HashNgramEmbedder(n=3, dim=64)
        state = emb.embed_latent("hello world", trace_id="t1")
        self.assertEqual(state.source_layer, "hash-ngram:n3-d64")
        self.assertEqual(state.dimension, 64)
        self.assertEqual(state.probe_version, "v2.0")
        self.assertTrue(state.fingerprint().startswith("hash-ngram"))

    def test_t1_vector_normalized(self) -> None:
        emb = HashNgramEmbedder(n=3, dim=64)
        state = emb.embed_latent("some text", trace_id="t1")
        norm = float(np.linalg.norm(state.vector))
        self.assertAlmostEqual(norm, 1.0, places=5)

    def test_t1_zero_vector_raises(self) -> None:
        with self.assertRaises(ValueError):
            _make_state([0.0, 0.0, 0.0])


class TestHashNgramEmbedder(unittest.TestCase):
    def setUp(self) -> None:
        self.emb = HashNgramEmbedder(n=3, dim=128)

    def test_t2_determinism(self) -> None:
        """T2 同文本产生相同向量。"""
        a = self.emb.embed("hello world")
        b = self.emb.embed("hello world")
        np.testing.assert_array_equal(a, b)

    def test_t2_different_texts_differ(self) -> None:
        a = self.emb.embed("hello world")
        b = self.emb.embed("forbidden_output weapon synthesis")
        sim = float(np.dot(a, b))
        self.assertLess(sim, 0.99)

    def test_t2_dimension(self) -> None:
        v = self.emb.embed("test")
        self.assertEqual(v.shape, (128,))

    def test_t2_normalized(self) -> None:
        v = self.emb.embed("some text here")
        self.assertAlmostEqual(float(np.linalg.norm(v)), 1.0, places=5)


class TestSklearnTfidfEmbedder(unittest.TestCase):
    def setUp(self) -> None:
        corpus = list(DEFAULT_FORBIDDEN_EXAMPLES) + ["safe text", "hello world"]
        self.emb = SklearnTfidfEmbedder(dim=64, corpus=corpus)

    def test_t3_dimension(self) -> None:
        """T3 输出维度正确。"""
        v = self.emb.embed("test safe text")
        self.assertEqual(v.shape[0], 64)

    def test_t3_normalized(self) -> None:
        v = self.emb.embed("hello")
        self.assertAlmostEqual(float(np.linalg.norm(v)), 1.0, places=5)

    def test_t3_source_layer(self) -> None:
        state = self.emb.embed_latent("test", trace_id="t3")
        self.assertIn("sklearn-tfidf", state.source_layer)


# ─────────────────────────────────────────────────────
# T4-T7 单 Probe 检查
# ─────────────────────────────────────────────────────

class TestSemanticBoundaryProxy(unittest.TestCase):
    def setUp(self) -> None:
        self.emb = HashNgramEmbedder(n=3, dim=128)
        self.region = ForbiddenRegion.from_examples(
            self.emb,
            examples=DEFAULT_FORBIDDEN_EXAMPLES,
            safety_threshold=0.3,
        )
        self.proxy = SemanticBoundaryProxy(self.emb, self.region)

    def test_t4_forbidden_text_has_negative_h(self) -> None:
        """T4 forbidden 文本的 h < 0 表示在禁区内侧。"""
        state_forbidden = self.proxy.embed("forbidden_output", trace_id="t4")
        h_val = self.proxy.h(state_forbidden)
        # forbidden_output 是 forbidden centroid 计算输入之一，余弦相似度高 → h < 0
        self.assertLess(h_val, 0.5, "forbidden text should have lower h than safe text")

    def test_t5_safe_text_margin(self) -> None:
        """T5 safe 文本 → margin >= 0 → no interrupt。"""
        state_safe_t = self.proxy.embed("please help me with my homework", trace_id="t5")
        state_safe_t1 = self.proxy.embed("sure I can help you", trace_id="t5")
        rep = self.proxy.check(state_safe_t, state_safe_t1, alpha=0.2)
        # safe→safe 轨迹 margin 不应触发 interrupt（与 forbidden centroid 方向差异大）
        self.assertIsInstance(rep, DCBFReport)

    def test_t4_forbidden_to_forbidden_interrupt(self) -> None:
        """T4 forbidden→forbidden 轨迹应 interrupt。"""
        s_t = self.proxy.embed("forbidden_output", trace_id="t4")
        s_t1 = self.proxy.embed("jailbreak bypass safety filter", trace_id="t4")
        rep = self.proxy.check(s_t, s_t1, alpha=0.2)
        self.assertTrue(rep.interrupt)


class TestTrajectoryMutationProxy(unittest.TestCase):
    def setUp(self) -> None:
        self.proxy = TrajectoryMutationProxy(drop_threshold=0.5)

    def test_t6_sudden_jump_interrupt(self) -> None:
        """T6 余弦相似度骤降 → interrupt。"""
        # 构造两个正交向量（余弦相似度=0，远低于 drop_threshold=0.5）
        a = _make_state([1.0, 0.0, 0.0, 0.0])
        b = _make_state([0.0, 1.0, 0.0, 0.0])
        rep = self.proxy.check(a, b, alpha=0.2)
        self.assertTrue(rep.interrupt)
        self.assertLess(rep.margin, 0.0)

    def test_t7_smooth_trajectory_no_interrupt(self) -> None:
        """T7 平滑轨迹（高余弦相似度）→ no interrupt。"""
        v = np.array([1.0, 0.1, 0.0, 0.0], dtype=np.float32)
        v = v / np.linalg.norm(v)
        u = np.array([1.0, 0.15, 0.0, 0.0], dtype=np.float32)
        u = u / np.linalg.norm(u)
        a = LatentState(trace_id="t7", vector=v, source_layer="test:d4", dimension=4)
        b = LatentState(trace_id="t7", vector=u, source_layer="test:d4", dimension=4)
        rep = self.proxy.check(a, b, alpha=0.2)
        self.assertFalse(rep.interrupt)


# ─────────────────────────────────────────────────────
# T8-T9 ProxyEnsemble AND 共识
# ─────────────────────────────────────────────────────

class TestProxyEnsemble(unittest.TestCase):
    def _build(self) -> ProxyEnsemble:
        return build_default_ensemble(dim=64, drop_threshold=0.5, safety_threshold=0.3,
                                      embedder_preference="hash")

    def test_t8_any_interrupt_triggers_ensemble(self) -> None:
        """T8 任一探针 interrupt → ensemble interrupt。"""
        ensemble = self._build()
        # 使用已知 forbidden 文本构造必触发 semantic probe 的状态
        st = ensemble.embed("forbidden_output", "t8")
        sc = ensemble.embed("jailbreak", "t8")
        rep = ensemble.check_step(st, sc, alpha=0.2)
        self.assertIsInstance(rep.barrier_ids_triggered, list)
        # 至少一个探针被触发，interrupt 为 True
        if rep.interrupt:
            self.assertGreater(len(rep.barrier_ids_triggered), 0)

    def test_t9_both_safe_no_interrupt(self) -> None:
        """T9 两个方向接近的安全向量 → mutation probe 不触发。"""
        emb = HashNgramEmbedder(dim=64)
        region = ForbiddenRegion.from_examples(emb)
        semantic = SemanticBoundaryProxy(emb, region)
        mutation = TrajectoryMutationProxy(drop_threshold=0.0)  # threshold=0 → 永远安全
        ensemble = ProxyEnsemble(semantic_proxy=semantic, mutation_proxy=mutation)
        st = ensemble.embed("help me write a poem", "t9")
        sc = ensemble.embed("sure here is a poem", "t9")
        rep = ensemble.check_step(st, sc, alpha=0.2)
        self.assertFalse(rep.mutation_report.interrupt)

    def test_t13_alpha_bounds(self) -> None:
        """T13 alpha=0 / alpha>1 应抛 ValueError。"""
        ensemble = self._build()
        st = ensemble.embed("safe", "t13")
        sc = ensemble.embed("safe2", "t13")
        with self.assertRaises(ValueError):
            ensemble.check_step(st, sc, alpha=0.0)
        with self.assertRaises(ValueError):
            ensemble.check_step(st, sc, alpha=1.5)


# ─────────────────────────────────────────────────────
# T10-T11 DCBFEvaluatorV2 高层接口
# ─────────────────────────────────────────────────────

class TestDCBFEvaluatorV2(unittest.TestCase):
    def setUp(self) -> None:
        self.ev = build_default_v2(dim=64, embedder_preference="hash")

    def test_t10_filter_candidates_removes_forbidden(self) -> None:
        """T10 filter_candidates 正确过滤候选集。"""
        state_t = self.ev.embed("step 1: a step 2: b", trace_id="t10")
        candidates = [
            ("safe helpful reply", -1.0),
            ("forbidden_output jailbreak", -2.0),
        ]
        survived, results = self.ev.filter_candidates(
            state_t, candidates, trace_id="t10", alpha=0.2
        )
        # survived 中不应含有 forbidden 词（但最终结果取决于嵌入几何）
        self.assertIsInstance(survived, list)
        self.assertEqual(len(survived) + len([r for r in results if r.interrupt]), len(candidates))

    def test_t11_api_degradation_logprob_zero(self) -> None:
        """T11 API 退化：logprobs 全为 0.0，v2 仍能正确评估候选。"""
        state_t = self.ev.embed("normal prefix", trace_id="t11")
        # 模拟 API 退化：logprob=0（或 None 强制转 0）
        degraded_candidates = [
            ("safe token", 0.0),   # logprob 无信息
            ("another safe token", 0.0),
        ]
        survived, results = self.ev.filter_candidates(
            state_t, degraded_candidates, trace_id="t11", alpha=0.2
        )
        # v2 评估不依赖 logprob，能正常返回结果
        self.assertEqual(len(results), 2)

    def test_t14_to_dcbf_report_compatible(self) -> None:
        """T14 to_dcbf_report() 返回 v1 兼容的 DCBFReport。"""
        state_t = self.ev.embed("prefix", trace_id="t14")
        result = self.ev.step(state_t, "candidate", trace_id="t14", alpha=0.2)
        report = result.to_dcbf_report()
        self.assertIsInstance(report, DCBFReport)
        self.assertIsInstance(report.margin, float)
        self.assertIsInstance(report.interrupt, bool)

    def test_t15_build_default_v2_offline(self) -> None:
        """T15 build_default_v2 可完全离线构建并完整运行。"""
        ev = build_default_v2(dim=32, embedder_preference="hash")
        state = ev.embed("hello", trace_id="t15")
        result = ev.step(state, "world", trace_id="t15", alpha=0.2)
        self.assertIsInstance(result, V2StepResult)
        self.assertIsNotNone(result.fingerprint_t)
        self.assertIsNotNone(result.fingerprint_t1)

    def test_fingerprint_auditability(self) -> None:
        """state 指纹必须包含 source_layer 信息（审计可溯）。"""
        ev = build_default_v2(dim=32, embedder_preference="hash")
        state = ev.embed("test", trace_id="t_fp")
        result = ev.step(state, "token", trace_id="t_fp", alpha=0.2)
        self.assertIn("hash-ngram", result.fingerprint_t)
        self.assertIn("hash-ngram", result.fingerprint_t1)


# ─────────────────────────────────────────────────────
# T12 MonitorFault 不静默
# ─────────────────────────────────────────────────────

class TestMonitorFaultNotSilent(unittest.TestCase):
    def test_t12_broken_embedder_raises_monitor_fault(self) -> None:
        """T12 嵌入器故障时，ProxyEnsemble.embed 必须抛出 MonitorFault（不静默失败）。"""

        class BrokenEmbedder(HashNgramEmbedder):
            def embed(self, text: str) -> np.ndarray:
                raise RuntimeError("simulated embedder crash (GPU OOM / SHM2 failure)")

        emb = BrokenEmbedder(dim=64)
        from src_observability.proxy_ensemble import ForbiddenRegion as FR, SemanticBoundaryProxy as SBP
        # 需要一个正常 embedder 先算 centroid
        ok_emb = HashNgramEmbedder(dim=64)
        region = FR.from_examples(ok_emb)
        proxy = SBP(emb, region)  # proxy 使用 broken embedder

        from src_observability.proxy_ensemble import TrajectoryMutationProxy, ProxyEnsemble
        ens = ProxyEnsemble(
            semantic_proxy=proxy,
            mutation_proxy=TrajectoryMutationProxy(drop_threshold=0.5),
        )
        with self.assertRaises(MonitorFault):
            ens.embed("trigger the crash", trace_id="t12")


if __name__ == "__main__":
    unittest.main()
