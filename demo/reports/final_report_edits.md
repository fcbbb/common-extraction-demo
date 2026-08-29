# 代码共性组件抽取：`_edits` 结果总结

## 1. 实验范围

本报告基于以下两组已完成评估的结果：

- CodeContests：`demo/results/codecontest_edits/`
- Scrapy complex：`demo/results/complex_edits/`

两种方法使用相同的评估口径：`compare_mode=original`、空白归一化、每个文件使用全部可用测试。CodeContests 使用 stdio 测试，Scrapy complex 使用 pytest。MDL 使用 `Qwen3.8-27B-Q4_K_M.gguf`；冲突率对当前 edit-intent 输出不适用，因此显示为 `not_available`。

完整的逐 cluster/subcluster 表格见 [report_edits.md](report_edits.md)。

## 2. 结果总览

| 数据集 | 方法 | 抽取单元 | 生成文件 | 有效文件 | file pass | test pass | token 压缩 | MDL 压缩 | API 覆盖 | API usage | 单元状态 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| CodeContests | baseline-a | 10 簇 | 300 | 280 | 100.0% | 100.0% | 4.0% | -0.29% | 18.0% | 76.0% | 10/10 ok |
| CodeContests | baseline-b | 73 子簇 | 237 | 223 | 91.5% | 95.6% | 10.8% | 0.83% | 92.0% | 76.9% | 59/73 ok |
| Scrapy complex | baseline-a | 1 簇 | 24 | 23 | 100.0% | 100.0% | -0.005% | -0.49% | 8.3% | 100.0% | 1/1 ok |
| Scrapy complex | baseline-b | 6 子簇 | 20 | 19 | 94.7% | 96.4% | -3.7% | -7.06% | 30.0% | 45.5% | 5/6 ok |

这里的“有效文件”是指原始文件在测试中通过、因而进入改写比较分母的文件；baseline-b 的生成文件数只统计进入有效子簇的成员，不包含发现阶段判定为噪声的文件。

## 3. 生成与测试结果

### 3.1 CodeContests

baseline-a 的 10 个 cluster 全部生成成功，300 个改写文件均已落盘并通过编译。原始代码通过测试的 280 个有效文件中，改写版全部通过；对应 24,956 个有效测试也全部通过。

baseline-b 发现了 73 个有效子簇，生成 237 个成员文件；59 个子簇测试完整通过，14 个子簇存在行为退化。加权 file pass rate 为 91.5%，test pass rate 为 95.6%。因此，baseline-b 的子簇划分和局部抽取虽然提高了复用覆盖，但整体功能稳定性低于 baseline-a。

### 3.2 Scrapy complex

baseline-a 对 24 个文件全部生成并通过编译；23 个原始有效文件和 711 个有效测试全部匹配。

baseline-b 发现 6 个有效子簇，生成 20 个成员文件，其中 5 个子簇完整通过，`sub_0` 未完全通过。总体 file pass rate 为 94.7%，test pass rate 为 96.4%。另有 4 个文件被发现阶段判定为噪声，未进入 baseline-b 改写。

## 4. 压缩与复用分析

### 4.1 CodeContests

baseline-a 的 token 总量从 121,644 降至 116,751，整体 token 压缩为 4.0%；但 MDL 从 83,278.17 增至 83,517.04，MDL 压缩为 -0.29%。这说明改写在功能上稳定，但加入 `common.py` 后信息量并未稳定下降。

baseline-b 的 token 总量从 108,391 降至 96,719，token 压缩为 10.8%；MDL 压缩为 0.83%。不过该结果建立在子簇成员范围口径上，且 14 个子簇测试失败，因此不能把全部压缩都视为有效收益。只有同时满足功能通过和真实 API 复用的子簇，才适合作为正向案例。

### 4.2 Scrapy complex

baseline-a 的 token 变化几乎为零（-0.005%），MDL 为 -0.49%，说明端到端方法在框架代码上采取了保守改写。

baseline-b 的 token 压缩为 -3.7%，MDL 压缩为 -7.06%，即加入公共组件和 import 后总体代码信息量反而增加。框架代码的共性主要体现在继承、生命周期和回调协议上，单纯抽取少量 helper 很难抵消公共模块和改写接口的开销。

## 5. 方法结论

1. **功能稳定性：baseline-a 更好。** 两个数据集上 baseline-a 都达到 100% 的有效 file/test pass；baseline-b 分别为 91.5%/95.6% 和 94.7%/96.4%。
2. **算法题上的 baseline-b 有局部压缩收益。** 先发现再抽取能够提高成员文件的 API 覆盖率，但发现错误或子簇内部接口不一致时，会引入功能退化。
3. **框架代码上的抽取收益不足。** Scrapy complex 的两个方法都没有获得稳定的 token 或 MDL 压缩；baseline-b 的压缩还明显为负。
4. **API 覆盖不等于有效抽取。** CodeContests baseline-b 的文件 API 覆盖达到 92.0%，但仍有 14 个子簇测试失败。功能测试应当优先于压缩率和复用覆盖率。
5. **baseline-b 的噪声策略影响覆盖范围。** 被发现阶段判定为噪声的文件保持原样，不应与已改写成员混入同一功能分母解读。

## 6. 失败项

本次评估阶段的失败不是“没有生成代码”，而是已生成并编译的改写代码未通过全部行为测试：

- CodeContests baseline-b：`0/sub_3`、`0/sub_4`、`0/sub_6`、`1/sub_0`、`2/sub_0`、`2/sub_3`、`3/sub_1`、`4/sub_4`、`6/sub_2`、`6/sub_7`、`6/sub_8`、`9/sub_2`、`9/sub_3`、`9/sub_5`
- Scrapy complex baseline-b：`0/sub_0`

详细测试结果、token/MDL 文件和每个单元的状态分别保存在对应的 `metrics.json`、`mdl_metrics.json` 和 `status.json` 中。

## 7. 最终结论

在当前数据和评估口径下，baseline-a 是更可靠的默认方案：它在两个数据集上都保持了完整的有效测试通过率。baseline-b 适合用于探索高同质算法子簇，能够在部分 CodeContests 子簇上取得真实压缩，但需要更严格的发现结果去重、接口一致性检查和失败子簇回退机制。对于 Scrapy 这类以继承与框架回调为主要共性的代码，仅依靠当前 helper 抽取策略不能证明存在稳定的经济收益。
