# 代码共性组件抽取/重构 — Demo 技术方案

**版本**：v1.2 ｜ **日期**：2026-08-06 ｜ **适用范围**：单一演示项目，通用代码

---

## 1. 目标

在单一演示项目上跑通"代码共性组件抽取"完整主线：识别实现各异的共性单元 → 归纳公共单元 → 改写复用点 → 封装为 Agent 可用 Skill。

## 2. 流程

```
预处理 → 共性发现 → 抽象设计+抽取实施 → 验证 → 组件 Skill 封装
```

### ① 预处理：文件级单元清单（功能（类）级别）

扫描演示项目源码（排除依赖/生成代码），以文件为单元输出 `units.json`（文件路径、语言、文件名、源码）。语法解析使用 **tree-sitter** 增量解析器 [1]；单元级切分在演示项目规模下非必需。

### ② 共性发现：3 路信号

| 信号 | 技术 | 捕获的共性 |
|---|---|---|
| 语义相似 | 代码 Embedding（按文件）+ HDBSCAN 聚类 | 职责相同但实现不同的文件 |
| AST 结构 | tree-sitter AST + 结构指纹 / 频繁子树挖掘 | 结构骨架相同但命名不同的文件 |
| 逐字共性 | jscpd | 明确复制的部分 |

- 语义相似使用代码表示学习模型（如 CodeBERT [2]）对文件源码生成向量，再以 HDBSCAN [3] 聚类。
- AST 结构信号对语法树提取结构指纹并挖掘频繁子树，捕获"骨架相同、写法不同"的共性 [4]。
- 逐字共性基于 token 级克隆检测 [5]（jscpd）。

三路信号合成候选簇，附证据（相似度、结构重合度、克隆行数）+ 收益排序（复用次数 × 行数）。

### ③ 抽象设计 + 抽取实施（融合）

**抽象设计（接口契约 + 成员映射表）**：

- 对每个候选簇，LLM 归纳公共单元设计，方法参考 LIBRARIAN [6]（LLM 采样候选抽象 + 择优，demo 简化为直接生成契约）。
- **接口契约**：统一输入（参数归一化）、输出/事件、类型、可变点。
- **成员映射表**：每个成员 → 公共单元的转换规则。
- 覆盖性检查（确定性）：映射表须覆盖簇内所有成员的全部对外用法，遗漏打回。

**抽取实施**：

- LLM 按契约生成公共单元源码，立即过语法/类型/编译校验，不通过回炉一次。
- 调用点改写：ast-grep 规则按映射表确定性执行（引用替换、参数映射、import 增删）[7]，无法规则化的进冲突清单交 LLM/人工。

### ④ 验证（功能一致性）

1. 全量构建通过。
2. 已有测试全量通过（无测试则跳过）。
3. 截图对比：抽取前后页面/输出截图，以 pixelmatch [8] 做像素级对比，差异率超阈值则人工确认。截图采集使用 Playwright [9]。

### ⑤ 组件 Skill 封装

每个收敛的公共单元封装为独立 Skill 包：

```
skills/<公共单元名>/
├── SKILL.md          # 职责、触发条件、接口 API、用法示例、边界限制
├── src/              # 公共单元源码
├── tests/            # 行为测试
├── contract.json     # 接口契约
└── metadata.yaml     # 语言、依赖、来源
```

**演示闭环**：让 Agent 基于该 Skill 实现一个新功能，证明"抽取一次、持续复用"。

---

## 3. 技术选型

| 环节 | 选型 | 参考 |
|---|---|---|
| 语法解析 | tree-sitter | [1] |
| 语义相似 | 代码 Embedding（CodeBERT）+ HDBSCAN | [2][3] |
| AST 结构 | tree-sitter AST + 结构指纹/频繁子树 | [4] |
| 逐字共性 | jscpd（token 级克隆检测） | [5] |
| 抽象设计 | LLM（LIBRARIAN 候选抽象+择优方法） | [6] |
| 公共单元生成 | LLM + 编译校验护栏 | — |
| 调用点改写 | ast-grep 规则化执行 | [7] |
| 截图对比 | pixelmatch + Playwright | [8][9] |
| Skill 交付 | SKILL.md + contract.json + 源码 + 测试 | — |

---

## 参考文献

- [1] M. Brunsfeld. *Tree-sitter — a new parsing system for programming tools*. FOSDEM 2018. https://archive.fosdem.org/2018/schedule/event/code_tree_sitter/
- [2] Z. Feng et al. *CodeBERT: A Pre-Trained Model for Programming and Natural Languages*. Findings of EMNLP 2020. arXiv:2002.08155
- [3] R. J. G. B. Campello, D. Moulavi, A. Zimek, J. Sander. *Hierarchical Density Estimates for Data Clustering, Visualization, and Outlier Detection*. ACM TKDD, 10(1), 2015. DOI: 10.1145/2733381
- [4] X. Yan, J. Han. *gSpan: Graph-Based Substructure Pattern Mining*. IEEE ICDM 2002. DOI: 10.1109/ICDM.2002.1184038
- [5] T. Kamiya, S. Kusumoto, K. Inoue. *CCFinder: A Multi-Linguistic Token-Based Code Clone Detection System for Large Scale Source Code*. IEEE TSE, 28(7), 2002. DOI: 10.1109/TSE.2002.1019480
- [6] Z. Kovacic, C. Lee, J. Chiu, W. Zhao, K. Ellis. *Refactoring Codebases through Library Design*（LIBRARIAN）. NeurIPS 2025. arXiv:2506.11058
- [7] ast-grep 项目文档（结构化代码搜索/改写）. https://ast-grep.github.io/
- [8] pixelmatch 项目文档（像素级图像对比）. https://github.com/mapbox/pixelmatch
- [9] Playwright 官方文档（浏览器自动化与截图）. https://playwright.dev/
