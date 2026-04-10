# Revision Response to [0410-1] Review

> 本文档逐点回应 `[0410-1]review.md` 的 8 个部分（Part 1–8），并标注对应的修改文件与位置。

---

## Part 1: Problem Framing Audit — 问题定义审计

### 1.1 "问题定义应从计算理论出发，而非经济学隐喻"

**回应**：已采纳。

**修改内容**：
- 在 LaTeX 论文 `§2 解耦安全范式` 中新增 `§2.1 形式化问题定义`（`\label{sec:problem-statement}`），从概率程序、Rice 定理与实例级验证出发定义核心研究问题。
- 在 `§2.2 防御目标`（`\label{sec:defense-goals}`）中明确列出三条形式化防御目标：多项式时间运行时拦截、前向安全不变性、不可逾越的故障安全底线。
- 新增 `§2.4 理论框架与方法总览`（`\label{sec:paradigm-overview}`），阐述从隐式对齐到解耦安全的范式转移核心逻辑。

**修改文件**：`papers/BeyondModelReflection_DecoupledSafety_CN.tex`（§2 新增 subsections）

### 1.2 "术语替换：经济学意义→计算不对称"

**回应**：已采纳。

**修改内容**：
- `经济学意义上的充分安全` → 删除，改写为「计算不对称意义上」或「前向不变性保证」
- `绝对安全` → `前向不变性保证`
- `信息经济不对称` → `计算/验证不对称（Computational/Verification Asymmetry）`
- `多轮抽取经济学` → `多轮抽取的计算/验证不对称`
- 摘要中 `信息经济学` 作为 Pillar 1 的表述已修正为 `计算/验证不对称`
- 结论已相应更新

**修改文件**：`papers/BeyondModelReflection_DecoupledSafety_CN.tex`（全文术语替换）

---

## Part 2: Threat Model Tightening — 威胁模型收紧

**回应**：已采纳。

**修改内容**：
- LaTeX 论文 `§2.3 威胁模型与攻击者能力` 完全重写，新增：
  - **攻击者能力**：明确为 Hard-label 黑盒，支持自适应查询与组合攻击
  - **Kerckhoffs 边界**：白盒架构 / 黑盒策略参数
  - **攻击者限制**：无白盒梯度；系统级交互预算约束（$N$, $T$）
  - **成功判据**：在查询预算 $B$ 内突破 DCBF 安全超水平集
- RFC 文档 `§2.2 对手能力假设` 同步收紧，添加上述所有约束

**修改文件**：
- `papers/BeyondModelReflection_DecoupledSafety_CN.tex`（§2.3 重写）
- `papers/Decoupled Safety Kernel Architecture RFC_v0.2_r2.md`（§2.2 + §2.3）

---

## Part 3: Theory Claim Legality Check — 理论主张合法性审查

**回应**：已采纳。全部 5 条理论主张已按"最强可 Defend 版本"降温。

| # | 原始主张 | 修正后（最强可 Defend） | 修改位置 |
|---|---------|----------------------|---------|
| 1 | 全局隐式安全不可判定 | 明确"非平凡语义属性"的 Rice 定理前提 | `Step1-理论部分.md` Theorem 1 |
| 2 | 外置解耦多项式验证 | 补充"多项式界限候选集"假设 + 安全谓词 ∈ P | `Step1-理论部分.md` Theorem 2 |
| 3 | DCBF 前向不变性 | 限定为"可观测潜在/状态演化"，添加 Assumption 6.1 | `Step1-理论部分.md` Theorem 2.2 |
| 4 | 组合隐式安全不可组合 | 声明超图建模假设 | `Step1-理论部分.md` Theorem 3.1 |
| 5 | 安全代数优雅降级 | 声明有限下确界与底元素假设 | `Step1-理论部分.md` Theorem 3.6 |

**修改文件**：`papers/Step1-理论部分.md`（5 个 Theorem 各添加 scope annotation）

---

## Part 4: Theory-to-Code Traceability — 理论到代码的可追溯性

**回应**：已通过以下代码修改建立显式映射。

| 理论主张 | 代码映射 | 修改 |
|---------|---------|------|
| Corollary 2.1: \|C\| ≤ poly | `axiom_hive_solver.rs` | 新增 `MAX_CANDIDATE_SET_SIZE = 128` 常量 + 超限 page_fault |
| Assumption 6.1: Surrogate φ | `proxy_ensemble.py` | 模块 docstring 补充完整数学公式 |
| Theorem 3.1: 不可组合 | `action_space_benchmark.py` | 新增 password split 与 RAG split 场景 |
| Theorem 2.2: 前向不变性 | `dcbf_evaluator_v2.py` / `dcbf_monitor.py` | 已有实现，docstring 已对齐 |

**修改文件**：
- `decoupled-safety-kernel/src_kernel/src/axiom_hive_solver.rs`
- `decoupled-safety-kernel/src_observability/proxy_ensemble.py`
- `decoupled-safety-kernel/src_eval_benchmark/action_space_benchmark.py`

---

