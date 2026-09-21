# `demo` 模块说明

推荐从仓库根目录的 [`README.md`](../README.md) 开始。这里仅说明程序接口与产物契约。

## 输入契约

每个数据集包含一个 `cluster_manifest.json`。核心字段为：

- `clusters[].cluster_id`：代码簇编号；
- `clusters[].files[].file_id`：匿名文件编号；
- `clusters/<cluster_id>/original/<file_id>`：模型可见源码；
- `tests`、真实文件名等元数据只供评估使用，不进入抽取提示词。

## 输出布局

整簇方法：

```text
results/<dataset>/baseline_a/<cluster_id>/
  common.py
  refactored/file_*.py
  status.json
  metrics.json
```

局部候选方法：

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

原始响应、调用日志、测试明细和生成代码树默认不提交。发布版本只保留 `demo/results/` 下的聚合报告与结构化汇总。

## 直接调用

```bash
python -m demo.baselines.run_baseline_a --help
python -m demo.baselines.run_signal --help
python -m demo.eval.run_existing_metrics --help
python -m demo.eval.report --help
python -m demo.eval.verify_release
```

统一脚本与参数说明见 [`docs/command.md`](../docs/command.md)。
