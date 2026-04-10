# Track A 评估说明（论文主表：RSR / FPR / 抽取 F1）

Track A 是 **Ring-4 论文评估层**：在 **Ring-0 可信内核之外** 编排上游 LLM、`e2e_full_chain` 全链路与可选 HTTP 裁判，产出可写入论文主表的指标与 Ring-0 `profile` 聚合。**不得**把裁判逻辑并入 Ring-0。

## 实验维度（与代码一致）

每条 run 的 `variant_id` 形如：

`dcbf=<...>|judge=<...>|asg=<...>`

| 维度 | CLI / 代码取值 | 含义 |
|------|----------------|------|
| `dcbf_mode` | `--dcbf v1` → `v1-logprob`；`--dcbf v2` → `v2-proxy-ensemble` | DCBF v1 依赖 API logprob；v2 用本地 ProxyEnsemble 几何距离（与 logprob 解耦） |
| `judge_mode` | `--judge none` / `heuristic` / `http` | 无裁判 / 正则启发式 / 外部 HTTP 裁判（LLM-as-judge） |
| `action_space_guard` | 当前 harness 固定 `off` | 动作空间守卫（与 action-space benchmark 对齐时可扩展） |

Ring-0 侧延迟与缓存等来自子进程返回的 `profile`，汇总进 `*.summary.json`（如 `mean_rust_total_us_last_step`、`ring0_cache_hit_rate` 等）。

## 目录结构

```
paper_eval/
├── README.md              # 本文件
├── data/                  # 版本化小数据集
│   ├── harmful_prompts_v1.txt
│   ├── benign_prompts_v1.txt
│   ├── extract_prompts_v1.txt
│   └── extract_secrets_v1.json
└── runs/                  # 每次运行生成 <run_id>.jsonl 与 <run_id>.summary.json
```

## 环境与依赖

1. **Conda**：`conda activate decoupled`（或你团队约定的等价环境）。
2. **Python 依赖**（在 `decoupled-safety-kernel` 下）：
   ```bash
   pip install -r requirements.txt
   ```
3. **Rust 二进制**：全链路透传 Ring-0 需要已编译的 `e2e_ring3_json`：
   ```bash
   cd decoupled-safety-kernel
   cargo build --release --bin e2e_ring3_json
   ```
   （`debug` 产物亦可，脚本会按 `target/release` / `target/debug` 查找。）

4. **密钥与网络**（仓库**根目录**复制 `env.example` 为 `env` 并填写；该文件通常 gitignore）：
   - **上游模型 DeepSeek（直连）**：`DEEPSEEK_API_KEY`；可选 `GATEWAY_UPSTREAM`（默认 `https://api.deepseek.com`）。
   - **OpenRouter 作 HTTP 裁判**：`OPENROUTER_API_KEY`；`JUDGE_BASE_URL`（默认 `https://openrouter.ai/api/v1`）；`JUDGE_MODEL`（默认 `deepseek/deepseek-chat`）。
   - **仅裁判走 SOCKS5**（OpenRouter 需要代理时）：`TRACKA_HF_SOCKS5`，例如 `127.0.0.1:1080`（DeepSeek 仍直连，不经过该变量）。

`run_trackA_harness.py` 与 `judge_service.py` 会自动尝试加载仓库根目录的 `env`。

## 裁判服务（`--judge http` 时必开）

在 **`decoupled-safety-kernel`** 目录下（保证 `PYTHONPATH` 正确）：

```bash
conda activate decoupled
cd decoupled-safety-kernel
PYTHONPATH=. python3 src_paper_eval/judge_service.py --port 8199 --backend chat
```

- `--backend heuristic`：零外网，适合 CI / 快速冒烟。
- `--backend chat`：通过 OpenAI 兼容 API 调用 OpenRouter；若设置了 `TRACKA_HF_SOCKS5`，会使用 `httpx` 的 SOCKS5 客户端。

Harness 默认请求 `http://localhost:8199/judge`。若改端口，请设置：

```bash
export TRACKA_JUDGE_URL=http://127.0.0.1:<端口>/judge
```

## 运行 Track A Harness

**工作目录必须是 `decoupled-safety-kernel`**，且使用 `PYTHONPATH=.`（或等价绝对路径）。

```bash
conda activate decoupled
cd decoupled-safety-kernel
PYTHONPATH=. python3 scripts/run_trackA_harness.py --help
```

### 常用示例

| 场景 | 命令 |
|------|------|
| 离线桩（不调上游 API，指标多为 N/A） | `PYTHONPATH=. python3 scripts/run_trackA_harness.py --offline --dataset harmful` |
| 在线 + 启发式裁判 | `PYTHONPATH=. python3 scripts/run_trackA_harness.py --dataset harmful --dcbf v2 --judge heuristic` |
| 在线 + HTTP 裁判（需先起 `judge_service`） | `PYTHONPATH=. python3 scripts/run_trackA_harness.py --dataset harmful --dcbf v2 --judge http` |
| 缩短自回归步数（调试/冒烟） | 同上并加 `--max-steps 8` |
| 单种子 | 加 `--seeds 42` |

数据集：`--dataset harmful | benign | extract`（数据文件见上文 `data/`）。

### 输出

- 终端打印一行 JSON：`jsonl` 与 `summary` 的绝对路径。
- **`metrics` 摘要**（`*.summary.json`）：
  - `harmful_rsr`：有害提示上的拒答成功率（依赖裁判）。
  - `benign_refusal_fpr`：良性提示误拒率（`benign` 集）。
  - `mean_extract_f1` / `max_extract_f1`：抽取任务 token-level F1（`extract` 集）。
  - Ring-0 聚合：`mean_rust_total_us_last_step`、`ring0_cache_hit_rate`、`ring0_qp_exceeded_rate` 等。

### Shell 注意（Bash）

不要使用 `cd decoupled-safety-kernel && python ... &` 这种写法启动后台进程时依赖「当前目录已切换」——在 Bash 里 **`cd` 可能只作用于后台子 shell**，父 shell 仍在原目录，会导致找不到 `scripts/run_trackA_harness.py`。应**先** `cd` 到内核目录，**再**启动命令（或显式使用绝对路径）。

## 导出 LaTeX 主表片段

在 `decoupled-safety-kernel` 下：

```bash
PYTHONPATH=. python3 scripts/export_trackA_table_tex.py
PYTHONPATH=. python3 scripts/export_trackA_table_tex.py \
  --runs-dir paper_eval/runs \
  --out generated/trackA_main_table_cn.tex
```

默认扫描 `paper_eval/runs/*.summary.json`，生成 `booktabs` 表格片段（列与论文 Track A 维度对齐）。

## 相关源码（便于对照论文）

| 组件 | 路径 |
|------|------|
| Harness 核心 | `src_paper_eval/trackA_harness.py` |
| CLI | `scripts/run_trackA_harness.py` |
| HTTP 裁判服务 | `src_paper_eval/judge_service.py` |
| Harness → 裁判客户端 | `src_paper_eval/judge_client.py` |
| 抽取 F1 与 system 提示构造 | `src_paper_eval/extraction_protocol.py` |
| 全链路（DeepSeek top-k → Ring-0） | `scripts/e2e_full_chain.py`（支持 `--system` 注入秘密用于 extract） |
| DeepSeek 客户端 | `llm_user_space/deepseek_client.py` |
| 环境加载 | `llm_user_space/env_loader.py`（默认 `<repo_root>/env`） |

更全的环境变量说明见仓库根目录 **`env.example`**。
