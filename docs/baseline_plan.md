# Baseline 阶段执行计划

> **目标**：直接复用作者仓库中已经准备好的 CodeContests 聚类数据，运行两个 LLM baseline，并输出五项指标报告。
>
> **数据**：`Librarian/data/LLM_description_clusters/new/*.jsonl`
>
> **模型**：DeepSeek V4 Flash API
>
> **核心约束**：输入给 LLM 的源码只来自每条 JSONL 记录的 `solution` 字段。`description`、`short_description`、`name`、`difficulty`、tests 不进入 baseline prompt；tests 只用于外部验证。

---

## 0. 当前决策

本阶段不重新构建数据集，不额外选 HF Transformers / Diffusers，也不复现 LIBRARIAN 的 sample-and-rerank。只做两个最小对照：

1. **Baseline-a：端到端一次性抽取**
   - 对每个作者给定 cluster，单次把该 cluster 的所有 `solution` 源码喂给 LLM。
   - 让 LLM 一次性完成“发现共性、生成 `common.py`、改写成员文件”。

2. **Baseline-b：先发现，再生成公共库，最后应用成员改写**
   - Step 1：只看同一个 cluster 内所有 `solution`，让 LLM 自己发现哪些文件共享组件。
   - Step 2：对 Step 1 发现出的每个有效子簇，让 LLM 只生成 `common.py`。
   - Step 3：将 `common.py` 和成员源码交给独立的改写步骤，只生成成员文件的严格 diff；宿主程序应用 diff 后写出最终文件。

这里的“抽取”在语义上包含两件事：判断哪些逻辑值得共享，以及把这部分逻辑实现为 `common.py` 的 helper。为了让每次 LLM 调用只承担一个明确任务，Baseline-b 在工程实现上把“生成公共库”和“修改成员源码”分开。成员源码的具体删除位置、调用位置和 hunk 对应关系由 Step 3 输出并由宿主程序校验。

两者都使用同一个 DeepSeek V4 Flash API 配置，温度固定为 0，不做多样本采样，不做 rerank。

---

## 1. 数据口径

### 1.1 数据来源

使用当前仓库已有数据：

```text
Librarian/data/LLM_description_clusters/new/
  0.jsonl
  1.jsonl
  ...
  9.jsonl
```

每个 `.jsonl` 文件视为一个作者提供的算法主题 cluster。每行是一道题的完整记录，包含：

- `name`
- `description`
- `solution`
- `difficulty`
- `public_tests`
- `private_tests`
- `generated_tests`
- `short_description`

### 1.2 Baseline 输入

LLM prompt 中只暴露：

```json
{
  "file_id": "file_000.py",
  "source_code": "<solution 字段内容>"
}
```

不暴露：

- `name`：题名可能泄露题型，例如 Tree / Graph / XOR。
- `description`：题面会直接泄露算法语义。
- `short_description`：这是 LLM 算法摘要，等于给出共性组件提示。
- `difficulty`：非必要元信息。
- `public_tests/private_tests/generated_tests`：测试只用于 prompt 外部验证，不能给 LLM 看。

### 1.3 文件命名

每个 cluster 解包为中性文件名：

```text
demo/datasets/codecontest/clusters/<cluster_id>/original/file_000.py
demo/datasets/codecontest/clusters/<cluster_id>/original/file_001.py
...
```

同时保存一个 prompt 外部 manifest：

```json
{
  "cluster_id": "0",
  "source_jsonl": "Librarian/data/LLM_description_clusters/new/0.jsonl",
  "files": [
    {
      "file_id": "file_000.py",
      "row_index": 0,
      "name": "963_B. Destruction of a Tree",
      "tests": {
        "public": "...",
        "private": "...",
        "generated": "..."
      }
    }
  ]
}
```

这个 manifest 不进入 LLM prompt，只用于评估、溯源和报告。

---

## 2. 输出目录

建议统一写到：

```text
demo/
  dataset/
    DATA_INVENTORY.md
    cluster_manifest.json
    clusters/
      0/original/*.py
      ...
  baselines/
    prompts/
      baseline_a_system.txt
      baseline_a_user_template.txt
      baseline_b_discover_system.txt
      baseline_b_discover_user_template.txt
      baseline_b_extract_system.txt
      baseline_b_extract_user_template.txt
    llm_client.py
    run_baseline_a.py
    run_baseline_b.py
  eval/
    run_tests.py
    measure_tokens.py
    measure_mdl.py
    measure_api_coverage.py
    measure_conflicts.py
    report.py
  results/
    baseline_a/<cluster_id>/
    baseline_b/<cluster_id>/
  demo/reports/report.md
```

