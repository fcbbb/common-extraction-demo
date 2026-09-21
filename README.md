# 多信号代码共性组件抽取

本仓库比较两种自动代码共性抽取方法：

- **Baseline A**：把整个文件簇一次性交给大模型，直接生成公共组件与成员改写；
- **Signal**：先用逐字克隆、AST 骨架和语义嵌入召回局部候选，再经 LLM 判定门筛选并抽取。

功能等价是硬约束。最终结果只接受编译成功、行为测试全部通过且净 token 收益为正的改写；未接受文件保持原样并计入完整代码池。

## 当前结果

| 数据集 | 性质 | Baseline A | Signal | 有效行为验证 |
| --- | --- | ---: | ---: | ---: |
| Apache Libcloud load-balancer drivers | 真实生产代码 | 0.05% | **9.88%** | 全部通过 |
| CodeContests | 受控竞赛代码 | 3.81% | **5.13%** | 24,897 / 24,897 |

完整分析见[最终中文报告](demo/reports/final_report_signal.md)。已提交的 `demo/results/` 只包含小型汇总；原始 API 响应、改写代码树、测试轨迹、模型和缓存均被 Git 忽略。

## 五分钟开始

### 1. 创建环境

```bash
conda env create -f environment.yml
conda activate common-extraction
cp .env.example .env
```

在 `.env` 中填写 `DEEPSEEK_API_KEY`。不要把真实密钥提交到 Git。

### 2. 验证仓库

以下步骤不访问网络、不调用大模型 API：

```bash
make check
make verify-results
```

### 3. 准备数据

CodeContests 的匿名化源码和测试清单已经随仓库提供，无需准备。

Libcloud 使用固定的 `v3.9.1` / commit `6c867a3...`，首次运行会下载上游仓库并生成测试切片：

```bash
make prepare-libcloud
```

下载缓存在 `.cache/`，准备后的上游副本位于 `demo/datasets/libcloud_loadbalancer_real/`；两者均不会进入 Git。

### 4. 运行一次小规模实验

CodeContests 单簇：

```bash
DATASET=codecontest METHOD=all CLUSTER_ID=0 bash docs/run_extract.sh
DATASET=codecontest METHOD=all CLUSTER_ID=0 bash docs/run_eval.sh
```

Libcloud 全目录只有一个簇：

```bash
DATASET=libcloud METHOD=all bash docs/run_extract.sh
DATASET=libcloud METHOD=all TEST_TIMEOUT_SEC=120 bash docs/run_eval.sh
```

`METHOD=all` 运行 Baseline A 和 Signal；也可设为 `a`、`signal` 或兼容的 `b`。抽取阶段会产生远端 API 费用。默认使用 4 路 API 并发，可用 `WORKERS=1` 调低。

## Signal 流程

```text
源代码
  ├─ 逐字 token 克隆
  ├─ AST 骨架相似
  └─ 代码语义嵌入
          ↓
      多信号融合
          ↓
      LLM 判定门
          ↓
  局部公共组件与成员改写
          ↓
  编译、行为测试、收益验收
```

语义通道默认使用 `codefuse-ai/C2LLM-0.5B`，首次运行由 Hugging Face 下载；失败时回退到 `Qwen/Qwen3-Embedding-0.6B`。可通过 `--embedding-model` 指定本地路径，也可设置 `DEMO_EMBED_PYTHON` 或 `DEMO_EMBED_ENV` 使用独立嵌入环境。嵌入缓存位于 `demo/discovery/cache/`。

本次最终实验使用 `semantic_top_frac=0.005`、4 路 gate/extraction 并发和 65,536 token 的 API 输出上限。

## 目录结构

```text
demo/
  baselines/      Baseline A、Baseline B 和 Signal 运行器
  discovery/      克隆、AST、语义信号、融合与 LLM 判定门
  eval/           编译、测试、指标、完整池比较和发布结果校验
  prepare/        CodeContests 与 Libcloud 数据准备
  datasets/       已跟踪的 CodeContests；本地生成的 Libcloud 被忽略
  results/        仅跟踪当前两组实验的小型汇总
  reports/        当前最终中文报告
docs/
  run_extract.sh  生成公共组件和改写代码
  run_eval.sh     对已有产物执行测试与评估
tests/            不依赖 API 的离线测试
```

## 常用参数

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DATASET` | `codecontest` | `codecontest` 或 `libcloud` |
| `METHOD` | `all` | `a`、`b`、`signal` 或 `all` |
| `CLUSTER_ID` | `all` | 指定代码簇；CodeContests 可设 `0`–`9` |
| `WORKERS` | `4` | Baseline A、Signal gate 和抽取并发数 |
| `RESUME` | `0` | 设为 `1` 复用已完成产物 |
| `SEMANTIC_TOP_FRAC` | `0.005` | 语义边额外保留比例 |
| `TEST_WORKERS` | `16` | 评估并发数 |
| `ENABLE_MDL` | `0` | 设为 `1` 启用可选 GGUF MDL 指标 |

更多命令见[命令速查](docs/command.md)，结果口径和局限见[最终报告](demo/reports/final_report_signal.md)。

## 结果口径

- token 使用 Python tokenizer 统计，排除注释和文档字符串；
- 行为通过率只在原始程序已通过的测试子集上计算；
- Signal 候选有文件重叠时，使用最大权重的文件互斥组合；
- Baseline A 和 Signal 都采用相同的“测试通过且正收益，否则回滚”规则；
- CodeContests 的完整池组合目前是保守核算结果，联合部署前仍需命名各公共模块并再次执行完整测试。

## 安全与大文件

- `.env`、模型、缓存、Slurm 日志和原始 API 响应已经加入 `.gitignore`；
- 不要把 API key 写入命令历史、报告或调用日志；
- 可选 MDL 需要自行安装 `llama-cpp-python` 并设置 `MODEL_PATH`，不影响当前主结果复现。
