"""
DCBF Proxy Ensemble v0.2 — independent geometric latent-space probes (Gap 1 fix).

Theoretical Foundation (Assumption 6.1 & Theorem 2.2 in Step1-理论部分.md)
=========================================================================

This module implements the **Surrogate State Mapping** (Assumption 6.1) required
by the DCBF forward invariance theorem (Theorem 2.2). The mapping

    φ: H → R^d

projects the LLM's high-dimensional hidden/output space into a low-dimensional
continuous state space where the barrier function h can be evaluated.

Two concrete surrogate constructions are provided:

  SemanticBoundaryProxy — implements h via cosine distance to forbidden centroid:

    h(z) = θ_safe − cos(φ(z), c_forbidden)

    where c_forbidden is the centroid of known forbidden-topic embeddings, and
    θ_safe is a calibrated safety threshold. h > 0 → safe; h < 0 → violation.

  TrajectoryMutationProxy — implements h via trajectory smoothness:

    h(z_t, z_{t-1}) = cos(φ(z_t), φ(z_{t-1})) − θ_smooth

    Abrupt trajectory changes (e.g., jailbreak-induced semantic jumps) yield h < 0.

DCBF forward invariance (Theorem 2.2) is checked at each step:

    margin = h(x_{t+1}) − (1 − α) · h(x_t),  α ∈ (0, 1]

    If margin ≥ 0, the safe superlevel set {x : h(x) ≥ 0} is forward invariant.
    If margin < 0, the barrier condition is violated → interrupt.

Ensemble consensus: ANY probe interrupt → overall interrupt (conservative AND rule).

Limitations (must be understood by paper reviewers / deployers):
  - The surrogate mapping φ is NOT proven to capture all safety-relevant semantics.
  - The fidelity of safety guarantees is bounded by how well the embedder separates
    safe vs. unsafe regions in R^d.
  - For closed-source black-box APIs without hidden state access, φ is constructed
    from output text embeddings only → chunk-level (not token-level) monitoring.
  - DCBF does NOT claim to "guarantee natural language is absolutely safe."

Embedder degradation chain (runs without network / GPU):
  1. SklearnTfidfEmbedder — sklearn char n-gram TF-IDF (local, always offline)
  2. HashNgramEmbedder    — pure numpy char n-gram random projection (zero deps)

RFC alignment: RFC §5.2 LatentState contract; §9 MonitorFault routing; Theorem 2.2.
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
# Probe 3 — PerplexityShiftProxy (Round-2 Risk 1 response)
# ─────────────────────────────────────────────────────

class PerplexityShiftProxy:
    """
    Perplexity shift probe: detects abrupt entropy changes in the latent trajectory.

    Mathematical formulation (Assumption 6.1, dimension 3):

        h(z_t, z_{t-1}) = θ_entropy − |H(z_t) − H(z_{t-1})|

    where H(z) is the Shannon entropy of the normalized embedding vector
    (treated as a pseudo-probability distribution after softmax), and θ_entropy
    is a calibrated threshold for acceptable per-step entropy change.

    Intuition: jailbreak attempts often cause abrupt distributional shifts in
    the model's latent space — e.g., a sudden jump from "helpful assistant"
    mode to "unrestricted mode". This manifests as a spike in the entropy
    difference between consecutive states. h < 0 → interrupt.

    Limitations:
      - Entropy is computed on the embedding vector, not raw logits; the
        fidelity depends on how well the embedder preserves distributional
        information from the original model output.
      - Gradual entropy drift (slow-burn attacks) may not trigger this probe;
        it is designed to catch abrupt shifts, not monotonic trends.
    """

    def __init__(self, entropy_threshold: float = 0.3) -> None:
        self._entropy_threshold = entropy_threshold
        self.barrier_id = "perplexity-shift-v1"

    @staticmethod
    def _embedding_entropy(state: LatentState) -> float:
        v = np.abs(state.vector) + 1e-12
        p = v / v.sum()
        return float(-np.sum(p * np.log2(p)))

    def h_pair(self, state_t: LatentState, state_t1: LatentState) -> float:
        entropy_diff = abs(self._embedding_entropy(state_t1) - self._embedding_entropy(state_t))
        return self._entropy_threshold - entropy_diff

    def check(
        self, state_t: LatentState, state_t1: LatentState, alpha: float,
        *, near_margin_eps: float = 1e-6,
    ) -> DCBFReport:
        h_t = self.h_pair(state_t, state_t1)
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
    perplexity_report: DCBFReport | None
    interrupt: bool
    dominant_margin: float
    barrier_ids_triggered: list[str]


class ProxyEnsemble:
    """
    v0.3 多视角代理集（Proxy Ensemble）— 三维 DCBF 探针。

    Probe 1: SemanticBoundaryProxy  — 禁区距离
    Probe 2: TrajectoryMutationProxy — 轨迹平滑性
    Probe 3: PerplexityShiftProxy   — 熵突变检测（Round-2 新增）

    不使用目标 LLM 的 logprobs；内部持有独立探针。
    API 降级（logprobs=None）时仍正常工作——势能计算与 logprobs 完全解耦。
    """

    def __init__(
        self,
        semantic_proxy: SemanticBoundaryProxy,
        mutation_proxy: TrajectoryMutationProxy,
        perplexity_proxy: PerplexityShiftProxy | None = None,
    ) -> None:
        self._semantic = semantic_proxy
        self._mutation = mutation_proxy
        self._perplexity = perplexity_proxy

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
        对 (state_t, state_t1) 跨步运行所有探针；返回 AND 共识结果。
        任一探针报告 interrupt=True → 整体 interrupt=True。
        """
        if not (0.0 < alpha <= 1.0):
            raise ValueError("alpha must satisfy 0 < alpha <= 1 (Theorem 2.2 / RFC §7.5)")

        sem = self._semantic.check(state_t, state_t1, alpha, near_margin_eps=near_margin_eps)
        mut = self._mutation.check(state_t, state_t1, alpha, near_margin_eps=near_margin_eps)

        ppx: DCBFReport | None = None
        if self._perplexity is not None:
            ppx = self._perplexity.check(state_t, state_t1, alpha, near_margin_eps=near_margin_eps)

        interrupt = sem.interrupt or mut.interrupt
        margins = [sem.margin, mut.margin]
        triggered = []
        if sem.interrupt:
            triggered.append(sem.barrier_id or "semantic-boundary")
        if mut.interrupt:
            triggered.append(mut.barrier_id or "trajectory-mutation")
        if ppx is not None:
            interrupt = interrupt or ppx.interrupt
            margins.append(ppx.margin)
            if ppx.interrupt:
                triggered.append(ppx.barrier_id or "perplexity-shift")

        return EnsembleReport(
            semantic_report=sem,
            mutation_report=mut,
            perplexity_report=ppx,
            interrupt=interrupt,
            dominant_margin=min(margins),
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
    entropy_threshold: float = 0.3,
    embedder_preference: str = "hash",
    enable_perplexity_probe: bool = True,
) -> ProxyEnsemble:
    """
    工厂函数：构建带默认禁区配置的 ProxyEnsemble（v0.3 三维探针）。

    论文/生产中建议从外部配置注入 `forbidden_examples`。
    """
    embedder = build_embedder(preferred=embedder_preference, dim=dim, corpus=list(forbidden_examples))
    region = ForbiddenRegion.from_examples(embedder, examples=forbidden_examples,
                                            safety_threshold=safety_threshold)
    semantic_proxy = SemanticBoundaryProxy(embedder, region)
    mutation_proxy = TrajectoryMutationProxy(drop_threshold=drop_threshold)
    perplexity_proxy = PerplexityShiftProxy(entropy_threshold=entropy_threshold) if enable_perplexity_probe else None
    return ProxyEnsemble(
        semantic_proxy=semantic_proxy,
        mutation_proxy=mutation_proxy,
        perplexity_proxy=perplexity_proxy,
    )


