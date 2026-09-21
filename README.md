# 多信号代码共性组件抽取

本项目实现并评估一种证据驱动的代码共性组件抽取方法 Signal。该方法从跨文件源码中召回局部复用候选，经大语言模型判定后生成公共组件和成员改写，并通过编译、行为测试与代码规模检查完成验收。

项目包含以下两种对比方法：

- **Baseline A**：以完整文件簇为输入，由大语言模型一次性完成共性识别、公共组件生成和成员改写；
- **Signal**：融合逐字克隆、AST 骨架和语义嵌入信号召回局部候选，经 LLM 判定门筛选后执行局部抽取。

## 实验结果

| 数据集 | 数据类型 | Baseline A 压缩率 | Signal 压缩率 | 行为验证 |
| --- | --- | ---: | ---: | ---: |
| Apache Libcloud load-balancer drivers | 真实生产代码 | 0.05% | **9.88%** | 全部通过 |
| CodeContests | 竞赛代码 | 3.81% | **5.13%** | 24,897 / 24,897 |

压缩率按完整代码池计算，包含公共组件、改写文件和保持原样的文件。两种方法采用相同的编译、行为测试和正收益验收规则。完整实验结果见[实验结果报告](demo/reports/final_report_signal.md)。

## 方法流程

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
  编译、行为测试与收益验收
```

语义通道默认使用 `codefuse-ai/C2LLM-0.5B`，并支持回退到 `Qwen/Qwen3-Embedding-0.6B`。实验配置采用 `semantic_top_frac=0.005`、4 路 API 并发和 65,536 token 的最大输出长度。

## 仓库结构

```text
demo/
  baselines/      对比方法与 Signal 运行器
  discovery/      代码单元解析、信号计算、候选融合与 LLM 判定门
  eval/           编译、行为测试、指标计算、完整池比较与结果校验
  prepare/        CodeContests 与 Libcloud 数据准备程序
  datasets/       CodeContests 数据集；Libcloud 数据由准备程序生成
  results/        两组实验的结构化汇总与数据集报告
  reports/        综合实验结果报告
docs/
  command.md      命令与参数参考
  run_extract.sh  抽取入口
  run_eval.sh     评估入口
tests/            离线回归测试
```

生成的 API 响应、改写代码树、测试轨迹、模型文件和缓存不纳入版本控制。

## 环境安装

项目使用 Python 3.10。Conda 环境定义如下：

```bash
conda env create -f environment.yml
conda activate common-extraction
```

API 配置文件可由示例生成：

```bash
cp .env.example .env
```

抽取阶段需要在 `.env` 或进程环境中配置 `DEEPSEEK_API_KEY`。`.env` 已排除在版本控制之外。

## 数据准备

CodeContests 的匿名化源码、测试清单和聚类清单已包含在仓库中。

Libcloud 数据固定为 `apache/libcloud` v3.9.1、commit `6c867a3ca299f1b16057fab96bff65564c0ac5fe`。以下命令下载固定版本并生成实验切片：

```bash
make prepare-libcloud
```

上游仓库缓存在 `.cache/`，生成的数据集位于 `demo/datasets/libcloud_loadbalancer_real/`。两个目录均不纳入版本控制。

## 实验运行

CodeContests 单簇实验：

```bash
DATASET=codecontest METHOD=all CLUSTER_ID=0 bash docs/run_extract.sh
DATASET=codecontest METHOD=all CLUSTER_ID=0 bash docs/run_eval.sh
```

CodeContests 全量实验：

```bash
DATASET=codecontest METHOD=all WORKERS=4 bash docs/run_extract.sh
DATASET=codecontest METHOD=all bash docs/run_eval.sh
```

Libcloud 实验：

```bash
DATASET=libcloud METHOD=all WORKERS=4 bash docs/run_extract.sh
DATASET=libcloud METHOD=all TEST_TIMEOUT_SEC=120 bash docs/run_eval.sh
```

`METHOD=all` 依次运行 Baseline A 和 Signal，单独运行时可设置为 `a` 或 `signal`。抽取阶段调用远端模型 API，并产生相应的调用费用。

## 配置参数

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DATASET` | `codecontest` | `codecontest` 或 `libcloud` |
| `METHOD` | `all` | `a`、`signal` 或 `all` |
| `CLUSTER_ID` | `all` | 代码簇编号；CodeContests 的有效范围为 `0`–`9` |
| `WORKERS` | `4` | Baseline A、Signal 判定门和抽取任务的 API 并发数 |
| `RESUME` | `0` | 设置为 `1` 时复用已完成产物 |
| `SEMANTIC_TOP_FRAC` | `0.005` | 语义候选边的额外保留比例 |
| `TEST_WORKERS` | `16` | 行为测试并发数 |
| `ENABLE_MDL` | `0` | 设置为 `1` 时启用可选 GGUF MDL 指标 |

完整参数与分阶段命令见[命令参考](docs/command.md)。

## 离线验证

以下命令不访问网络，也不调用模型 API：

```bash
make check
make verify-results
```

`make check` 执行 Python 编译检查和回归测试；`make verify-results` 校验已提交的结构化结果与报告数据是否一致。

## 评估口径

- token 数由 Python tokenizer 统计，不包含注释和文档字符串；
- 行为通过率以原始程序通过的测试为有效测试集合；
- 文件重叠的 Signal 候选通过最大权重文件互斥选择进行组合；
- 编译失败、行为测试未全部通过或无正收益的改写不进入最终代码池；
- 未接受的文件以原始代码计入完整池规模。

## 发布材料

- [综合实验结果报告](demo/reports/final_report_signal.md)
- [CodeContests 实验结果](demo/results/codecontest/EXPERIMENT_REPORT.md)
- [Libcloud 实验结果](demo/results/libcloud_loadbalancer_real/EXPERIMENT_REPORT.md)
- [模块接口与产物格式](demo/README.md)
- [命令与参数参考](docs/command.md)
