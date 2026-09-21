# Demo 模块接口说明

本文档定义数据输入、方法输出和评估程序使用的文件格式。项目概述、环境安装和实验结果见仓库根目录的 [`README.md`](../README.md)。

## 输入格式

每个数据集由 `cluster_manifest.json` 描述。主要字段如下：

- `clusters[].cluster_id`：代码簇编号；
- `clusters[].files[].file_id`：匿名文件编号；
- `clusters/<cluster_id>/original/<file_id>`：模型可见源码；
- `tests`：行为测试数据，仅由评估程序读取；
- 原始文件名与其他元数据：仅用于数据追踪和结果解释，不进入抽取提示词。

## 输出格式

### 整簇方法

```text
results/<dataset>/baseline_a/<cluster_id>/
  common.py
  refactored/file_*.py
  status.json
  metrics.json
```

### 局部候选方法

```text
results/<dataset>/signal/<cluster_id>/
  discovery_candidates.json
  discovery.json
  status.json
  sub_*/
    common.py
    refactored/file_*.py
    status.json
    metrics.json
```

`discovery_candidates.json` 保存多信号召回证据，`discovery.json` 保存 LLM 判定门确认后的候选簇。`status.json` 记录运行状态，`metrics.json` 记录编译、行为测试和代码规模指标。

原始模型响应、调用日志、测试明细和生成代码树属于可再生产物，不纳入版本控制。`demo/results/` 仅保留聚合报告和结构化结果汇总。

## 命令行接口

各模块提供 `--help` 参数：

```bash
python -m demo.baselines.run_baseline_a --help
python -m demo.baselines.run_signal --help
python -m demo.eval.run_existing_metrics --help
python -m demo.eval.report --help
python -m demo.eval.verify_release
```

统一运行入口和参数定义见 [`docs/command.md`](../docs/command.md)。
