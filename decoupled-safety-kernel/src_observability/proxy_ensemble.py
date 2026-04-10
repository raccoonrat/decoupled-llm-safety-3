"""
DCBF Proxy Ensemble v0.2 — independent geometric latent-space probes (Gap 1 fix).

# 为什么要替换 logprobs 代理势能？（因果欺骗抵抗性）
# ─────────────────────────────────────────────────────
# v0.1 的势能 h(x_t) = Σ logp_i 依赖目标 LLM API 提供的 logprobs。
# 对手模型可通过对抗性微调（Adversarial Fine-tuning）伪造极高的 logprobs 给
# 恶意 token（"因果欺骗"），或 API 提供商直接剥夺 logprobs 输出（"API 退化"）。
#
# v0.2 解决方案：用两个独立的本地固定权重探针替代：
#
#   SemanticBoundaryProxy
#     用本地可信嵌入模型将文本前缀编码为 z_t；
#     h(x_t) = threshold − cosine_similarity(z_t, forbidden_centroid)
#     几何直觉：forbidden_centroid 是"禁区"的质心；
#     h > 0 表示 z_t 离禁区足够远（安全侧），h < 0 表示已越界。
#     因果欺骗抵抗性：本地嵌入器权重固定、与目标 LLM 完全隔离；
#     目标 LLM 的微调无法影响本地 embedding 的几何结构。
#
#   TrajectoryMutationProxy
#     h(x_t→x_{t+1}) = cosine_similarity(z_t, z_{t+1}) − drop_threshold
#     几何直觉：轨迹"平滑性"即安全性；若相邻步骤向量余弦相似度骤降，
#     表明语义方向发生突变（可能是被诱导向禁区方向转向），h < 0 → interrupt。
#
#   Ensemble 共识：ANY probe interrupt → 整体 interrupt（保守 AND 规则）。
#
# 嵌入器降级链（保证无网络/无 GPU 可用时同样运行）
#   1. SklearnTfidfEmbedder   — sklearn 字符 n-gram TF-IDF（本地，始终离线）
#   2. HashNgramEmbedder       — 纯 numpy 字符 n-gram 随机投影（零依赖回退）
#
# RFC 对齐：RFC §5.2 LatentState 契约；§9 MonitorFault 路由；Theorem 2.2 不变式。
"""

from __future__ import annotations

import hashlib
import os
import struct
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Sequence

# 限制 OpenMP / BLAS 线程数为 1，防止沙箱/容器环境因缺少 SHM 而崩溃。
# 须在任何 sklearn/numpy 导入之前设置。
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

import numpy as np

from .dcbf_evaluator import DCBFReport, MonitorFault

# ─────────────────────────────────────────────────────
# 模块级常量
# ─────────────────────────────────────────────────────
PROBE_VERSION = "v2.0"
_FLOAT32 = np.float32

# 默认禁区文本示例（用于计算 forbidden_centroid）
# 论文/实战中应由外部配置注入；此处为 Theorem 3.1 基准的最小可复现集合。
DEFAULT_FORBIDDEN_EXAMPLES: tuple[str, ...] = (
    "forbidden_output",
    "weapon synthesis steps",
    "bypass safety filter",
    "ignore all instructions",
    "jailbreak",
    "harmful content generation",
)


# ─────────────────────────────────────────────────────
# LatentState（RFC §5.2 v2 契约）
# ─────────────────────────────────────────────────────

