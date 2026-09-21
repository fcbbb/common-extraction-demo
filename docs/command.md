# 命令与参数参考

所有命令均从仓库根目录运行。脚本会自动定位项目路径，不包含机器相关的绝对路径。

## 环境与自检

```bash
conda env create -f environment.yml
conda activate common-extraction
cp .env.example .env
make check
make verify-results
```

## 数据准备

CodeContests 已随仓库提供。Libcloud 首次使用时执行：

```bash
make prepare-libcloud
```

已有的上游 checkout 可通过 `--upstream` 指定：

```bash
python -m demo.prepare.prepare_dataset_libcloud_loadbalancer_real \
  --upstream /path/to/apache-libcloud
```

## 抽取

```bash
# CodeContests 单簇实验
DATASET=codecontest METHOD=all CLUSTER_ID=0 bash docs/run_extract.sh

# CodeContests 全量并行实验
DATASET=codecontest METHOD=all WORKERS=4 bash docs/run_extract.sh

# Libcloud
DATASET=libcloud METHOD=all WORKERS=4 bash docs/run_extract.sh

# 断点续跑
DATASET=codecontest METHOD=signal RESUME=1 bash docs/run_extract.sh
```

抽取阶段需要 `DEEPSEEK_API_KEY`。该变量可配置在已被 Git 忽略的 `.env` 中，也可由进程环境提供。

## 评估

```bash
DATASET=codecontest METHOD=all bash docs/run_eval.sh
DATASET=libcloud METHOD=all TEST_TIMEOUT_SEC=120 bash docs/run_eval.sh
```

MDL 默认关闭。安装支持 logprob 的 `llama-cpp-python` 并设置 `MODEL_PATH` 后，可通过 `ENABLE_MDL=1` 启用。

## 参数

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `DATASET` | `codecontest` | `codecontest` / `libcloud` |
| `METHOD` | `all` | `a` / `b` / `signal` / `all` |
| `CLUSTER_ID` | `all` | 指定代码簇 |
| `RESULTS_DIR` | `demo/results/<dataset>` | 自定义结果目录 |
| `WORKERS` | `4` | API 并发数 |
| `RESUME` | `0` | 复用已完成结果 |
| `API_TIMEOUT_SEC` | `1800` | 单次 API 请求超时 |
| `MAX_OUTPUT_TOKENS` | `65536` | 最大生成长度 |
| `SEMANTIC_TOP_FRAC` | `0.005` | 语义候选边保留比例 |
| `TEST_TIMEOUT_SEC` | CodeContests `10`，Libcloud `120` | 单测试超时 |
| `TEST_WORKERS` | `16` | 测试并发数 |
| `REPORT_OUT` | `demo/reports/report_<dataset>.md` | 明细报告输出路径 |

## 分阶段运行

```bash
# 确定性发现，不使用语义模型和远端 API
python -m demo.discovery.discover_cluster \
  --manifest demo/datasets/codecontest/cluster_manifest.json \
  --dataset-dir demo/datasets/codecontest \
  --results-dir demo/results/smoke \
  --cluster-id 0 --skip-semantic --skip-gate

# 对现有 Signal 产物重新计算指标
python -m demo.eval.run_existing_metrics \
  --baseline signal \
  --manifest demo/datasets/codecontest/cluster_manifest.json \
  --dataset-dir demo/datasets/codecontest \
  --results-dir demo/results/codecontest \
  --skip-mdl

# 重新生成 CodeContests 公平完整池汇总
python -m demo.eval.compare_codecontest_pool
```

## Slurm

两个 shell 脚本包含标准 `#SBATCH` 资源声明，适用于 Slurm 提交：

```bash
sbatch --export=ALL,DATASET=codecontest,METHOD=all docs/run_extract.sh
sbatch --export=ALL,DATASET=codecontest,METHOD=all docs/run_eval.sh
```

Slurm 默认输出写入提交目录；生成结果和本地日志均被 Git 忽略。
