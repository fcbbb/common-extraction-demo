# 常用命令速查

两个专用脚本，分工明确：

- `run_extract.sh` — 只跑 DeepSeek 抽取/改写 + 编译（CPU，不测不评）
- `run_eval.sh` — 只跑 compile/test/metrics/MDL/报告（GPU，需要 GGUF 模型，不调 DeepSeek）

两个脚本都支持 `DATASET` 开关：`codecontest`（默认）或 `complex`（Scrapy slice，pytest 模式）。
`complex` 会自动切到 conda 环境 `qwen-gguf`（pytest 模式需要 scrapy@commit）。

## 环境变量

| 变量 | 默认 | 作用 |
|---|---|---|
| `DATASET` | `codecontest` | `codecontest` / `complex` |
| `BASELINE` | extract: `both`；eval: `a` | `a` / `b` / `both` |
| `CLUSTER_ID` | extract: `all`；eval: `all` | `0..9` 或 `all` |
| `RESULTS_SUFFIX` | 空字符串 | 结果目录后缀，例如 `_edits` 对应 `demo/results/complex_edits` |
| `DEEPSEEK_API_KEY` | - | extract 必需（除非 `RESUME=1` 全命中） |
| `DEEPSEEK_MODEL` | `deepseek-v4-flash` | 抽取用的模型 |
| `RESUME` | `0` | `1` 跳过已 ok 的 cluster/subcluster；有未完成部分仍会调 DeepSeek 补跑 |
| `TEST_TIMEOUT_SEC` | `5` | 单个测试超时（complex 建议 `120`） |
| `TEST_LIMIT` | `0` | `0` = 全部测试 |
| `COMPARE_MODE` | `original` | `original` / `expected` |
| `NORMALIZE` | `whitespace` | `whitespace` / `strip` |
| `MODEL_PATH` | `model/Qwen3.8-27B-Q4_K_M.gguf` | eval 的 MDL 模型；文件不存在则 MDL 记 not_available |
| `N_CTX` / `N_GPU_LAYERS` / `N_BATCH` | `4096` / `-1` / `256` | llama.cpp 参数 |
| `MDL_SCORE_BATCH_SIZE` | `64` | MDL logits 的 NumPy 分块大小；显存/内存紧张时可调小 |

## 推荐流程（两阶段）

### 1. 抽取（DeepSeek，CPU）

全量抽取（两个 baseline）：

```bash
sbatch --export=ALL,DEEPSEEK_API_KEY=你的key,BASELINE=both,CLUSTER_ID=all \
  run_extract.sh
```

只补跑失败的（已 ok 的不动，不重新花钱）：

```bash
sbatch --export=ALL,DEEPSEEK_API_KEY=你的key,BASELINE=both,CLUSTER_ID=all,RESUME=1 \
  run_extract.sh
```

只抽某个 baseline / 某个 cluster：

```bash
sbatch --export=ALL,DEEPSEEK_API_KEY=你的key,BASELINE=a,CLUSTER_ID=0 run_extract.sh
```

### 2. 评测（GPU + MDL）

抽取完成后，算全部指标 + 生成报告：

```bash
sbatch --export=ALL,BASELINE=both,CLUSTER_ID=all run_eval.sh
```

只测 baseline-a 的 cluster 0：

```bash
sbatch --export=ALL,BASELINE=a,CLUSTER_ID=0 run_eval.sh
```

MDL 模型不存在时只警告不报错；换模型：

```bash
sbatch --export=ALL,BASELINE=a,MODEL_PATH=/path/to/other.gguf run_eval.sh
```

## signal 方法（三路工具信号 + LLM 判定门 + 抽取）

发现 + 判定门 + 抽取一条命令（`--resume` 续跑；簇级 status 已 ok 的跳过）：

```bash
conda run -n qwen-gguf python -m demo.baselines.run_signal \
  --manifest demo/datasets/codecontest/cluster_manifest.json \
  --dataset-dir demo/datasets/codecontest \
  --results-dir demo/results/codecontest \
  --cluster-id 0 --cluster-id 1 --resume
```

只跑发现（不调抽取/判定 API 用 `--skip-gate`；已 gate 过的簇加 `--resume` 复用判定结果）：

```bash
conda run -n qwen-gguf python -m demo.discovery.discover_cluster \
  --manifest demo/datasets/codecontest/cluster_manifest.json \
  --dataset-dir demo/datasets/codecontest \
  --results-dir demo/results/codecontest --cluster-id 0
```

评测与报告（GPU + MDL；扫描 a/b/signal 三方法同表）：

```bash
conda run -n qwen-gguf python -m demo.eval.run_existing_metrics \
  --manifest demo/datasets/codecontest/cluster_manifest.json \
  --dataset-dir demo/datasets/codecontest \
  --results-dir demo/results/codecontest --baseline signal
conda run -n qwen-gguf python -m demo.eval.report \
  --results-dir demo/results/codecontest --out demo/reports/report_signal.md
```

语义 embedding（C2LLM-0.5B，torch env）首次按簇计算并缓存到 `demo/discovery/cache/`，后续复用；想清掉某簇向量重算用 `--force-semantic`。其他开关（`--test-mode` / `--timeout-sec` / `--test-limit` 等）与 baseline 相同。

## complex 数据集（Scrapy slice）

路径/模式全部由 `DATASET=complex` 自动切换，其余不变：

```bash
sbatch --export=ALL,DEEPSEEK_API_KEY=你的key,DATASET=complex,BASELINE=both run_extract.sh
sbatch --export=ALL,DATASET=complex,BASELINE=both,TEST_TIMEOUT_SEC=120 run_eval.sh
```

（pytest 模式建议 `TEST_TIMEOUT_SEC=120`；结果写到 `demo/results/complex/`，不影响 CodeContests 结果。报告统一由 `demo.eval.report` 生成合并版到 `demo/reports/report.md`。）

## 本地（不经过 Slurm）运行

环境变量形式相同；`DATASET=complex` 时先 `conda activate qwen-gguf`。

```bash
# 抽取一个 cluster（本地跑需 DEEPSEEK_API_KEY）
python3 -m demo.baselines.run_baseline_a --cluster-id 0 --skip-metrics
python3 -m demo.baselines.run_baseline_b --cluster-id 0 --skip-metrics

# 评测已有产物
python3 -m demo.eval.run_existing_metrics --baseline both --test-mode stdio
python3 -m demo.eval.report

# complex 版
python3 -m demo.baselines.run_baseline_a \
  --manifest demo/datasets/complex/cluster_manifest.json \
  --dataset-dir demo/datasets/complex --results-dir demo/results/complex \
  --test-mode pytest --skip-metrics
python3 -m demo.eval.run_existing_metrics \
  --manifest demo/datasets/complex/cluster_manifest.json \
  --dataset-dir demo/datasets/complex --results-dir demo/results/complex \
  --baseline a --test-mode pytest --timeout-sec 120
```

## 日志

`slurm_log/extract_*.log` / `slurm_log/eval_*.log`；每个 cluster/subcluster 有
`status.json`、`metrics.json`、`mdl_metrics.json`（eval 后）等产物。