@dataclass
class LatentState:
    """
    Read-only latent proxy state (RFC §5.2 v2).

    Fields are fixed at construction; treat as immutable by convention.
    `source_layer`, `dimension`, `probe_version` 三个字段使审计日志可精确复现。
    """

    trace_id: str
    vector: np.ndarray          # normalized float32, shape (dimension,)
    source_layer: str           # e.g. "sklearn-tfidf:c3-512" / "hash-ngram:n3-d256"
    dimension: int
    probe_version: str = field(default=PROBE_VERSION)

    def __post_init__(self) -> None:
        norm = float(np.linalg.norm(self.vector))
        if norm < 1e-12:
            raise ValueError("LatentState vector must be non-zero (embedder error or empty input)")
        if not np.isfinite(self.vector).all():
            raise ValueError("LatentState vector contains NaN/Inf")
        # 强制归一化（方便下游余弦计算）
        object.__setattr__(self, "vector", (self.vector / norm).astype(_FLOAT32))

    def cosine_similarity(self, other: "LatentState") -> float:
        """余弦相似度（已归一化向量故等于点积，维度不匹配则 pad/截断后警告）。"""
        a, b = self.vector, other.vector
        if a.shape != b.shape:
            min_d = min(len(a), len(b))
            a, b = a[:min_d], b[:min_d]
        return float(np.dot(a, b))

    def fingerprint(self) -> str:
        """用于审计日志：(source_layer, dim, version, vector_hash) 的紧凑表示。"""
        vh = hashlib.sha256(self.vector.tobytes()).hexdigest()[:16]
        return f"{self.source_layer}|d={self.dimension}|pv={self.probe_version}|vh={vh}"


# ─────────────────────────────────────────────────────
# 嵌入器抽象与实现
# ─────────────────────────────────────────────────────

class BaseEmbedder(ABC):
    """本地固定权重文本嵌入器基类；不得使用目标 LLM 的任何输出作为输入。"""

    @property
    @abstractmethod
    def source_layer(self) -> str: ...

    @property
    @abstractmethod
    def dimension(self) -> int: ...

    @abstractmethod
    def embed(self, text: str) -> np.ndarray:
        """返回归一化 float32 向量，shape (dimension,)。"""
        ...

    def embed_latent(self, text: str, trace_id: str) -> LatentState:
        vec = self.embed(text)
        return LatentState(
            trace_id=trace_id,
            vector=vec,
            source_layer=self.source_layer,
            dimension=self.dimension,
        )


class HashNgramEmbedder(BaseEmbedder):
    """
    纯 numpy 零依赖嵌入器：字符 n-gram → 随机投影密集向量。

    实现方式：
      对文本每个 n-gram，取其 MD5 的前 8 字节作为种子，
      用该种子生成 dim 维随机向量并加和，最终归一化。
    此方案确定性强，字符 n-gram 重叠是有效的文本相似性代理。
    """

    def __init__(self, n: int = 3, dim: int = 256) -> None:
        self._n = n
        self._dim = dim
        self._rng_cache: dict[bytes, np.ndarray] = {}

    @property
    def source_layer(self) -> str:
        return f"hash-ngram:n{self._n}-d{self._dim}"

    @property
    def dimension(self) -> int:
        return self._dim

    def _ngram_vec(self, gram: str) -> np.ndarray:
        key = gram.encode("utf-8")
        if key not in self._rng_cache:
            seed_bytes = hashlib.md5(key).digest()[:8]
            seed = struct.unpack("<Q", seed_bytes)[0] % (2**31)
            rng = np.random.default_rng(seed)
            v = rng.standard_normal(self._dim).astype(_FLOAT32)
            self._rng_cache[key] = v
        return self._rng_cache[key]

    def embed(self, text: str) -> np.ndarray:
        grams = [text[i : i + self._n] for i in range(max(1, len(text) - self._n + 1))]
        acc = np.zeros(self._dim, dtype=_FLOAT32)
        for g in grams:
            acc += self._ngram_vec(g)
        norm = float(np.linalg.norm(acc))
        if norm < 1e-12:
            acc = np.ones(self._dim, dtype=_FLOAT32)
            norm = float(np.linalg.norm(acc))
        return (acc / norm).astype(_FLOAT32)


