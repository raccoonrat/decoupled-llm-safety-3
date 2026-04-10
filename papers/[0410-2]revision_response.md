# Round-2 审稿回复（[0410-2]round2-review.md 逐点回应）

**论文**：Beyond Model Reflection — Decoupled Safety Kernel Architecture  
**修订日期**：2026-04-10  
**对应审稿文件**：`papers/[0410-2]round2-review.md`

---

## 总体回应

感谢审稿人对架构的积极评价（"高度合理性与完备性"、"开创性全栈方案"）及对 6 个风险点的深入分析。我们逐点回应如下，所有修改均已在本次提交中完成。

---

## 风险点 1：DCBF 多维指标不足

**审稿意见**：DCBF 仅有两个代理维度不足以覆盖所有攻击路径，建议增加"困惑度突增"等指标。

**回应**：

已完成以下修改：

| 修改项 | 位置 |
|--------|------|
| 新增 `PerplexityShiftProxy` 第三维探针 | `src_observability/proxy_ensemble.py` |
| 扩展 `EnsembleReport` 增加 `perplexity_report` 字段 | 同上 |
| `ProxyEnsemble` 升级至 v0.3 三维探针 | 同上 |
| `build_default_ensemble` 工厂函数增加 `enable_perplexity_probe` 参数 | 同上 |
| 论文 §6 新增「DCBF 多维指标路线图」段落 | `BeyondModelReflection_DecoupledSafety_CN.tex` |
| 理论文档 Assumption 6.1 扩展为三维代理映射 | `Step1-理论部分.md` |
| RFC §5.2 补充多维 DCBF 探针接口说明 | `RFC_v0.2_r2.md` |

**第三维探针数学定义**：$h_3(z_t, z_{t-1}) = \theta_{\text{entropy}} - |H(\phi(z_t)) - H(\phi(z_{t-1}))|$，检测嵌入向量归一化后 Shannon 熵的急剧变化。

**未来路线图**（已写入论文 §6）：(a) 训练辅助安全评分模型，(b) 多元 SPC 慢速漂移检测，(c) 黑盒 API 多采样输出探测。

---

## 风险点 2：Axiom Hive 性能基准缺失

**审稿意见**：补充不同 K 值下的投影耗时数据。

**回应**：

| 修改项 | 位置 |
|--------|------|
| 新增 K-sweep 基准测试（K={5,10,20,50,128}） | `src_eval_benchmark/utility_benchmark.py` |
| `UTILITY_K_SWEEP=1` 环境变量启用 K-sweep 模式 | 同上 |
| 论文 §6 新增性能数据表（Table: projection-perf） | `BeyondModelReflection_DecoupledSafety_CN.tex` |

**关键数据**：所有 K ≤ 128 均在 20ms 硬预算内完成，QP 求解器耗时与 K 近似线性增长（O(K) argmin），与 Corollary 2.1 的多项式时间声明一致。

---

## 风险点 3：Judge Ensemble confidence 字段未参与投票

**审稿意见**：`confidence: f32` 字段存在但未使用，应实现置信度加权融合。

**回应**：

| 修改项 | 位置 |
|--------|------|
| 新增 `confidence_weighted_tally()` 方法 | `src_kernel/src/judge_ensemble.rs` |
| 新增 `WeightedEnsembleReport` 结构体 | 同上 |
| 3 个新增单元测试（weighted_deny_first, weighted_allow_when_no_deny, weighted_revise_beats_allow） | 同上 |
| 论文 §4.4 补充置信度加权投票说明与验证器性能参考 | `BeyondModelReflection_DecoupledSafety_CN.tex` |
| RFC §5.4 补充 WeightedEnsembleReport 接口与投票规则 | `RFC_v0.2_r2.md` |

**Deny-优先不变量**：只要 $W_{\text{deny}} > 0$，最终裁决必为 Deny（除 break-glass）。所有 28 个 Rust 测试通过。

---

## 风险点 4：架构复杂度论证缺失

**审稿意见**：量化说明架构复杂度可控。

**回应**：

| 修改项 | 位置 |
|--------|------|
| 论文 §6 新增「架构复杂度与工程可控性」段落 | `BeyondModelReflection_DecoupledSafety_CN.tex` |