本文件是执行计划，不直接实现上述脚本。

---

## 3. 指标体系

四硬一软，按重要性顺序：

| 编号 | 指标 | 角色 | 计算对象 |
|---|---|---|---|
| 1 | **pass rate** | 功能等价硬约束 | 原 `solution` vs `common.py + refactored/*.py` 在同一测试集上的通过率 |
| 2 | **MDL 压缩率** | 主指标 | 原始源码包 vs 抽库改写后源码包的 NLL |
| 3 | **tokens 压缩率** | 辅指标 | 原始源码包 token 数 vs `common.py + refactored` token 数 |
| 4 | **API coverage** | 结构约束 | `common.py` 暴露 API 被改写文件实际使用的覆盖情况 |
| 5 | **改写冲突率** | 软指标 | LLM 输出的 `call_mapping` 与 AST/diff 实际调用之间的不一致 |

### 3.1 Pass Rate

每个原始 `solution` 先跑一遍 JSONL 自带测试，作为 `before` 基线。测试集合为：

```text
public_tests + private_tests + generated_tests
```

改写后，对每个 `refactored/file_xxx.py` 在同一测试集上执行：

- `common.py` 与改写文件放在同一目录。
- 改写文件使用 `from common import ...` 或 `import common`。
- 每个 test input 单独启动一次进程，避免全局状态污染。

报告两个层级：

```text
test_pass_rate = passed_tests / total_tests
file_pass_rate = all_tests_passed_files / total_files
```

主表使用 `file_pass_rate`，附表保留 `test_pass_rate`。

### 3.2 MDL 压缩率

计算公式：

```text
MDL_before = NLL(concat(original/file_*.py))
MDL_after  = NLL(common.py + concat(refactored/file_*.py))
MDL_compression = 1 - MDL_after / MDL_before
```

口径要求：

- `common.py` 必须计入 `after`，否则会虚高。
- before/after 必须使用同一个 LM 算 NLL。
- 对失败改写不奖励压缩：报告中主表只对 pass 的文件/簇计算压缩，失败项单独列为 extraction failed 或 pass=0。

模型优先级：

1. 如果 DeepSeek V4 Flash API 可返回 token logprobs，则直接用 DeepSeek V4 Flash 计算 MDL。
2. 如果该 API 不提供可用 logprobs，则用开源 coder LM 作为固定参照 LM，只要求 before/after 同模型可比。
3. 如果本阶段暂不跑本地 LM，则 MDL 列标记为 `not_available`，但保留 tokens 压缩率作为可运行指标。

### 3.3 Tokens 压缩率

计算公式：

```text
tokens_before = tokens(concat(original/file_*.py))
tokens_after  = tokens(common.py + concat(refactored/file_*.py))
tokens_compression = 1 - tokens_after / tokens_before
```

优先用 `tiktoken` 的 GPT-4 系列 tokenizer；如果环境不可用，则固定使用同一个本地 tokenizer，并在报告中记录 tokenizer 名称。

### 3.4 API Coverage

从 `common.py` 里提取对外 API：

- 顶层 `def`
- 顶层 `class`

从每个改写文件 AST 中提取实际调用：

- `foo(...)`
- `common.foo(...)`
- 类实例化 `Foo(...)`

报告两个覆盖率：

```text
api_usage_coverage = 被至少一个改写文件调用的 common API 数 / common API 总数
file_api_coverage = 至少调用一个 common API 的改写文件数 / 成功改写文件数
```

主表使用 `file_api_coverage`，因为它更能说明“多少原文件真正被抽库改写”。

### 3.5 改写冲突率

Baseline 输出要求包含 `edit_mapping`（兼容旧结果中的 `call_mapping`）。评估器按“文件—helper”提取声明的 helper，并与改写文件 AST 中实际调用的 `common.py` 顶层 API 比较：