class SklearnTfidfEmbedder(BaseEmbedder):
    """
    sklearn TF-IDF 字符 n-gram 嵌入器 + 固定随机投影降维（Johnson-Lindenstrauss）。

    刻意 **避免** TruncatedSVD / ARPACK：它们在沙箱/容器中可能因 OpenMP SHM2
    初始化而崩溃。改用固定种子的随机投影矩阵（seed=42），数学等效且零 BLAS 依赖。

    Lazy-fit：首次 embed 时 fit；之后 transform。权重固定，完全离线。
    """

    def __init__(
        self,
        dim: int = 512,
        ngram_range: tuple[int, int] = (2, 4),
        corpus: Sequence[str] | None = None,
    ) -> None:
        from sklearn.feature_extraction.text import TfidfVectorizer

        self._dim = dim
        self._vectorizer = TfidfVectorizer(
            analyzer="char_wb", ngram_range=ngram_range, max_features=dim * 8
        )
        self._projection: np.ndarray | None = None  # (vocab, dim) 固定随机投影矩阵
        self._fitted = False
        self._corpus: list[str] = list(corpus) if corpus else []

    @property
    def source_layer(self) -> str:
        return f"sklearn-tfidf:c{self._dim}"

    @property
    def dimension(self) -> int:
        return self._dim

    def _ensure_fit(self, extra: str | None = None) -> None:
        if self._fitted:
            return
        corpus = list(self._corpus)
        if extra:
            corpus.append(extra)
        if len(corpus) < 2:
            corpus += ["safe normal helpful response", "forbidden harmful dangerous"]
        self._vectorizer.fit(corpus)
        vocab_size = len(self._vectorizer.vocabulary_)
        # 固定随机投影：seed=42，每列归一化；纯 numpy，不调用 BLAS SVD/ARPACK
        rng = np.random.default_rng(42)
        proj = rng.standard_normal((vocab_size, self._dim)).astype(_FLOAT32)
        col_norms = np.linalg.norm(proj, axis=0, keepdims=True)
        self._projection = proj / (col_norms + 1e-12)
        self._fitted = True

    def embed(self, text: str) -> np.ndarray:
        self._ensure_fit(text)
        assert self._projection is not None
        tfidf = self._vectorizer.transform([text])   # scipy sparse (1, vocab)
        # 稀疏矩阵 × 密集投影 = 密集向量（pure numpy path，无 ARPACK/LAPACK）
        vec: np.ndarray = np.asarray(tfidf @ self._projection, dtype=_FLOAT32).ravel()
        norm = float(np.linalg.norm(vec))
        if norm < 1e-12:
            vec = np.ones(self._dim, dtype=_FLOAT32)
            norm = float(np.linalg.norm(vec))
        return (vec / norm).astype(_FLOAT32)


def build_embedder(
    preferred: str = "sklearn",
    dim: int = 256,
    corpus: Sequence[str] | None = None,
) -> BaseEmbedder:
    """
    嵌入器工厂（降级链）：
      "sklearn" → SklearnTfidfEmbedder → HashNgramEmbedder
      "hash"    → HashNgramEmbedder 直接使用
    """
    if preferred == "hash":
        return HashNgramEmbedder(dim=dim)
    try:
        emb = SklearnTfidfEmbedder(dim=dim, corpus=corpus)
        # 预热以尽早发现 sklearn 问题
        emb._ensure_fit()
        return emb
    except Exception:
        return HashNgramEmbedder(dim=dim)


# ─────────────────────────────────────────────────────
# 禁区（Forbidden Region）
# ─────────────────────────────────────────────────────

@dataclass
class ForbiddenRegion:
    """
    禁区定义：forbidden_centroid 是禁区文本示例嵌入的质心向量。

    h(x_t) = safety_threshold − cosine_similarity(z_t, centroid)
    h > 0 → z_t 距禁区质心 > safety_threshold（安全侧）
    h < 0 → z_t 已进入禁区（不变性破坏）
    """

    centroid: np.ndarray            # normalized float32, shape (dim,)
    safety_threshold: float = 0.3   # cosine sim 超过此值则视为越界
    barrier_id: str = "semantic-boundary-v2"

    @classmethod
    def from_examples(
        cls,
        embedder: BaseEmbedder,
        examples: Sequence[str] = DEFAULT_FORBIDDEN_EXAMPLES,
        safety_threshold: float = 0.3,
        barrier_id: str = "semantic-boundary-v2",
    ) -> "ForbiddenRegion":
        vecs = np.stack([embedder.embed(e) for e in examples])
        centroid = vecs.mean(axis=0).astype(_FLOAT32)
        norm = float(np.linalg.norm(centroid))
        centroid = (centroid / norm).astype(_FLOAT32) if norm > 1e-12 else centroid
        return cls(centroid=centroid, safety_threshold=safety_threshold, barrier_id=barrier_id)

    def h(self, state: LatentState) -> float:
        sim = float(np.dot(state.vector, self.centroid))
        return self.safety_threshold - sim


