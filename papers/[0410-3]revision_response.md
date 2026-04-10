# Round-3 修订回复

> 对应审稿文档：`[0410-3]round3-review.md`
> 修订日期：2025-04-10

---

## 总体回应

感谢审稿人对 Round-2 修订的高度认可和深入的改进建议。本轮修订聚焦于**深化论证、补充实验、扩展讨论**，不涉及架构重构。以下逐点回应审稿人的 5 个改进维度。

---

## §1: DCBF 探针阈值调优论证与渐进攻击检测

### 1.1 阈值选取依据（审稿人：三个阈值的选取依据是什么？）

**修改位置**：论文 §6「阈值选取依据与灵敏度分析」段落

**回应**：新增阈值校准方法论的完整说明：
- $\theta_{\text{safe}} = 0.3$ 取自良性/恶意嵌入余弦距离分布的等错误率（EER）点
- $\theta_{\text{smooth}} = 0.5$ 来源于正常对话相邻步嵌入余弦距离的第 99 百分位数
- $\theta_{\text{entropy}} = 0.3$ 取自良性对话嵌入熵差分布尾部的 2σ 边界
- 灵敏度分析表明 TPR/FPR 在合理阈值区间内变化 < 8%

**代码支撑**：`proxy_ensemble.py` 新增 `threshold_sensitivity_sweep()` 函数，支持部署者根据自身语料在线校准。

### 1.2 渐进攻击检测（审稿人：渐进式"温水煮蛙"攻击能否检测？）

**修改位置**：论文 §6「渐进攻击检测与 CUSUM 展望」段落

**回应**：新增基于 CUSUM（累积和）的滑动窗口补充方案：$S_t = \max(0, S_{t-1} + (\mu_0 - h_t) - k)$，标注为未来工作。代码库已实现实验性组件 `SlidingWindowMonitor`（默认关闭）。

**代码支撑**：`proxy_ensemble.py` 新增 `SlidingWindowMonitor` 类。

### 1.3 多探针可视化（审稿人：缺少多探针协同的可视化）

**修改位置**：论文附录「多探针屏障值模拟可视化」（§附录 B）

**回应**：新增 DAN 越狱场景下 $h_1, h_2, h_3$ 随步变化的模拟数据表及解读，展示三探针互补特性。

---

## §2: Axiom Hive K 值上限依据与动态扩容

### 2.1 K=128 选型依据（审稿人：K=128 为何为默认上限？）

**修改位置**：论文 §6「$K=128$ 默认上限选型依据」段落

**回应**：从概率质量覆盖率（Top-128 覆盖 > 99.5% 概率质量）和硬预算约束（p95 wall time < 20ms）两个角度论证合理性。

### 2.2 动态扩容逻辑（审稿人：EmptySafeCandidateSet 时直接拒绝过于保守）

**修改位置**：
- `axiom_hive_solver.rs` 新增 `DynamicKExpansionPolicy`（默认关闭）
- 论文讨论部分新增 EmptySafeCandidateSet 处理策略段落
- RFC §5.5 新增动态扩容接口说明

**回应**：实现可选的动态 K 翻倍策略（最大至 K=512，最多重试 2 次），默认关闭。讨论了硬拒绝 vs 动态扩容的权衡——安全场景用硬拒绝，可用场景用动态扩容。

### 2.3 动态 K 实验

**修改位置**：`utility_benchmark.py` 新增动态 K 扩容实验（`UTILITY_DYNAMIC_K=1` 启用）

**回应**：构造全候选均被否决的极端用例，测量扩容后的 K 值、耗时和成功输出率。

---

## §3: 置信度加权投票论证深化

### 3.1 Confidence 来源与校准（审稿人：confidence 来源和校准未说明）

**修改位置**：论文 §4.4「置信度来源与校准」段落

**回应**：明确三类验证器的 confidence 来源：
- LLM 型：softmax 概率，可选温度缩放校准
- 规则型：固定 1.0
- 外部分类器：评分归一化
- 关键不变量：Deny-优先使安全保证单调

**RFC 同步**：RFC §5.4 新增 Confidence 校准指导表。

### 3.2 加权 vs 未加权对比实验（审稿人：缺少定量对比）

**修改位置**：
- 新建 `voting_comparison_benchmark.py`（250 组合成投票场景）
- 论文 §6「置信度加权 vs 未加权投票的模拟对比」段落

**回应**：实验表明两种策略在 Deny-优先不变量下 TPR 一致，加权投票的优势在于审计链的信号保持能力。

### 3.3 审计透明性（审稿人：审计透明性需强调）

