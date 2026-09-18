# Baseline Report

结论先看 `pass rate`，再看压缩率；功能不等价的压缩不计为正收益。

pass rate 只统计原始代码已通过的测试子集；具体候选数与有效分母保存在各项 `metrics.json` 的 `tests` 字段中。

MDL 默认按计划保留字段；如果没有配置可返回 logprobs 的参照 LM，则显示 `not_available`。

> pass rate 只在原始代码已通过的有效子集上计算，因此上限为 100%；与 100% 的差值才是改写在有效测试上的功能退化。

## complex_edits

### Main Table

| baseline | cluster | files | pass file % | pass test % | MDL compression | token compression | file API coverage | API usage coverage | conflict rate | status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline_a | 0 | 23 | 100.0% | 100.0% | -0.0049 | -0.0% | 8.3% | 100.0% | not_available | ok |
| baseline_b | 0/sub_0 | 4 | 75.0% | 70.7% | -0.0441 | -1.6% | 0.0% | 0.0% | not_available | tests_failed |
| baseline_b | 0/sub_1 | 3 | 100.0% | 100.0% | -0.0626 | -2.5% | 0.0% | not_available | not_available | ok |
| baseline_b | 0/sub_2 | 2 | 100.0% | 100.0% | -0.1465 | -6.3% | 100.0% | 100.0% | not_available | ok |
| baseline_b | 0/sub_3 | 2 | 100.0% | 100.0% | -0.0729 | -2.8% | 66.7% | 33.3% | not_available | ok |
| baseline_b | 0/sub_4 | 6 | 100.0% | 100.0% | 0.0029 | -1.4% | 0.0% | 0.0% | not_available | ok |
| baseline_b | 0/sub_5 | 2 | 100.0% | 100.0% | -0.2271 | -17.8% | 100.0% | 100.0% | not_available | ok |
| signal | 0/sub_0 | 1 | 100.0% | 100.0% | -0.0290 | -0.4% | 100.0% | 100.0% | not_available | ok |

### baseline_b Discovery

| cluster | discovered subclusters | valid subclusters | noise files | notes |
| --- | --- | --- | --- | --- |
| 0 | 6 | 6 | 4 | ok |

### signal Discovery

| cluster | discovered subclusters | valid subclusters | noise files | notes |
| --- | --- | --- | --- | --- |
| 0 | 1 | 1 | 22 | ok |

### Failure Appendix

- `demo/results/complex_edits/baseline_b/0/sub_0/status.json`: tests_failed - 