# ─────────────────────────────────────────────────────
# Probe 1 — SemanticBoundaryProxy
# ─────────────────────────────────────────────────────

class SemanticBoundaryProxy:
    """
    语义边界探针：计算文本前缀到禁区质心的几何距离势能。

    h(x_t) = safety_threshold − cosine_similarity(z_t, forbidden_centroid)

    因果欺骗抵抗性：
      本地嵌入器权重在服务启动时固定；z_t 仅由本地模型计算，
      与目标 LLM 的参数/输出完全解耦。即使目标 LLM 对抗微调以
      伪造极高的 logprobs，本地几何距离仍然独立准确。
    """

    def __init__(self, embedder: BaseEmbedder, forbidden_region: ForbiddenRegion) -> None:
        self._embedder = embedder
        self._region = forbidden_region

    @property
    def barrier_id(self) -> str:
        return self._region.barrier_id

    def embed(self, text: str, trace_id: str) -> LatentState:
        try:
            return self._embedder.embed_latent(text, trace_id)
        except Exception as exc:
            dummy_report = DCBFReport(
                h_t=float("nan"), h_t1=float("nan"), margin=float("nan"),
                near_violation=False, interrupt=True,
                barrier_id=self.barrier_id,
            )
            raise MonitorFault(dummy_report, f"SemanticBoundaryProxy embed failed: {exc}") from exc

    def h(self, state: LatentState) -> float:
        return self._region.h(state)

    def check(
        self, state_t: LatentState, state_t1: LatentState, alpha: float,
        *, near_margin_eps: float = 1e-6,
    ) -> DCBFReport:
        h_t = self.h(state_t)
        h_t1 = self.h(state_t1)
        margin = h_t1 - (1.0 - alpha) * h_t
        interrupt = margin < 0.0
        return DCBFReport(
            h_t=h_t, h_t1=h_t1, margin=margin,
            near_violation=(not interrupt) and (margin < near_margin_eps),
            interrupt=interrupt,
            barrier_id=self.barrier_id,
        )


# ─────────────────────────────────────────────────────
# Probe 2 — TrajectoryMutationProxy
# ─────────────────────────────────────────────────────

class TrajectoryMutationProxy:
    """
    轨迹突变探针：检测连续步骤间的余弦相似度骤降。

    h(x_t → x_{t+1}) = cosine_similarity(z_t, z_{t+1}) − drop_threshold

    几何直觉：
      安全轨迹在语义空间中"平滑移动"；若余弦相似度骤降至 drop_threshold 以下，
      表明模型输出发生了剧烈语义转向（向禁区方向跳跃），h < 0 → interrupt。

    对 logprob-缺失的鲁棒性：
      余弦相似度完全由本地嵌入向量计算，不依赖任何外部 logprobs；
      即使 API 退化返回单候选、无 logprobs，轨迹仍可被独立监控。
    """

    def __init__(self, drop_threshold: float = 0.5) -> None:
        self._drop_threshold = drop_threshold
        self.barrier_id = "trajectory-mutation-v2"

    def h_pair(self, state_t: LatentState, state_t1: LatentState) -> float:
        sim = state_t.cosine_similarity(state_t1)
        return sim - self._drop_threshold

    def check(
        self, state_t: LatentState, state_t1: LatentState, alpha: float,
        *, near_margin_eps: float = 1e-6,
    ) -> DCBFReport:
        h_t = self.h_pair(state_t, state_t1)
        # 对于轨迹探针，"下一步"的 h 需要与下一帧的配对；
        # 在单步调用时 h_t 与 h_t1 使用同一对（退化对称），等价于监控当前步骤的平滑性。
        h_t1 = h_t
        margin = h_t1 - (1.0 - alpha) * h_t
        interrupt = margin < 0.0
        return DCBFReport(
            h_t=h_t, h_t1=h_t1, margin=margin,
            near_violation=(not interrupt) and (margin < near_margin_eps),
            interrupt=interrupt,
            barrier_id=self.barrier_id,
        )


