# Apache Libcloud 实验结果

## 数据集

- 上游仓库：`apache/libcloud`；
- 发布版本：`v3.9.1`；
- 固定提交：`6c867a3ca299f1b16057fab96bff65564c0ac5fe`；
- 许可证：Apache-2.0；
- 数据范围：`libcloud/loadbalancer/drivers` 中除 `__init__.py` 外的全部 10 个生产模块；
- 代码规模：7,440 行源码、26,045 个可执行 Python token；
- 行为验证：11 个上游 pytest 模块、416 个测试。

Baseline A 和 Signal 使用相同的 DeepSeek API、上游源码、行为测试和完整池核算规则。模型输入使用匿名文件编号，不包含仓库身份和测试内容。

## 实验结果

| 方法 | 改写前 token | 改写后 token | 节省 token | 压缩率 | 行为验证 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Baseline A | 26,045 | 26,031 | 14 | 0.05% | 416 / 416 |
| Signal | 26,045 | **23,471** | **2,574** | **9.88%** | 全部通过 |

Signal 的最终代码池比 Baseline A 少 2,560 token，压缩率提高 9.83 个百分点。

Signal 完成了两个生产代码实现家族的公共组件抽取：

- Dimension Data 与 NTT CIS 驱动：从 9,982 token 压缩到 7,515 token，压缩率 24.71%，126 个相关测试全部通过；
- AWS ALB 与 ELB 驱动：从 5,163 token 压缩到 5,055 token，压缩率 2.09%，37 个相关测试全部通过。

其余模块保持原样，完整行为验证全部通过。

## 结论

Signal 在 Apache Libcloud 真实生产代码上取得了显著高于 Baseline A 的完整池压缩率。多信号召回能够定位跨文件实现家族，LLM 判定门和局部抽取能够将公共组件生成集中在高置信代码范围内，从而在保持行为一致的同时减少重复实现。

机器可读结果：

- [`full_pool_comparison.json`](full_pool_comparison.json)
- [`embedding_recall_summary.json`](embedding_recall_summary.json)