- mapping 声称用了某 helper，但 AST 中没有实际调用。
- AST 中实际调用了某 helper，但 mapping 没记录。
- mapping 指向的 helper 不存在于 `common.py`。
- 严格 diff 是否能应用、删除 hunk 与新增 common 调用是否对应，由抽取阶段的宿主校验器提前检查，不计入这里的冲突率。

计算：

```text
conflict_rate = conflict_items / max(mapping_items, actual_helper_calls, 1)
```

其中 `mapping_items` 和 `actual_helper_calls` 都按每个文件中的唯一 helper 计数，而不是按调用次数计数。这是软指标，不直接决定 pass/fail，只用于解释“LLM 声明的复用关系是否与最终源码一致”。

---

## 4. Baseline-a：端到端一次性抽取

### 4.1 意图

测试最弱但最直接的对照：单 prompt 把整簇源码交给 LLM，让它一口气完成：

1. 发现共享算法组件。
2. 写出 `common.py`。
3. 改写所有成员文件去 import 并调用 `common.py`。

### 4.2 输入

单个作者 cluster 的全部 `solution`，中性文件名包装：

```json
{
  "cluster_id": "0",
  "files": [
    {
      "file_id": "file_000.py",
      "source_code": "..."
    }
  ]
}
```

### 4.3 Prompt 要点

SYSTEM：

- 角色：资深 Python 重构工程师。
- 任务：从多个独立脚本中发现共性 helper，抽成 `common.py`，并改写每个脚本。
- 约束：
  - 不引入第三方依赖。
  - 不改变输入输出行为。
  - 保留每个文件自己的 stdin/stdout 主流程。
  - `common.py` 只放可复用 helper 和必要标准库 imports。
  - 输出必须是可解析 JSON。

USER：

- 列出 `{file_id, source_code}`。
- 要求完整输出 library 和所有成员改写结果。

JSON schema：

```json
{
  "library": {
    "path": "common.py",
    "content": "..."
  },
  "members": {
    "file_000.py": {
      "new_content": "...",
      "call_mapping": [
        {
          "helper": "helper_name",
          "original_role": "what original logic this replaces",
          "call_sites": ["brief location or statement"]
        }
      ]
    }
  },
  "rationale": "..."
}
```

### 4.4 运行逻辑

对每个 `new/<cluster_id>.jsonl`：

1. 构造 prompt，只包含 `solution` 源码。
2. 调 DeepSeek V4 Flash API 一次。
3. 保存原始响应到 `raw_response.txt`。
4. 解析 JSON；解析失败则回炉一次，仍失败则该 cluster 标记为 extraction failed。
5. 写入：

```text
results/baseline_a/<cluster_id>/common.py
results/baseline_a/<cluster_id>/refactored/file_*.py
results/baseline_a/<cluster_id>/raw_output.json
results/baseline_a/<cluster_id>/call_log.json
```

6. 编译检查：`python -m py_compile common.py refactored/*.py`。
7. 编译失败则把错误信息追加给 LLM 回炉一次，要求完整重输出 JSON。
8. 跑五项指标。

---

## 5. Baseline-b：先发现再抽取

### 5.1 意图

把“发现共性”和“抽取改写”拆开，分别观察：

- LLM 能否只从代码发现哪些文件共享算法组件。
- 给定发现结果后，LLM 能否更稳定地抽库和改写。

### 5.2 Step 1：发现

输入仍然是单个作者 cluster 的全部 `solution`。

SYSTEM：

- 角色：代码分析专家。
- 任务：只发现共享组件，不输出改写代码，不写 `common.py`。
- 约束：只能根据源码判断，不要假设题名或题面。

USER：

- 列出 `{file_id, source_code}`。
- 要求输出发现出的子簇、共享概念、候选接口、关键差异。

JSON schema：

```json
{
  "clusters": [
    {
      "cluster_id": "sub_0",
      "members": ["file_000.py", "file_003.py"],
      "shared_concept": "...",
      "shared_interface": [
        {
          "name": "helper_name",
          "signature_guess": "helper_name(...)",
          "role": "..."
        }
      ],
      "key_variations": ["..."]
    }
  ],
  "noise": ["file_007.py"],
  "rationale": "..."
}
```

发现结果规则：

- `members` 少于 2 个文件的子簇不进入 Step 2 和 Step 3。
- `noise` 只记录，不抽取。
- Step 1 输出保存为 `discovery.json`，用于报告分析。

