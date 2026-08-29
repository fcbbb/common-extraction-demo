# Baseline Report

结论先看 `pass rate`，再看压缩率；功能不等价的压缩不计为正收益。

pass rate 只统计原始代码已通过的测试子集；具体候选数与有效分母保存在各项 `metrics.json` 的 `tests` 字段中。

MDL 默认按计划保留字段；如果没有配置可返回 logprobs 的参照 LM，则显示 `not_available`。

> pass rate 只在原始代码已通过的有效子集上计算，因此上限为 100%；与 100% 的差值才是改写在有效测试上的功能退化。

## codecontest_edits

### Main Table

| baseline | cluster | files | pass file % | pass test % | MDL compression | token compression | file API coverage | API usage coverage | conflict rate | status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline_a | 0 | 26 | 100.0% | 100.0% | -0.0030 | 1.5% | 16.7% | 100.0% | not_available | ok |
| baseline_a | 1 | 29 | 100.0% | 100.0% | -0.0310 | 1.7% | 13.3% | 100.0% | not_available | ok |
| baseline_a | 2 | 30 | 100.0% | 100.0% | -0.0220 | 2.0% | 30.0% | 100.0% | not_available | ok |
| baseline_a | 3 | 28 | 100.0% | 100.0% | 0.0039 | 1.3% | 0.0% | 0.0% | not_available | ok |
| baseline_a | 4 | 30 | 100.0% | 100.0% | -0.0140 | 4.9% | 16.7% | 80.0% | not_available | ok |
| baseline_a | 5 | 27 | 100.0% | 100.0% | -0.0210 | 9.1% | 10.0% | 50.0% | not_available | ok |
| baseline_a | 6 | 25 | 100.0% | 100.0% | 0.0021 | 3.4% | 20.0% | 100.0% | not_available | ok |
| baseline_a | 7 | 28 | 100.0% | 100.0% | 0.0181 | 6.2% | 23.3% | 66.7% | not_available | ok |
| baseline_a | 8 | 29 | 100.0% | 100.0% | 0.0208 | 2.0% | 23.3% | 100.0% | not_available | ok |
| baseline_a | 9 | 28 | 100.0% | 100.0% | 0.0026 | 7.5% | 26.7% | 60.0% | not_available | ok |
| baseline_b | 0/sub_0 | 2 | 100.0% | 100.0% | -0.0493 | 30.5% | 100.0% | 100.0% | not_available | ok |
| baseline_b | 0/sub_1 | 3 | 100.0% | 100.0% | -0.5361 | -29.3% | 100.0% | 75.0% | not_available | ok |
| baseline_b | 0/sub_2 | 2 | 100.0% | 100.0% | -0.0584 | 7.0% | 100.0% | 100.0% | not_available | ok |
| baseline_b | 0/sub_3 | 1 | 0.0% | 80.3% | 0.3410 | 33.9% | 100.0% | 100.0% | not_available | tests_failed |
| baseline_b | 0/sub_4 | 4 | 75.0% | 73.0% | 0.0089 | -14.5% | 100.0% | 100.0% | not_available | tests_failed |
| baseline_b | 0/sub_5 | 2 | 100.0% | 100.0% | -0.2699 | -11.0% | 100.0% | 100.0% | not_available | ok |
| baseline_b | 0/sub_6 | 2 | 50.0% | 93.1% | -0.0452 | 7.1% | 100.0% | 100.0% | not_available | tests_failed |
| baseline_b | 1/sub_0 | 11 | 90.9% | 92.2% | 0.0362 | 8.2% | 100.0% | 100.0% | not_available | tests_failed |
| baseline_b | 1/sub_1 | 4 | 100.0% | 100.0% | -0.0436 | 10.8% | 100.0% | 100.0% | not_available | ok |
| baseline_b | 1/sub_2 | 9 | 100.0% | 100.0% | 0.0500 | 16.6% | 100.0% | 100.0% | not_available | ok |
| baseline_b | 1/sub_3 | 2 | 100.0% | 100.0% | -0.0461 | 9.1% | 100.0% | 100.0% | not_available | ok |
| baseline_b | 1/sub_4 | 3 | 100.0% | 100.0% | -0.0126 | 3.9% | 100.0% | 100.0% | not_available | ok |
| baseline_b | 1/sub_5 | 5 | 100.0% | 100.0% | -0.0501 | 1.0% | 100.0% | 100.0% | not_available | ok |
| baseline_b | 2/sub_0 | 4 | 75.0% | 98.9% | 0.1454 | 20.1% | 100.0% | 100.0% | not_available | tests_failed |
| baseline_b | 2/sub_1 | 2 | 100.0% | 100.0% | 0.1011 | -1.0% | 50.0% | 100.0% | not_available | ok |
| baseline_b | 2/sub_2 | 3 | 100.0% | 100.0% | 0.0444 | 4.7% | 100.0% | 100.0% | not_available | ok |
| baseline_b | 2/sub_3 | 7 | 71.4% | 98.2% | 0.1141 | 22.4% | 100.0% | 87.5% | not_available | tests_failed |
| baseline_b | 2/sub_4 | 3 | 100.0% | 100.0% | 0.2355 | 15.7% | 100.0% | 100.0% | not_available | ok |
| baseline_b | 2/sub_5 | 5 | 100.0% | 100.0% | 0.0065 | 4.7% | 100.0% | 100.0% | not_available | ok |
| baseline_b | 2/sub_6 | 2 | 100.0% | 100.0% | 0.1471 | 5.1% | 100.0% | 100.0% | not_available | ok |
| baseline_b | 3/sub_0 | 4 | 100.0% | 100.0% | -0.2799 | -5.4% | 100.0% | 100.0% | not_available | ok |
| baseline_b | 3/sub_1 | 4 | 25.0% | 19.8% | 0.0197 | 12.5% | 100.0% | 100.0% | not_available | tests_failed |
| baseline_b | 3/sub_2 | 6 | 100.0% | 100.0% | 0.0419 | 14.1% | 50.0% | 50.0% | not_available | ok |
| baseline_b | 3/sub_3 | 1 | 100.0% | 100.0% | -0.0914 | 6.7% | 66.7% | 100.0% | not_available | ok |
| baseline_b | 3/sub_4 | 2 | 100.0% | 100.0% | 0.2726 | 30.6% | 100.0% | 100.0% | not_available | ok |
| baseline_b | 3/sub_5 | 2 | 100.0% | 100.0% | -0.0026 | 2.3% | 100.0% | 100.0% | not_available | ok |
| baseline_b | 3/sub_6 | 3 | 100.0% | 100.0% | 0.0890 | 4.0% | 100.0% | 100.0% | not_available | ok |
| baseline_b | 3/sub_7 | 3 | 100.0% | 100.0% | -0.0673 | 4.0% | 100.0% | 100.0% | not_available | ok |
| baseline_b | 4/sub_0 | 2 | 100.0% | 100.0% | -0.0261 | 44.5% | 100.0% | 25.0% | not_available | ok |
| baseline_b | 4/sub_1 | 6 | 100.0% | 100.0% | 0.0869 | 4.6% | 100.0% | 100.0% | not_available | ok |
| baseline_b | 4/sub_2 | 2 | 100.0% | 100.0% | 0.1335 | -5.9% | 100.0% | 66.7% | not_available | ok |
| baseline_b | 4/sub_3 | 6 | 100.0% | 100.0% | -0.1106 | 7.2% | 83.3% | 62.5% | not_available | ok |
| baseline_b | 4/sub_4 | 2 | 0.0% | 99.6% | 0.2733 | 50.0% | 100.0% | 40.0% | not_available | tests_failed |
| baseline_b | 4/sub_5 | 2 | 100.0% | 100.0% | -0.2449 | -20.4% | 100.0% | 33.3% | not_available | ok |
| baseline_b | 4/sub_6 | 3 | 100.0% | 100.0% | -0.4486 | -14.9% | 100.0% | 50.0% | not_available | ok |
| baseline_b | 5/sub_0 | 2 | 100.0% | 100.0% | -0.1853 | 0.3% | 100.0% | 100.0% | not_available | ok |
| baseline_b | 5/sub_1 | 2 | 100.0% | 100.0% | -0.0239 | 20.6% | 100.0% | 100.0% | not_available | ok |
| baseline_b | 5/sub_2 | 2 | 100.0% | 100.0% | 0.1340 | 58.0% | 100.0% | 100.0% | not_available | ok |
| baseline_b | 5/sub_3 | 1 | 100.0% | 100.0% | 0.2032 | 12.4% | 100.0% | 100.0% | not_available | ok |
| baseline_b | 5/sub_4 | 3 | 100.0% | 100.0% | 0.0652 | 20.0% | 0.0% | 0.0% | not_available | ok |
| baseline_b | 5/sub_5 | 3 | 100.0% | 100.0% | -0.0120 | 0.0% | 0.0% | not_available | not_available | ok |
| baseline_b | 5/sub_6 | 2 | 100.0% | 100.0% | -0.1540 | 34.1% | 100.0% | 100.0% | not_available | ok |
| baseline_b | 5/sub_7 | 2 | 100.0% | 100.0% | -0.0246 | 0.0% | 0.0% | not_available | not_available | ok |
| baseline_b | 6/sub_0 | 3 | 100.0% | 100.0% | -0.0411 | 11.3% | 100.0% | 100.0% | not_available | ok |
| baseline_b | 6/sub_1 | 2 | 100.0% | 100.0% | 0.0301 | 6.7% | 100.0% | 100.0% | not_available | ok |
| baseline_b | 6/sub_2 | 2 | 50.0% | 97.6% | 0.0184 | 16.9% | 100.0% | 66.7% | not_available | tests_failed |
| baseline_b | 6/sub_3 | 2 | 100.0% | 100.0% | -0.1783 | 21.8% | 100.0% | 100.0% | not_available | ok |
| baseline_b | 6/sub_4 | 1 | 100.0% | 100.0% | -0.1953 | 5.2% | 100.0% | 100.0% | not_available | ok |
| baseline_b | 6/sub_5 | 2 | 100.0% | 100.0% | 0.1383 | 28.7% | 100.0% | 100.0% | not_available | ok |
| baseline_b | 6/sub_6 | 2 | 100.0% | 100.0% | -0.2219 | -6.9% | 100.0% | 100.0% | not_available | ok |
| baseline_b | 6/sub_7 | 2 | 50.0% | 87.7% | -0.0413 | -0.9% | 100.0% | 50.0% | not_available | tests_failed |
| baseline_b | 6/sub_8 | 2 | 50.0% | 95.0% | -0.2111 | 0.3% | 100.0% | 100.0% | not_available | tests_failed |
| baseline_b | 7/sub_0 | 3 | 100.0% | 100.0% | 0.1521 | 12.9% | 100.0% | 66.7% | not_available | ok |
| baseline_b | 7/sub_1 | 2 | 100.0% | 100.0% | -0.2373 | -3.7% | 100.0% | 100.0% | not_available | ok |
| baseline_b | 7/sub_2 | 2 | 100.0% | 100.0% | 0.1714 | 18.1% | 100.0% | 100.0% | not_available | ok |
| baseline_b | 7/sub_3 | 2 | 100.0% | 100.0% | 0.0327 | 9.3% | 100.0% | 100.0% | not_available | ok |
| baseline_b | 7/sub_4 | 3 | 100.0% | 100.0% | -0.1178 | -7.8% | 66.7% | 100.0% | not_available | ok |
| baseline_b | 7/sub_5 | 2 | 100.0% | 100.0% | 0.0281 | 12.8% | 100.0% | 100.0% | not_available | ok |
| baseline_b | 7/sub_6 | 2 | 100.0% | 100.0% | -0.0114 | 0.0% | 0.0% | not_available | not_available | ok |
| baseline_b | 8/sub_0 | 3 | 100.0% | 100.0% | 0.0082 | 8.2% | 100.0% | 66.7% | not_available | ok |
| baseline_b | 8/sub_1 | 8 | 100.0% | 100.0% | 0.0908 | 18.5% | 100.0% | 66.7% | not_available | ok |
| baseline_b | 8/sub_2 | 5 | 100.0% | 100.0% | 0.0631 | 22.9% | 100.0% | 100.0% | not_available | ok |
| baseline_b | 8/sub_3 | 3 | 100.0% | 100.0% | 0.0212 | 11.4% | 100.0% | 100.0% | not_available | ok |
| baseline_b | 8/sub_4 | 2 | 100.0% | 100.0% | 0.1368 | 8.7% | 100.0% | 50.0% | not_available | ok |
| baseline_b | 8/sub_5 | 2 | 100.0% | 100.0% | 0.1154 | 23.1% | 100.0% | 25.0% | not_available | ok |
| baseline_b | 8/sub_6 | 2 | 100.0% | 100.0% | -0.0006 | 13.0% | 100.0% | 100.0% | not_available | ok |
| baseline_b | 9/sub_0 | 3 | 100.0% | 100.0% | 0.0925 | 17.6% | 100.0% | 100.0% | not_available | ok |
| baseline_b | 9/sub_1 | 3 | 100.0% | 100.0% | 0.0111 | 30.5% | 100.0% | 27.3% | not_available | ok |
| baseline_b | 9/sub_2 | 3 | 66.7% | 73.1% | -0.0534 | 4.8% | 100.0% | 40.0% | not_available | tests_failed |
| baseline_b | 9/sub_3 | 7 | 71.4% | 55.1% | 0.0116 | 13.4% | 100.0% | 100.0% | not_available | tests_failed |
| baseline_b | 9/sub_4 | 2 | 100.0% | 100.0% | -0.0689 | 2.1% | 100.0% | 100.0% | not_available | ok |
| baseline_b | 9/sub_5 | 3 | 66.7% | 89.2% | 0.0262 | 9.4% | 100.0% | 100.0% | not_available | tests_failed |
| baseline_b | 9/sub_6 | 2 | 100.0% | 100.0% | -0.0871 | 4.6% | 0.0% | 0.0% | not_available | ok |