## Part 5: Top Reviewer Risks — 审稿人风险点

### Risk 1（致命）：DCBF 连续/离散失配

**回应**：已在论文与理论文档中显式承认并限定 scope。

- LaTeX `§6 解耦安全的物理学` 新增段落「DCBF 的作用域与状态映射假设」
- 理论文档新增 Assumption 6.1（Surrogate State Mapping），明确 φ: H → R^d 的工程假设性质
- 代码 `proxy_ensemble.py` docstring 同步标注局限性

### Risk 2（叙事风险）：经济学术语未建模

**回应**：已通过 Part 1 的术语替换处理。所有未经博弈论建模的经济学术语已替换为计算复杂度语言。

### Risk 3（实验风险）：缺少组合攻击基准

**回应**：已在 `action_space_benchmark.py` 新增两个场景：
1. `password_split_extraction` — Agent A + B 密码上下半段拼合
2. `rag_split_extraction` — 三 Agent RAG 语料分片提取

所有 5 个场景通过，composability gain = 1.0。

### Risk 4（定义风险）：Token-level vs Chunk-level 边界

**回应**：已在 LaTeX `§6` 新增段落「Token 级投影与段落级过滤的能力边界」，显式声明黑盒 API 下降级为 chunk-level 后置过滤。

---

## Part 6: Contribution Reframing — 贡献重构

**回应**：已采纳。

贡献改写为三条平衡版：
1. **理论基础**：全局隐式安全不可判定性 + 合取依赖下不可组合性
2. **安全内核架构与多项式验证**：解耦运行时架构 + 多项式有界候选集下的验证降阶
3. **代数框架与系统实现**：安全算子偏序集 + 开源原型 + 超图基准验证

**修改文件**：`papers/BeyondModelReflection_DecoupledSafety_CN.tex`（§1 引言）

---

## Part 7: Paper-Ready Text — 论文就绪文本

**回应**：已全部插入。

- Problem Statement → `§2.1`
- Threat Model → `§2.3`（完全重写）
- Defense Goals → `§2.4`
- Theoretical & Method Overview → `§2.5`

**修改文件**：`papers/BeyondModelReflection_DecoupledSafety_CN.tex`

---

## Part 8: Research Team Action List — 研究团队任务清单

### 理论组 [必须做]

| 任务 | 状态 | 位置 |
|------|------|------|
| DCBF 状态映射假设补充 | ✅ 完成 | `Step1-理论部分.md` Assumption 6.1 |
| 理论主张降温 | ✅ 完成 | `Step1-理论部分.md` 5 个 Theorem |

### 系统组 [必须做]

| 任务 | 状态 | 位置 |
|------|------|------|
| 候选集多项式截断常量 | ✅ 完成 | `axiom_hive_solver.rs` `MAX_CANDIDATE_SET_SIZE` |
| Surrogate metric 文档化 | ✅ 完成 | `proxy_ensemble.py` module docstring |

### 实验组 [必须做]

| 任务 | 状态 | 位置 |
|------|------|------|
| Agent A+B 密码拼合场景 | ✅ 完成 | `action_space_benchmark.py` `scenario_password_split_extraction` |
| RAG 分片提取场景 | ✅ 完成 | `action_space_benchmark.py` `scenario_rag_split_extraction` |

### 实验组 [建议做]

| 任务 | 状态 | 位置 |
|------|------|------|
| 单步 Projection latency/throughput | ✅ 完成 | `utility_benchmark.py` `projection_latency` 统计（p50/p95/p99/throughput） |

### 写作组 [必须做]

| 任务 | 状态 | 位置 |
|------|------|------|
| 术语全局替换 | ✅ 完成 | LaTeX 全文 |
| 贡献重构 | ✅ 完成 | LaTeX §1 |

### 写作组 [不要做]

| 任务 | 处理方式 |
|------|---------|
| Token-level vs chunk-level 边界 | 已主动暴露此边界（LaTeX §6），彰显学术克制 |

---

## 修改文件汇总

| 文件 | 修改类型 |
|------|---------|
| `papers/BeyondModelReflection_DecoupledSafety_CN.tex` | 问题定义重写、威胁模型收紧、贡献重构、术语替换、DCBF scope、能力边界 |
| `papers/Step1-理论部分.md` | Assumption 6.1 新增、5 个 Theorem scope annotation |
| `papers/Decoupled Safety Kernel Architecture RFC_v0.2_r2.md` | §2.2 威胁模型收紧、§2.3 新增计算不对称目标 |
| `decoupled-safety-kernel/src_kernel/src/axiom_hive_solver.rs` | `MAX_CANDIDATE_SET_SIZE` 常量 + 超限截断 |
| `decoupled-safety-kernel/src_observability/proxy_ensemble.py` | 数学公式 docstring |
| `decoupled-safety-kernel/src_eval_benchmark/action_space_benchmark.py` | 2 个新增场景 |
| `decoupled-safety-kernel/src_eval_benchmark/utility_benchmark.py` | projection latency p50/p95/p99 + throughput |