### 5.3 Step 2：生成公共库

对 Step 1 每个有效子簇调用一次 DeepSeek V4 Flash，只负责根据发现结果和成员源码设计并生成 `common.py`，不修改成员文件。

输入：

- 该子簇的 `shared_concept`
- `shared_interface`
- `key_variations`
- 子簇成员的 `solution` 源码

输出：

```json
{
  "library": {
    "path": "common.py",
    "content": "..."
  },
  "rationale": "..."
}
```

### 5.4 Step 3：应用成员改写

将 Step 2 生成的 `common.py`、Step 1 的子簇信息和成员源码交给独立的成员改写调用。该步骤不重新设计公共库，只负责描述每个成员文件如何调用已有 helper。

成员结果只允许包含严格 unified diff 和 `edit_mapping`：

```json
{
  "refactors": [
    {
      "file_id": "file_000.py",
      "diff": "...",
      "edit_mapping": [
        {
          "edit_id": "edit_1",
          "helper": "helper_name",
          "removed_hunks": [1],
          "added_hunks": [1],
          "relationship": "..."
        }
      ]
    }
  ]
}
```

宿主程序将 diff 精确应用到原始成员源码，并检查 helper 存在、删除和新增 hunk 已对应、新增代码确实调用 common helper。检查通过后才写出 `refactored/file_*.py`。

### 5.5 运行逻辑

对每个 `new/<cluster_id>.jsonl`：

1. Step 1 发现，保存：

```text
results/baseline_b/<cluster_id>/discovery.json
results/baseline_b/<cluster_id>/discovery_call_log.json
```

2. 对每个有效子簇执行 Step 2 和 Step 3，保存：

```text
results/baseline_b/<cluster_id>/<sub_cluster_id>/common.py
results/baseline_b/<cluster_id>/<sub_cluster_id>/refactored/file_*.py
results/baseline_b/<cluster_id>/<sub_cluster_id>/raw_output.json
results/baseline_b/<cluster_id>/<sub_cluster_id>/call_log.json
```

3. 编译失败或 JSON 解析失败，最多回炉一次。
4. 跑与 Baseline-a 相同的五项指标。
5. 对没有进入任何有效子簇的文件，记为 `not_refactored`，pass 仍可单独报告，但不计入抽库压缩收益。

---

## 6. DeepSeek V4 Flash API 配置

统一封装一个 LLM client，记录每次调用：

```json
{
  "provider": "deepseek",
  "model": "deepseek-v4-flash",
  "temperature": 0,
  "cluster_id": "0",
  "baseline": "baseline_a",
  "prompt_path": "...",
  "raw_response_path": "...",
  "latency_sec": 0,
  "usage": {}
}
```

环境变量建议：

```text
DEEPSEEK_API_KEY
DEEPSEEK_BASE_URL
DEEPSEEK_MODEL=deepseek-v4-flash
```

如果实际 API model id 不是 `deepseek-v4-flash`，只改配置，不改实验口径。报告中记录真实 model id。

---

## 7. 报告格式

最终报告：

```text
demo/reports/report.md
```

主表：

| baseline | cluster | files | pass file % | pass test % | MDL compression | token compression | file API coverage | API usage coverage | conflict rate | status |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|

Baseline-b 额外附表：

| cluster | discovered subclusters | valid subclusters | noise files | notes |
|---|---:|---:|---:|---|

失败案例附录：

- JSON 解析失败
- 编译失败
- 测试失败
- 抽出空 `common.py`
- `common.py` 有 API 但没有任何 refactored 文件使用
- `call_mapping` 与实际调用冲突

---

## 8. 数字解读规则

| 现象 | 解释 |
|---|---|
| pass rate 高，token/MDL compression 也高 | baseline 能从该类题中抽出真实可复用 helper |
| pass rate 高，但 compression 低或为负 | 抽取行为等价，但库接口冗余、重复包装太多 |
| pass rate 低，compression 高 | 不能算成功；可能只是删坏了代码 |
| Baseline-b pass 高于 Baseline-a | 先发现再抽取能降低端到端混乱 |
| Baseline-b pass 低于 Baseline-a | LLM 自主发现阶段把成员分错或接口猜错 |
| API coverage 低 | 生成了共享库，但多数文件没有真正使用 |
| conflict rate 高 | LLM 的自述 mapping 不可信，后续需要规则化验证 |