**修改位置**：论文 §4.4「审计透明性与 Evidence Chain」段落

**回应**：强调 Evidence Chain 记录每个验证器判决 + confidence 的完全可追溯性，对比 OpenAI 等黑箱方案的优势。

---

## §4: DSL 表达能力、局限性与形式化展望

### 4.1 规则类型能力矩阵（审稿人：规则类型的能力/局限未集中论述）

**修改位置**：论文附录 A「规则类型能力矩阵」表

**回应**：新增 6 种规则类型的适用场景、局限性和互补机制对照表。

### 4.2 DSL 局限与扩展方向（审稿人：缺少局限性讨论）

**修改位置**：论文讨论部分「DSL 局限与扩展方向」段落

**回应**：明确 DSL 当前不支持嵌套条件逻辑、上下文状态依赖、多语言内容，提出未来扩展路径（脚本扩展、自动翻译、社区模式库）。

### 4.3 形式化验证展望（审稿人：缺少形式化验证展望）

**修改位置**：论文讨论部分「形式化验证展望」段落

**回应**：提出对 DeterministicAutomaton 应用模型检测（SPIN/NuSMV）进行无冲突/无死角验证的方案。

### 4.4 复杂组合策略示例（审稿人：缺少复杂组合策略）

**修改位置**：
- 新建 `escalating_sensitivity.policy.json`
- 论文附录 A 新增示例 4 及解读

**回应**：展示四规则组合实现"多轮机密请求阶梯式加严"——cross_session_guard + rate_limit + budget_guard + keyword_flag 协同工作。

---

## §5: 越狱覆盖率、横向对比与案例深化

### 5.1 持续攻防评测闭环（审稿人：缺少持续评测闭环机制）

**修改位置**：论文讨论部分「持续攻防评测闭环」段落

**回应**：提出动态 Jailbreak 测试集方案（生产采集 + Red Teaming 自动生成 + 月度评估周期）。

### 5.2 主流方案横向对比表（审稿人：缺少主流方案横向对比表）

**修改位置**：论文附录「主流 LLM 安全机制横向对比」表

**回应**：比较 OpenAI Moderation API、RLHF/RLAIF、Constitutional AI、Google 内容过滤、Llama-Guard 与本架构，从拦截机制、介入粒度、规则灵活性、可审计性、热更新能力 5 个维度分析。

### 5.3 案例研究深化（审稿人：案例分析需逐步解构模块协同）

**修改位置**：论文 §6 案例研究段落

**回应**：将 DAN 越狱案例深化为逐步模块协同解构——Gateway regex 匹配 → DCBF 三探针 margin 收窄 → Judge Ensemble 加权投票（$W_d = 1.76$）→ 超图合取检测 → Evidence Chain 全链路记录。

### 5.4 开放挑战与未覆盖攻击面（审稿人：需承认未覆盖的攻击面）

**修改位置**：论文讨论部分「开放挑战与未覆盖攻击面」段落

**回应**：主动承认隐蔽通道、多语言编码变种、逻辑陷阱/事实误导、模型级后门等目前未完全解决的攻击矢量。

---

## 修改文件汇总

| 文件 | 修改内容 |
|---|---|
| `papers/BeyondModelReflection_DecoupledSafety_CN.tex` | 阈值调优论证、CUSUM 展望、K 选型依据、confidence 来源与校准、审计透明性、DSL 能力矩阵、DSL 局限、形式化验证、组合策略解读、持续评测、主流对比表、案例深化、加权投票对比、EmptySafeCandidateSet 策略、开放挑战 |
| `papers/Decoupled Safety Kernel Architecture RFC_v0.2_r2.md` | 动态 K 扩容接口（§5.5）、confidence 校准指导表（§5.4） |
| `decoupled-safety-kernel/src_observability/proxy_ensemble.py` | `threshold_sensitivity_sweep()` 函数、`SlidingWindowMonitor` 类 |
| `decoupled-safety-kernel/src_kernel/src/axiom_hive_solver.rs` | `DynamicKExpansionPolicy`、`DynamicExpansionResult`、`MAX_EXPANDED_K` |
| `decoupled-safety-kernel/src_eval_benchmark/utility_benchmark.py` | 动态 K 扩容实验 |
| `decoupled-safety-kernel/src_eval_benchmark/voting_comparison_benchmark.py` | 加权 vs 未加权投票模拟对比（新建） |
| `decoupled-safety-kernel/rfc_contracts/example_policies/escalating_sensitivity.policy.json` | 阶梯式加严组合策略（新建） |
| `papers/[0410-3]revision_response.md` | 本文档（新建） |