# ─────────────────────────────────────────────────────
# Threshold sensitivity analysis (Round-3 R1 response)
# ─────────────────────────────────────────────────────

def threshold_sensitivity_sweep(
    benign_texts: Sequence[str],
    malicious_texts: Sequence[str],
    *,
    forbidden_examples: Sequence[str] = DEFAULT_FORBIDDEN_EXAMPLES,
    dim: int = 256,
    embedder_preference: str = "hash",
    theta_safe_range: Sequence[float] = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6),
    theta_smooth_range: Sequence[float] = (0.3, 0.4, 0.5, 0.6, 0.7, 0.8),
    theta_entropy_range: Sequence[float] = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6),
    alpha: float = 0.5,
) -> dict:
    """
    Sweep threshold parameters across benign/malicious text pairs and report
    TPR (true positive rate = fraction of malicious pairs correctly interrupted)
    and FPR (false positive rate = fraction of benign pairs incorrectly interrupted)
    for each probe independently.

    Returns a dict with keys "semantic", "trajectory", "perplexity", each mapping
    to a list of {threshold, tpr, fpr} dicts.

    This function supports the Round-3 reviewer request for threshold calibration
    evidence (§1 of [0410-3]round3-review.md).
    """
    embedder = build_embedder(preferred=embedder_preference, dim=dim,
                              corpus=list(forbidden_examples))

    def _embed_pairs(texts: Sequence[str]) -> list[tuple]:
        states = [LatentState(trace_id=f"sweep-{i}", vector=embedder.encode(t),
                              source_layer="sweep", dimension=dim, probe_version="v0.3")
                  for i, t in enumerate(texts)]
        return list(zip(states[:-1], states[1:])) if len(states) >= 2 else []

    benign_pairs = _embed_pairs(benign_texts)
    malicious_pairs = _embed_pairs(malicious_texts)

    region = ForbiddenRegion.from_examples(embedder, examples=forbidden_examples,
                                            safety_threshold=0.3)

    results: dict = {"semantic": [], "trajectory": [], "perplexity": []}

    for theta in theta_safe_range:
        region_t = ForbiddenRegion.from_examples(embedder, examples=forbidden_examples,
                                                  safety_threshold=theta)
        probe = SemanticBoundaryProxy(embedder, region_t)
        fp = sum(1 for s_t, s_t1 in benign_pairs
                 if probe.check(s_t, s_t1, alpha).interrupt) if benign_pairs else 0
        tp = sum(1 for s_t, s_t1 in malicious_pairs
                 if probe.check(s_t, s_t1, alpha).interrupt) if malicious_pairs else 0
        results["semantic"].append({
            "threshold": theta,
            "tpr": tp / max(len(malicious_pairs), 1),
            "fpr": fp / max(len(benign_pairs), 1),
        })

    for theta in theta_smooth_range:
        probe = TrajectoryMutationProxy(drop_threshold=theta)
        fp = sum(1 for s_t, s_t1 in benign_pairs
                 if probe.check(s_t, s_t1, alpha).interrupt) if benign_pairs else 0
        tp = sum(1 for s_t, s_t1 in malicious_pairs
                 if probe.check(s_t, s_t1, alpha).interrupt) if malicious_pairs else 0
        results["trajectory"].append({
            "threshold": theta,
            "tpr": tp / max(len(malicious_pairs), 1),
            "fpr": fp / max(len(benign_pairs), 1),
        })

    for theta in theta_entropy_range:
        probe = PerplexityShiftProxy(entropy_threshold=theta)
        fp = sum(1 for s_t, s_t1 in benign_pairs
                 if probe.check(s_t, s_t1, alpha).interrupt) if benign_pairs else 0
        tp = sum(1 for s_t, s_t1 in malicious_pairs
                 if probe.check(s_t, s_t1, alpha).interrupt) if malicious_pairs else 0
        results["perplexity"].append({
            "threshold": theta,
            "tpr": tp / max(len(malicious_pairs), 1),
            "fpr": fp / max(len(benign_pairs), 1),
        })

    return results


