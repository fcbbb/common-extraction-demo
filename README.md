# 代码共性组件抽取 Demo

从一组功能相似的代码文件中抽取公共组件（`common.py`），并把各文件改写为使用该组件的形态，然后从功能等价性、压缩率、覆盖率、冲突率等维度评估抽取质量。

本仓库已经包含两个 baseline 的完整结果，也可以跑你自己的抽取方法（见下文「跑你自己的抽取方法」）。

## 目录结构

```
demo/
  baselines/        # baseline a/b 的抽取实现（调 DeepSeek API）
  eval/             # 评估：编译、测试、MDL、token、API 覆盖率、冲突率、报告
  prepare/          # 数据预处理（prepare_dataset.py / prepare_dataset_complex.py）
  datasets/         # 数据集
    codecontest/    #   CodeContests（10 个 cluster，stdio 测试）
    complex/        #   Scrapy 切片（1 个 cluster，pytest 测试）
  results/          # 抽取结果
    codecontest/    #   CodeContests 的 baseline_a/b 结果
    complex/        #   Scrapy 切片的 baseline_a/b 结果
  reports/          # 报告（report.md 由 demo.eval.report 生成，final_report.md 为分析文档）
  scripts/          # MDL 重算脚本（recompute_mdl_b.py 等）
  README.md         # demo 内部详细说明
docs/               # 运行脚本（run_extract/run_eval/recompute_mdl）+ 设计文档
model/              # GGUF 模型（MDL 评估用）
Librarian/data/     # 上游原始数据（datasets 的来源）
environment.yml     # qwen-gguf conda 环境定义
```

## 环境准备

```bash
conda env create -f environment.yml
conda activate qwen-gguf
```

注意：`llama-cpp-python` 需要 **CUDA 源码构建**（MDL 评估必须），直接 `conda env create` 装的是 CPU 版。按 [environment.yml](environment.yml) 顶部注释里的 `CMAKE_ARGS="-DGGML_CUDA=on" FORCE_CMAKE=1 pip install llama-cpp-python==0.3.35` 重装。

## 数据集

两个数据集都已生成好（`demo/datasets/codecontest/`、`demo/datasets/complex/`），无需再 prepare。如需重新生成：

```bash
conda run -n qwen-gguf python -m demo.prepare.prepare_dataset           # CodeContests
conda run -n qwen-gguf python -m demo.prepare.prepare_dataset_complex   # Scrapy 切片
```

## 快速跑通已有 baseline

两个阶段分开跑：

```bash
# 阶段 1：抽取（DeepSeek API，CPU 即可）
# 需要先 export DEEPSEEK_API_KEY=...
bash docs/run_extract.sh        # 默认 DATASET=codecontest, BASELINE=both, CLUSTER_ID=all

# 阶段 2：评估 + 报告（需要 GPU + model/Qwen3.8-27B-Q4_K_M.gguf）
bash docs/run_eval.sh           # 默认 BASELINE=a
```

常用开关（详见 docs/command.md）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `DATASET` | `codecontest` | `codecontest` / `complex` |
| `BASELINE` | extract: `both`；eval: `a` | `a` / `b` / `both` |
| `CLUSTER_ID` | `all` | `0..9` 或 `all` |
| `RESUME` | `0` | `1` 跳过已 ok 的 cluster |
| `MODEL_PATH` | `model/Qwen3.8-27B-Q4_K_M.gguf` | MDL 用模型 |
| `TEST_TIMEOUT_SEC` | `5`（complex 建议 `120`） | 单测试超时 |

只重算 MDL（不动其他指标）：`bash docs/recompute_mdl.sh`。

## 跑你自己的抽取方法

核心思路：**抽取方法自由，评估管线固定**。你只需要按下面的输出契约把你的结果写进一个结果目录，剩下的评估和报告全部复用。

### 输入契约（你的方法读什么）

