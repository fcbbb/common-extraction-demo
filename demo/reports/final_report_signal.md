# Signal 代码共性组件抽取实验结果报告

> 实验数据：Apache Libcloud 真实生产代码、CodeContests 竞赛代码
>
> 对比方法：Signal、Baseline A
>
> 核心指标：行为测试通过率、完整代码池 token 压缩率

## 1. 实验结论

Signal 在两个数据集上均取得了高于 Baseline A 的有效压缩率，并保持最终代码行为验证全部通过。

| 数据集 | Baseline A | Signal | Signal 提升 | 行为验证 |
| --- | ---: | ---: | ---: | ---: |
| Apache Libcloud load-balancer drivers | 0.05% | **9.88%** | **+9.83 个百分点** | 全部通过 |
| CodeContests | 3.81% | **5.13%** | **+1.32 个百分点** | 24,897 / 24,897 |

实验结果表明，Signal 能够在保持功能等价的前提下，更有效地识别和抽取跨文件公共实现。其优势在真实生产代码上尤为明显。

## 2. 方法说明

### 2.1 Baseline A

Baseline A 将整个文件簇一次性交给大模型，由模型直接完成公共组件识别、`common.py` 生成和成员文件改写。

### 2.2 Signal

Signal 将公共组件发现和代码生成拆分为多个阶段：

1. 从源码中解析函数、方法和类等代码单元；
2. 使用逐字克隆、AST 骨架和语义嵌入召回跨文件候选；
3. 融合多路信号，并使用 LLM 判定门确认共享语义和抽取边界；
4. 针对通过判定的局部候选生成公共组件和成员改写；
5. 通过编译、行为测试和 token 收益检查完成最终验收。

Signal 的语义召回参数为 `semantic_top_frac = 0.005`，嵌入模型为 `C2LLM-0.5B`。模型调用使用 `deepseek-flash`，temperature 为 0，API 阶段采用 4 路并发。

### 2.3 统一验收规则

两种方法采用相同的最终验收标准：

- 改写代码必须成功编译；
- 有效行为测试必须全部通过；
- 公共组件和改写文件合计后必须产生正 token 收益；
- 未通过验收的文件保持原样；
- 最终指标按完整代码池统一核算。

## 3. 数据集

### 3.1 Apache Libcloud

Libcloud 数据来自 `apache/libcloud` v3.9.1，固定提交为 `6c867a3ca299f1b16057fab96bff65564c0ac5fe`。实验覆盖 `libcloud/loadbalancer/drivers` 中除 `__init__.py` 外的全部 10 个生产模块：

- 7,440 行真实生产源码；
- 26,045 个可执行 Python token；
- 11 个上游 pytest 模块；
- 416 个行为测试。

### 3.2 CodeContests

CodeContests 实验覆盖 10 个代码簇，每簇包含 30 个 Python solution：

- 共 300 个源文件；
- 29,966 个 public、private 和 generated 候选测试；
- 24,897 个原始代码有效测试进入最终行为验证；
- 测试采用 stdin/stdout 执行和空白归一化比较。

## 4. 实验结果

### 4.1 完整结果

| 数据集 | 方法 | 改写前 token | 改写后 token | 节省 token | 压缩率 | 行为验证 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Apache Libcloud | Baseline A | 26,045 | 26,031 | 14 | 0.05% | 416 / 416 |
| Apache Libcloud | Signal | 26,045 | **23,471** | **2,574** | **9.88%** | 全部通过 |
| CodeContests | Baseline A | 121,644 | 117,004 | 4,640 | 3.81% | 24,897 / 24,897 |
| CodeContests | Signal | 121,644 | **115,399** | **6,245** | **5.13%** | 24,897 / 24,897 |

### 4.2 Apache Libcloud 结果

Signal 将完整代码池从 26,045 token 压缩到 23,471 token，共节省 2,574 token，压缩率达到 9.88%。Baseline A 节省 14 token，压缩率为 0.05%。Signal 的最终代码池比 Baseline A 少 2,560 token。

Signal 完成了两个生产代码实现家族的公共组件抽取：

- Dimension Data 与 NTT CIS 驱动：压缩率 24.71%，126 个相关测试全部通过；
- AWS ALB 与 ELB 驱动：压缩率 2.09%，37 个相关测试全部通过。

未参与改写的模块保持原样，完整测试集验证通过。

### 4.3 CodeContests 结果

Signal 将完整代码池从 121,644 token 压缩到 115,399 token，共节省 6,245 token，压缩率达到 5.13%。Baseline A 节省 4,640 token，压缩率为 3.81%。Signal 比 Baseline A 多节省 1,605 token。

两种方法最终均通过 24,897 个有效测试，Signal 在功能等价条件下获得了更高的完整池压缩率。

## 5. Signal 的方法优势

### 5.1 局部公共实现发现

Signal 能够在大型、异质代码目录中定位局部实现家族，并针对真正相关的文件执行公共组件抽取。

### 5.2 多信号互补

逐字克隆用于识别直接重复，AST 骨架用于识别结构对应，语义嵌入用于识别命名或表述不同但功能相关的实现。多路证据融合提高了候选发现的完整性。

### 5.3 LLM 判定门

LLM 判定门在代码生成前检查候选的共享语义、接口兼容性和抽取边界，使后续生成聚焦于高置信候选。

### 5.4 局部改写与风险隔离

Signal 以局部候选为改写单元。未参与公共组件抽取的文件保持不变，抽取结果也能够分别执行编译、测试和收益验收。

### 5.5 完整代码池收益

最终压缩结果包含公共组件、改写文件和未改写文件，反映了公共组件落地后的完整代码规模。

## 6. 总结

实验结果支持以下结论：

1. Signal 在 Apache Libcloud 真实生产代码上实现 9.88% 的完整池压缩率，显著高于 Baseline A 的 0.05%；
2. Signal 在 CodeContests 上实现 5.13% 的完整池压缩率，高于 Baseline A 的 3.81%；
3. 两个数据集的最终改写均通过全部有效行为验证；
4. 多信号召回、LLM 判定门和局部抽取共同提升了公共组件发现与代码压缩效果；
5. Signal 适合从包含局部重复实现的代码集合中发现并生成可复用公共组件。

## 7. 结果与复现材料

- [Libcloud 中文实验结果](../results/libcloud_loadbalancer_real/EXPERIMENT_REPORT.md)
- [Libcloud 完整池数据](../results/libcloud_loadbalancer_real/full_pool_comparison.json)
- [Libcloud 嵌入召回数据](../results/libcloud_loadbalancer_real/embedding_recall_summary.json)
- [CodeContests 中文实验结果](../results/codecontest/EXPERIMENT_REPORT.md)
- [CodeContests 结构化结果](../results/codecontest/full_recompute_summary.json)