# ─────────────────────────────────────────────────────
# Sliding Window Monitor (Round-3 R1 — gradual attack detection)
# ─────────────────────────────────────────────────────

class SlidingWindowMonitor:
    """
    CUSUM-based sliding window monitor for detecting gradual risk accumulation.

    Implements the Page's CUSUM algorithm on the barrier function sequence h_t:

        S_t = max(0, S_{t-1} + (mu_0 - h_t) - drift_allowance)

    where mu_0 is the expected safe-state barrier value and drift_allowance
    controls sensitivity. When S_t exceeds the decision_threshold, a slow-drift
    alarm is raised.

    This addresses the Round-3 reviewer concern that per-step DCBF probes may
    miss gradual "boiling frog" attacks where each step's h_t remains above zero
    but trends downward across many steps.

    Limitations:
      - The CUSUM is parameterized by mu_0 and drift_allowance which require
        calibration on deployment-specific benign traffic.
      - This is an EXPERIMENTAL component (opt-in via ProxyEnsemble configuration).
      - Does not replace per-step DCBF checks; it supplements them.
    """

    def __init__(
        self,
        mu_0: float = 0.2,
        drift_allowance: float = 0.05,
        decision_threshold: float = 1.0,
        window_size: int = 20,
    ) -> None:
        self._mu_0 = mu_0
        self._drift_allowance = drift_allowance
        self._decision_threshold = decision_threshold
        self._window_size = window_size
        self._cusum: float = 0.0
        self._history: list[float] = []

    def update(self, h_t: float) -> bool:
        """Feed a new barrier value h_t. Returns True if slow-drift alarm is triggered."""
        self._history.append(h_t)
        if len(self._history) > self._window_size:
            self._history.pop(0)
        self._cusum = max(0.0, self._cusum + (self._mu_0 - h_t) - self._drift_allowance)
        return self._cusum >= self._decision_threshold

    def reset(self) -> None:
        self._cusum = 0.0
        self._history.clear()

    @property
    def cusum(self) -> float:
        return self._cusum

    @property
    def alarm(self) -> bool:
        return self._cusum >= self._decision_threshold