- manifest：`demo/datasets/codecontest/cluster_manifest.json`，顶层有 `clusters` 列表，每个 cluster 有：
  - `cluster_id`（`"0"` ~ `"9"`）
  - `files`：成员列表，每项含 `file_id`（如 `file_000.py`）、`name`、`difficulty`、`tests`（public/private/generated 输入输出）
- 源码文件：`demo/datasets/codecontest/clusters/<cluster_id>/original/file_*.py`，只有 `solution` 字段写进了文件；测试和题目信息只在 manifest 里（评估时才用，不会进你的 prompt）

### 输出契约（评估器读什么）

评估和报告代码硬编码了 `baseline_a` / `baseline_b` 两个子目录名，但结果目录可以任选。建议用一个新目录，例如 `demo/results_mymethod/`：

**baseline_a 布局（整簇抽取）** —— 每个 cluster 一个目录：

```
demo/results_mymethod/baseline_a/<cluster_id>/
  common.py            # 抽取出的公共代码
  refactored/          # 改写后的成员文件，文件名与 original 对应（file_*.py）
```

**baseline_b 布局（子簇抽取）** —— 先做子簇发现，再逐子簇抽取：

```
demo/results_mymethod/baseline_b/<cluster_id>/
  discovery.json       # {"clusters": [{"cluster_id": "sub_0", "members": ["file_000.py", ...]}], "noise": [...]}
  sub_0/
    common.py
    refactored/
  sub_1/
    ...
```

### 评估你的结果

```bash
# 对已有抽取产物跑编译 + 测试 + 全部指标（缺 common.py/refactored 的 cluster 记为 missing_extraction）
conda run -n qwen-gguf python -m demo.eval.run_existing_metrics \
  --manifest demo/datasets/codecontest/cluster_manifest.json \
  --dataset-dir demo/datasets/codecontest \
  --results-dir demo/results_mymethod \
  --baseline a

# 生成报告（baseline a + b 都会扫；--results-dir 可传多个，生成合并报告）
conda run -n qwen-gguf python -m demo.eval.report \
  --results-dir demo/results_mymethod \
  --results-dir demo/results/codecontest \
  --out demo/reports/report_mymethod.md
```

评估开关：`--test-limit`（每文件测试数，0=全部）、`--compare-mode original|expected`、`--normalize whitespace|strip`（默认严格模式 `original`+`whitespace`；Librarian 兼容的宽松模式是 `expected`+`strip`，见 docs/command.md）、`--test-mode stdio|pytest`（complex 数据集用 pytest）。

### 指标怎么解读

| 指标 | 含义 |
|---|---|
| pass file % / pass test % | 功能等价性。先用原始代码筛出通过的测试，再计算重构版在该子集上的比例；分母分别是含原始通过测试的文件数和原始通过测试数 |
| MDL compression | 改写后 vs 原文件在参照 LM（GGUF 模型）下的 log-likelihood 压缩率 |
| token compression | 改写前后 token 数压缩率 |
| file API coverage / API usage coverage | 公共代码中被成员文件实际使用的 API 占比 |
| conflict rate | 改写与原始实现的冲突率 |

### 用自己的方法名而不是 baseline_a/b

`run_existing_metrics.py` 和 `report.py` 都硬编码了子目录名：`demo/eval/run_existing_metrics.py` 的 `run_baseline_a_metrics`/`run_baseline_b_metrics` 写入 `results_dir/"baseline_a"`，`demo/eval/report.py` 的 `baseline_a_rows`/`baseline_b_rows` 读取同名目录。要新增方法名，改这两处即可，评估逻辑不用动。

## 相关文档

- [docs/command.md](docs/command.md) — 常用命令速查
- [docs/baseline_plan.md](docs/baseline_plan.md) — baseline 设计
- [docs/代码共性组件抽取Demo版技术方案.md](docs/代码共性组件抽取Demo版技术方案.md) — 技术方案
- [demo/README.md](demo/README.md) — demo 内部细节（含 complex 环境安装说明）