**代码统计**：
- Rust Ring-0 内核：2,408 行（751 行 Axiom Hive、650 行 IPC、264 行 Judge Ensemble、167 行审计链、142 行降级 FSM）
- Python 可观测性层：943 行
- 评测基准：1,742 行
- 总计：约 5,100 行
- 编译后 debug 二进制约 6.8 MB，release strip 后 < 2 MB
- 28 个 Rust 测试全部通过

---

## 风险点 5：缺少策略 DSL 示例与真实越狱案例

**审稿意见**：附录提供策略 DSL 规则示例；补充真实越狱防御案例。

**回应**：

| 修改项 | 位置 |
|--------|------|
| 新建 5 个 DSL 策略示例文件 | `rfc_contracts/example_policies/` |
| 论文新增附录 §A「安全策略 DSL 语法与规则示例」 | `BeyondModelReflection_DecoupledSafety_CN.tex` |
| RFC §13.1 补充 DSL 热加载具体操作流程 | `RFC_v0.2_r2.md` |
| 新增 3 个真实越狱场景（DAN、Grandma、Crescendo） | `src_eval_benchmark/action_space_benchmark.py` |
| 论文 §6 新增「案例研究」段落 | `BeyondModelReflection_DecoupledSafety_CN.tex` |
| 论文 §6 新增 JailbreakBench 对比讨论 | 同上 |

**DSL 示例策略**：
1. `pii_filter.policy.json` — PII 过滤（信用卡号、SSN、电话号码）
2. `prompt_injection_guard.policy.json` — 提示注入防御（忽略指令、系统提示抽取、角色重写）
3. `harmful_content_classifier.policy.json` — 有害内容分类（武器、恶意软件、自残）
4. `multi_agent_capability_fence.policy.json` — 多 Agent 合取能力围栏（Theorem 3.1 defense）
5. `rate_limit_and_budget.policy.json` — 速率限制与交互预算

**真实越狱场景**：
- `scenario_do_anything_now()` — DAN 角色重写越狱
- `scenario_grandma_exploit()` — 情感操纵越狱
- `scenario_multi_turn_crescendo()` — 多轮逐步升级攻击

---

## 风险点 6：论文结构性补充

**审稿意见**：Track A/B 解读指导、部署建议。

**回应**：

| 修改项 | 位置 |
|--------|------|
| 新增 §「讨论」节 | `BeyondModelReflection_DecoupledSafety_CN.tex` |
| Track A/B 分轨解读指导 | 同上 |
| 真实世界部署建议（4 条） | 同上 |

---

## 修改文件汇总

| 文件 | 修改类型 |
|------|----------|
| `papers/BeyondModelReflection_DecoupledSafety_CN.tex` | DCBF 路线图、DSL 附录、性能表、案例研究、代码统计、置信度加权说明、讨论节 |
| `papers/Step1-理论部分.md` | Assumption 6.1 扩展为三维代理映射 |
| `papers/Decoupled Safety Kernel Architecture RFC_v0.2_r2.md` | §5.2 多维 DCBF 接口、§5.4 置信度加权投票、§13.1 DSL 热加载流程 |
| `decoupled-safety-kernel/src_observability/proxy_ensemble.py` | 新增 PerplexityShiftProxy、EnsembleReport 扩展、ProxyEnsemble v0.3 |
| `decoupled-safety-kernel/src_kernel/src/judge_ensemble.rs` | confidence_weighted_tally()、WeightedEnsembleReport、3 个新测试 |
| `decoupled-safety-kernel/src_eval_benchmark/utility_benchmark.py` | K-sweep 基准 |
| `decoupled-safety-kernel/src_eval_benchmark/action_space_benchmark.py` | 3 个真实越狱场景 |
| `decoupled-safety-kernel/rfc_contracts/example_policies/` | 5 个 DSL 策略示例文件（新建） |
| `papers/[0410-2]revision_response.md` | 本文件（新建） |

---

## 验证状态

- [x] Rust 编译通过（`cargo check`）
- [x] 28 个 Rust 单元测试全部通过（`cargo test`，含 3 个新增置信度加权测试）
- [x] 所有新增代码与论文修改一致