主结论必须先看 `pass rate`，再看压缩率。任何功能不等价的压缩都不作为正收益。

---

## 9. 验收标准

阶段完成时应满足：

1. `DATA_INVENTORY.md` 清楚列出 10 个 cluster、每簇 30 条记录、字段结构和测试数量。
2. Baseline-a 在 10 个 cluster 上全部完成调用，失败也有结构化失败记录。
3. Baseline-b 在 10 个 cluster 上全部完成 discovery，并对有效子簇完成抽取。
4. 每个 baseline、每个 cluster 都有：
   - 原始 LLM 响应
   - 解析后的 JSON
   - `common.py`
   - 改写文件
   - 编译结果
   - 测试结果
   - 五项指标
5. `demo/reports/report.md` 给出主表、Baseline-b discovery 附表和失败案例附录。

---

## 10. 实施顺序

### Day 1：数据与评估器

1. 解包 `new/*.jsonl` 的 `solution` 到 `demo/datasets/codecontest/clusters/*/original/`。
2. 生成 `cluster_manifest.json` 和 `DATA_INVENTORY.md`。
3. 写原始 solution runner，确认 before pass rate。
4. 写 tokens、API coverage、conflict 的静态分析脚本。

### Day 2：Baseline-a

1. 写 DeepSeek client 和 Baseline-a prompt。
2. 跑通 1 个 cluster 的端到端流程。
3. 确认 JSON 解析、落盘、编译、测试、指标汇总都能跑。
4. 批量跑 10 个 cluster。

### Day 3：Baseline-b

1. 写 discovery prompt。
2. 写 Step 2 extraction prompt。
3. 跑通 1 个 cluster。
4. 批量跑 10 个 cluster。

### Day 4：报告

1. 汇总两套 baseline 的指标。
2. 检查失败案例。
3. 写 `demo/reports/report.md`。
4. 明确结论：DeepSeek V4 Flash 在“只看 solution 源码”的条件下，端到端抽取和先发现再抽取分别能达到什么程度。

---

## 11. 待确认项

当前没有阻塞性决策。实施时只需要在配置层确认：

1. DeepSeek V4 Flash 的真实 API model id。
2. DeepSeek API 是否返回 token logprobs；如果不返回，MDL 指标按 §3.2 的降级策略处理。
3. 是否先跑全部 10 个 cluster，还是先跑 2 个 cluster 做 smoke test 后再全量跑。




2 任务设定：输入输出形式化（cluster 源码 → common.py + 改写文件）、功能等价约束、LLM 只可见 solution 源码（不泄漏题名/题面/测试）
3 方法：3.1 Baseline-a 单次端到端；3.2 Baseline-b 三步流水线（发现子簇 → 生成公共库 → 应用成员 diff）；3.3 共同设置（温度 0、一次回炉、JSON schema 约束）
4 评估设置：两个数据集（CodeContests 10 簇×30 题 vs Scrapy 24 文件 pytest 套件）、模型（DeepSeek V4 Flash API）、五项指标定义与判定顺序（先 pass 后压缩）
5 实验结果：按数据集×方法的主表 + 聚合统计，不做逐簇罗列
6 分析（逐 RQ）：
RQ1 方法对比：CodeContests 上 a 功能保持更好（+6pp file pass），b 压缩显著更强（MDL 0.89 vs 0.31）→ 明确的 pass/压缩权衡
RQ2 压缩的真实性：b 的高压缩伴随 file API 覆盖 96%（真实复用）但 usage 覆盖仅 71%（抽出的接口未全用）；a 的低压缩是保守策略
RQ3 发现质量：64 子簇全部有效，但个别子簇 pass 崩塌（如 cluster 2/sub_5 为 0%、cluster 9/sub_0 为 18%）→ 发现阶段成员分错/接口猜错的代价
RQ4 数据泛化：真实框架代码上 a 保功能但 MDL 为负（加了 common.py 却没净删码）、b 压缩但破坏功能（63% pass、57% 冲突）→ 框架代码共性更隐晦、改写风险更高
RQ5 可信度：冲突率在 b/complex 上显著升高 → LLM 自述 call_mapping 不可靠，结论需规则化验证支撑