### Baseline-b Discovery

| cluster | discovered subclusters | valid subclusters | noise files | notes |
| --- | --- | --- | --- | --- |
| 0 | 7 | 7 | 12 | ok |
| 1 | 6 | 6 | 2 | ok |
| 2 | 7 | 7 | 4 | ok |
| 3 | 8 | 8 | 3 | ok |
| 4 | 7 | 7 | 7 | ok |
| 5 | 8 | 8 | 12 | ok |
| 6 | 9 | 9 | 10 | ok |
| 7 | 7 | 7 | 13 | ok |
| 8 | 7 | 7 | 4 | ok |
| 9 | 7 | 7 | 5 | ok |

### Failure Appendix

- `demo/results/codecontest_edits/baseline_b/0/sub_3/status.json`: tests_failed - 
- `demo/results/codecontest_edits/baseline_b/0/sub_4/status.json`: tests_failed - 
- `demo/results/codecontest_edits/baseline_b/0/sub_6/status.json`: tests_failed - 
- `demo/results/codecontest_edits/baseline_b/1/sub_0/status.json`: tests_failed - 
- `demo/results/codecontest_edits/baseline_b/2/sub_0/status.json`: tests_failed - 
- `demo/results/codecontest_edits/baseline_b/2/sub_3/status.json`: tests_failed - 
- `demo/results/codecontest_edits/baseline_b/3/sub_1/status.json`: tests_failed - 
- `demo/results/codecontest_edits/baseline_b/4/sub_4/status.json`: tests_failed - 
- `demo/results/codecontest_edits/baseline_b/6/sub_2/status.json`: tests_failed - 
- `demo/results/codecontest_edits/baseline_b/6/sub_7/status.json`: tests_failed - 
- `demo/results/codecontest_edits/baseline_b/6/sub_8/status.json`: tests_failed - 
- `demo/results/codecontest_edits/baseline_b/9/sub_2/status.json`: tests_failed - 
- `demo/results/codecontest_edits/baseline_b/9/sub_3/status.json`: tests_failed - 
- `demo/results/codecontest_edits/baseline_b/9/sub_5/status.json`: tests_failed - 

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

### Baseline-b Discovery

| cluster | discovered subclusters | valid subclusters | noise files | notes |
| --- | --- | --- | --- | --- |
| 0 | 6 | 6 | 4 | ok |

### Failure Appendix

- `demo/results/complex_edits/baseline_b/0/sub_0/status.json`: tests_failed - 

