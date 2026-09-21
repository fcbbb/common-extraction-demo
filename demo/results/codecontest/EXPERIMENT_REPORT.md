# CodeContests 实验结果

## 实验概况

- 数据规模：10 个代码簇、300 个 Python solution；
- 候选测试：29,966 个；
- 最终有效测试：24,897 个；
- Signal 语义召回参数：`semantic_top_frac = 0.005`；
- API 并发数：4；
- API 最大输出：65,536 token；
- 单测试进程超时：10 秒。

Baseline A 和 Signal 使用相同的编译、行为测试、正收益与完整池核算规则。

## 实验结果

| 方法 | 改写前 token | 改写后 token | 节省 token | 压缩率 | 行为验证 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Baseline A | 121,644 | 117,004 | 4,640 | 3.81% | 24,897 / 24,897 |
| Signal | 121,644 | **115,399** | **6,245** | **5.13%** | 24,897 / 24,897 |

Signal 比 Baseline A 多节省 1,605 token，完整池压缩率提高 1.32 个百分点。两种方法的最终代码均通过全部有效行为测试。

## 结论

Signal 在 CodeContests 数据集上取得了更高的有效代码压缩率。多信号候选发现和局部抽取机制能够识别细粒度公共实现，并在保持程序行为一致的前提下减少完整代码池规模。

结构化实验数据见 [`full_recompute_summary.json`](full_recompute_summary.json)。
