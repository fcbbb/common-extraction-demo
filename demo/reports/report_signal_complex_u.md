# Baseline Report

结论先看 `pass rate`，再看压缩率；功能不等价的压缩不计为正收益。

pass rate 只统计原始代码已通过的测试子集；具体候选数与有效分母保存在各项 `metrics.json` 的 `tests` 字段中。

MDL 默认按计划保留字段；如果没有配置可返回 logprobs 的参照 LM，则显示 `not_available`。

> pass rate 只在原始代码已通过的有效子集上计算，因此上限为 100%；与 100% 的差值才是改写在有效测试上的功能退化。

## complex_signal_u

### Main Table

| baseline | cluster | files | pass file % | pass test % | MDL compression | token compression | file API coverage | API usage coverage | conflict rate | status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| signal | 0/sub_0 | 5 | 100.0% | 100.0% | -0.0274 | -3.1% | 0.0% | 0.0% | not_available | ok |
| signal | 0/sub_1 | 2 | 100.0% | 100.0% | -0.1438 | -3.6% | 100.0% | 100.0% | not_available | ok |
| signal | 0/sub_2 | not_available | not_available | not_available | not_available | not_available | not_available | not_available | not_available | missing_extraction |
| signal | 0/sub_3 | 2 | 100.0% | 100.0% | -1.0222 | -42.4% | 0.0% | 0.0% | not_available | ok |
| signal | 0/sub_4 | 2 | 100.0% | 100.0% | -0.1155 | -5.6% | 100.0% | 100.0% | not_available | ok |
| signal | 0/sub_5 | not_available | not_available | not_available | not_available | not_available | not_available | not_available | not_available | missing_extraction |

### signal Discovery

| cluster | discovered subclusters | valid subclusters | noise files | notes |
| --- | --- | --- | --- | --- |
| 0 | 6 | 6 | 11 | ok |

### Failure Appendix

- `demo/results/complex_signal_u/signal/0/sub_2/status.json`: missing_extraction - No existing extraction under /home/xiaoheng/demo_common_extraction/demo/results/complex_signal_u/signal/0/sub_2
- `demo/results/complex_signal_u/signal/0/sub_5/status.json`: missing_extraction - No existing extraction under /home/xiaoheng/demo_common_extraction/demo/results/complex_signal_u/signal/0/sub_5