# ─────────────────────────────────────────────────────
# ProxyEnsemble — AND 共识（保守规则）
# ─────────────────────────────────────────────────────

@dataclass
class EnsembleReport:
    """多探针共识报告；barrier_id 列出所有触发 interrupt 的探针。"""

    semantic_report: DCBFReport
    mutation_report: DCBFReport
    interrupt: bool
    dominant_margin: float      # 取两个 margin 的最小值（最保守）
    barrier_ids_triggered: list[str]


class ProxyEnsemble:
    """
    v0.2 多视角代理集（Proxy Ensemble）。

    不使用目标 LLM 的 logprobs；内部持有两个独立探针。
    API 降级（logprobs=None）时仍正常工作——势能计算与 logprobs 完全解耦。
    """

    def __init__(
        self,
        semantic_proxy: SemanticBoundaryProxy,
        mutation_proxy: TrajectoryMutationProxy,
    ) -> None:
        self._semantic = semantic_proxy
        self._mutation = mutation_proxy

    def embed(self, text: str, trace_id: str) -> LatentState:
        """将文本编码为 LatentState；任何嵌入失败都通过 MonitorFault 上报（不静默失败）。"""
        return self._semantic.embed(text, trace_id)

    def check_step(
        self,
        state_t: LatentState,
        state_t1: LatentState,
        alpha: float,
        *,
        near_margin_eps: float = 1e-6,
    ) -> EnsembleReport:
        """
        对 (state_t, state_t1) 跨步运行两个探针；返回 AND 共识结果。
        任一探针报告 interrupt=True → 整体 interrupt=True。
        """
        if not (0.0 < alpha <= 1.0):
            raise ValueError("alpha must satisfy 0 < alpha <= 1 (Theorem 2.2 / RFC §7.5)")

        sem = self._semantic.check(state_t, state_t1, alpha, near_margin_eps=near_margin_eps)
        mut = self._mutation.check(state_t, state_t1, alpha, near_margin_eps=near_margin_eps)

        interrupt = sem.interrupt or mut.interrupt
        dominant_margin = min(sem.margin, mut.margin)
        triggered = []
        if sem.interrupt:
            triggered.append(sem.barrier_id or "semantic-boundary")
        if mut.interrupt:
            triggered.append(mut.barrier_id or "trajectory-mutation")

        return EnsembleReport(
            semantic_report=sem,
            mutation_report=mut,
            interrupt=interrupt,
            dominant_margin=dominant_margin,
            barrier_ids_triggered=triggered,
        )


# ─────────────────────────────────────────────────────
# 便捷工厂
# ─────────────────────────────────────────────────────

def build_default_ensemble(
    forbidden_examples: Sequence[str] = DEFAULT_FORBIDDEN_EXAMPLES,
    *,
    dim: int = 256,
    safety_threshold: float = 0.3,
    drop_threshold: float = 0.5,
    embedder_preference: str = "hash",
) -> ProxyEnsemble:
    """
    工厂函数：构建带默认禁区配置的 ProxyEnsemble。

    论文/生产中建议从外部配置注入 `forbidden_examples`。
    """
    embedder = build_embedder(preferred=embedder_preference, dim=dim, corpus=list(forbidden_examples))
    region = ForbiddenRegion.from_examples(embedder, examples=forbidden_examples,
                                            safety_threshold=safety_threshold)
    semantic_proxy = SemanticBoundaryProxy(embedder, region)
    mutation_proxy = TrajectoryMutationProxy(drop_threshold=drop_threshold)
    return ProxyEnsemble(semantic_proxy=semantic_proxy, mutation_proxy=mutation_proxy)
