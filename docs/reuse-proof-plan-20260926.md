# 共性组件复用证明计划(执行文档)

日期:2026-09-27。来源:2026-09-26 postfix 批(10 任务 × signal × 1)的机制分析、两轮实验设计讨论及后续执行复盘。

**目标**:证明两件事——(a) 蒸馏出的共性组件**可以被复用**(收据 = 最终 diff 中的 `import`/继承,AST 可查,重合度不算证据,已证同源假象);(b) 复用带来**更高的通过率**(新增需求)。

**执行记录(2026-09-27)**:

- 阶段 0 已完成;Planet 任务按用户决定排除。
- 阶段 1 已完成 10 任务 × 3 条件 = 30 次调用,结果在 `demo/reports/reuse-proof-stage1-20260926.json`。复盘确认该 runner 是单轮提示→JSON 调用,生成时没有 C0 源码/工具探索;因此本轮 `no_go` 只描述这种受限提示方式,不能作为组件淘汰结论。阶段 1 已降级为提示材料烟雾检查,不再作为组件 go/no-go 门。
- 阶段 2 第 1 项已完成 AbuseIPDB 逐断言诊断;用户决定保留该任务。随后完成了 Signal 瓶颈离线复盘,尚未修改 agent prompt / pipeline,也未发起新校准。

**已确立的事实**(全部有数据支撑,详见对话记录与本批 run):

- 组件被读(SKILL.md / sub_*_common 引用 7–48 次/任务)但**零复用**:signal 臂 patch 与 pack 的行重合,和从未见过 pack 的 direct 臂完全一样(pints 24%/24%、pymdown 13%/14%、tox 8%/8%、plopp sub_2 28%/28%)。
- token 节省(8–9/10 任务)来自**迭代压缩**(轮数与测试-调试循环更少,5–6/8 任务低于 direct 最小值),读量并未减少——pack 的作用是"预制方案稳定第一稿",不是文件索引。
- 任务判定(固定分母,goal = signal 通过率更高):

| 任务 | direct / signal | 判定 |
|---|---|---|
| intezer | 69.6%(n=3, 56–82)/ 74.5–76.5% | **保留**(骨架分散 + 占难度大头) |
| chordparser | 65.6 / 65.6 | 边界展示(21 条失分断言知识在家族外,平局命) |
| pints | 57 / 56 | 边界展示(NUTS 细节在差集) |
| plopp | 85.4 / 85.7 | 边界展示(imshow 行为在差集,近天花板) |
| abuseipdb | 29.2 / 29.2(原 postfix 批) | pack 为 7/24; direct 有效历史校准 4×7/24、1×14/24;任务规格/测试中立性待审 |
| pymdown | 18.1 / 16 | 淘汰(沉底) |
| planet / tox / xsdata / feature_engine | 0 / 0 | 淘汰(双 0%,零区分度) |

---

## 资产与代码位置(执行前必读)

| 资产 | 位置 |
|---|---|
| 共享 pack(10 个,plopp 已于 postfix 批落地) | `.cache/downstream-signal-context-shared/<task>/`,组件在 `patterns/sub_*_common.py`,指导在 `patterns/sub_*.md`,成员源码摘录在 `snippets/`,清单 `manifest.json`(patterns[].member_paths / common_path) |
| 任务定义 | `demo/downstream/tasks.json`(task_id、repository.url/slug、history.c0、signal_history_paths、agent_task、install_command、test_env) |
| 仓库缓存(C0 快照) | `.cache/history/<slug>` |
| 现成 C0 workspace + 任务 venv | `.cache/downstream-workspaces/<task>/signal/<run-id>/`(postfix 批每任务一个,source + .venv) |
| future tests 重导出 / 注入 / 评分 | `demo/downstream.core`:`materialize(task_id, repo_cache, artifact_root, manifest)`、`inject_tests(artifact_root, workspace)`、`junit_keys(path, repo_prefix)`、`load_reference(task_id, references_root, repo_prefix)` |
| 固定分母 reference | `demo/datasets/swe_rebench_screen/references/<task>.xml` |
| 历史 / postfix 结果 | `.cache/downstream-runs/`、`.cache/downstream-runs-postfix/`;重打分:`python3 -m demo.downstream.rescore_runs [--runs-root ...]` |
| 批处理 | `docs/run_all_once.sh`(VARIANT / REPS / BATCH / TASKS 环境变量,结果隔离根 `.cache/downstream-runs-$BATCH`) |
| 已落地修改(2026-09-26) | `demo/downstream/signal_context.py` grounding 白名单(abc/typing 豁免);`demo/downstream/core.py` 任务 prompt 去掉 "confirm their interfaces" |

LLM 客户端:`demo.baselines.llm_client.DeepSeekClient`(蒸馏管线同款)。

---

## 阶段 0:组件可放置性检查(离线,零 API 成本,先行)

**目的**:证明组件物理上进得了仓库(能 import、不破坏既有测试)。这是每个新 pack 在首次复用交付前的一次性生产端验收,验收结果写入 pack 的 `qualification.json`;相同组件内容和 C0 身份命中该记录时不重复跑。运行期不做任务断言 ↔ 组件覆盖矩阵门禁。

每任务每 pattern 执行。运行 `reuse` 时 runner 先完成 task setup,再用一次性 C0 导出副本验收;pack 中的组件/指导/证据文件与仓库缓存保持只读,仅在 pack 根目录写资格报告:

1. 从 `manifest.json` 取 pattern 的 `member_paths`;取其中一个成员的目录作为放置位置。
2. 从干净 C0 导出临时快照,把 `sub_*_common.py` 放到成员目录。
3. 用已 setup 的 task venv 验证 import 解析(grounding 只保证名字存在于成员源码,不保证路径可解析)。
4. 跑可发现的成员既有测试;没有匹配文件时记录 `not_found`,不宣称语义正确。
5. 验收后临时快照自动删除;通过的模块才会复制到 agent workspace 的 C0 source。

**产出**:每 pattern 的 `placeable: yes/no` + 失败原因分类(相对 import 层级不匹配 / 绝对 import 模块不可解析 / 既有测试挂)。新复用通道将验收结果保存在 pack 根目录的 `qualification.json`;若没有可发现的成员测试,明确记为 `import_only_no_matching_tests`,不宣称语义正确。独立离线盘点仍可写入 `demo/reports/`。

**Go/no-go**:可放置的 pattern 才进阶段 1;若大面积不可放置,先修蒸馏(输出时按成员目录对齐 import),别往下走。

## 阶段 1:one-shot 提示材料烟雾检查(已完成,无 agent 循环)

**定位**:只检查在单轮、无源码探索的提示里,不同附加材料是否能改变初稿。它不测量 agent 在 C0 仓库中自主探索后能否复用组件,也不判断任务是否适配正式 A/B。

**已执行流程**(三条件只差"附加材料"):

1. Prompt = 任务的 `agent_task` 文本 + 附加材料 + 输出格式要求(JSON:`{相对路径: 文件内容}` 映射,允许输出多个文件)。
   - `run_reuse_stage1.py` 直接调用 DeepSeek 生成 JSON;生成结束后才准备/评分 C0 workspace。生成阶段没有 C0 源码,也没有 `run_once` 的 agent/tool 循环。
2. 三条件:
   - ① **baseline**:无附加材料;
   - ② **sibling**:最佳单个兄弟源码全文(取最大 pattern 成员中行数居中者,或人工指定,记录选择);
   - ③ **component**:`sub_*_common.py` + 对应 `sub_*.md` 指导(**不给 snippets**,避免混入成员源码);
   - (可选 ④ snippets-only,拆 guidance 与源码摘录的贡献)。
3. 三条件同模型、同 prompt 模板,每条件可重复 2–3 次取均值(生成有随机性)。
4. 评分:复用 core 的机制——`prepare` 建 C0 workspace(或复用现成的)→ 按 JSON 映射写入生成文件 → `materialize` + `inject_tests` 注入 future tests → 跑 pytest 出 junit → `junit_keys` + `load_reference` 固定分母计分。测试命令可参照 `run_agent_command` 内部构造(`_format_test_command`)。

**本阶段结果的判读边界**:

- 得分只反映该单轮 JSON 生成条件下的补全表现;不能解释成真实 agent 的组件复用率或仓库探索能力。
- 10 任务本轮 `no_go` 结果不据此回炉组件或淘汰任务;低分任务存在测试/任务适配与地板效应,分数相同也不说明三种材料等价。
- 若需筛正式 A/B 候选,以阶段 2 中 C0 可见、可自主探索的 direct agent 校准为准;组件增量留到阶段 3 的正面 A/B 中测。

**Go/no-go**:本阶段不设组件 go/no-go。历史 `no_go` 仅存档,后续不触发组件回炉。

## 方法测试问题总结与候选任务适配条件(供后续筛选)

### 现有方法测试暴露的问题

1. **测到的处理与“组件复用”主张不一致**:当前把 common.py 当只读参考材料,由 agent 自行决定是否读取、如何改写;实验没有要求或稳定观察到组件进入最终实现。因此现有结果测的是参考材料的帮助,不能直接证明发生复用,也不能把分数变化归因于复用。
2. **Signal 的信息增量有限且有条件**:direct 与 Signal 都能探索同一份 C0 仓库,强 agent 可能自行找到并组合相同模式。Signal 主要重排、压缩已有知识;若搜索和综合本来不是 direct 的瓶颈,准确率提升空间就小,当前较明确的收益更偏向减少 token/轮次。
3. **组件不覆盖任务新增的全部知识**:共性组件来自历史代码,适合表达稳定结构和仓库约定,不能补出仓库中没有、任务契约也未说明的领域语义。若关键 future 行为不在组件或指导覆盖范围内,Signal 对这些断言没有直接增益。
4. **部分任务和测试混淆了方法效果**:地板/天花板任务难以显示准确率差异;规格缺失、私有实现细节断言或接口契约不一致,也会造成与共性抽取无关的失败。此类问题应先从候选任务中筛除或修正。
5. **现有结果不足以支持普遍有效或普遍无效的结论**:任务间效果不一致且重复数有限。应把“准确率提升”和“保持准确率但降低成本”分开作为结果,并用适配的任务与配对重复来验证。

**方法假设**:Signal 从仓库已有实现中提炼稳定共性,降低 agent 查找、比较和组合多个实现的成本。它可以提高效率;只有当任务行为与提炼出的模式高度重合,且 direct 的主要失误来自探索/综合这些模式时,才有理由预期提高准确率。Signal 不创造仓库、任务契约或环境中都不存在的领域知识。

筛任务时先判断以下条件,不要只按仓库大小或任务标签(如“新增 backend”)入选:

1. **模式存在且确实分散**:仓库内有多份同类实现,关键约定需要从多处综合得出;没有一个显而易见的单一范本能覆盖大部分任务。仓库大只是潜在探索成本,不是 Signal 有效的充分条件。
2. **新旧行为高度重合**:future 断言中的重要行为能映射到共性组件骨架或具体指导,任务特有变化点数量有限且可明确描述。未被仓库模式覆盖的领域语义必须由任务契约/环境资料完整提供;不能期待共性组件补出这些知识。沿用本阶段覆盖矩阵 ≥70% 的准入线。覆盖矩阵仅作离线候选任务筛选记录,不接入 `run_once` 或正式运行门禁。
3. **Direct 的瓶颈匹配方法机制**:先在 C0 可探索、固定预算下校准 direct。若失败主要因为找不到分散约定、遗漏重复结构或组合不一致,Signal 有机会帮助;若失败源于缺规格、缺外部知识、环境故障或测试隐藏私有接口,应先修任务/测试。Direct 接近 0% 是地板,接近满分是天花板;当前沿用 50–85% 校准带。
4. **任务和测试中立、可判读**:给 agent 的契约足以实现被测行为;future tests 主要检查公开行为,不强迫特定私有 helper/属性名。断言应足够独立,并在正式批次做同任务配对重复。
5. **实验处理与要验证的主张一致**:当前 Signal 将组件作为只读参考交给 agent 自行判断和改写,测到的是“参考材料是否有帮助”,不能据此证明发生了组件复用。若要验证“复用组件提高准确率”,需要给出可实际采用的复用路径,并在最终改动中检查 `import` / 继承 / 调用等复用收据;若组件未被采用,只能归因于指导或探索效率,不能归因于代码复用。

强 agent 的搜索能力会降低 Signal 在“找到代码”上的边际收益,但不代表方法必然无效:在模式分散、综合负担高且预算受限时,预先归纳仍可能减少 token、轮次和实现遗漏。若 direct 已能在相同预算下稳定找到并正确组合这些模式,更合理的成功标准可能是保持准确率并降低成本,而非要求通过率继续上升。大仓库本身不保证收益;决定性条件是**direct 的真实瓶颈、模式覆盖度和复用可行性三者重合**。

## 阶段 2:任务集换血 + 校准硬门禁

1. **AbuseIPDB 保留与再校准准备**(用户已决定保留):结合下述判据①知识完备性与④测试中立性发现,先审查任务契约和测试边界,再安排重新校准;不以旧的单次得分直接淘汰。

   **AbuseIPDB 诊断记录(2026-09-27)**:

   - 冻结 reference 分母为 24。五次有效运行(`20260925-235003-710278`,`20260926-081136-046657`,`20260926-081944-147567`,`20260925-235900-613188`,`20260925-234223-804842`)的固定分数为 `7/24` 四次、`14/24` 一次;`20260926-083531-547830` 仅收集 1 个测试,记为无效校准结果,不并入分数。五次有效运行的 regression 均为 `22/22`。
   - `7/24` 的 17 个未通过用例分为:结果→STIX 3 个(没有生成测试要找的 Indicator / Extension-Definition);STIX→query 8 个(含 `IN` 未支持,以及 IPv4/IPv6、组合、NOT、LIKE、MATCHES 的输出与 AbuseIPDB query schema 不符);transmission 6 个(2 个 ping、4 个 query/results/status/delete 场景)。`14/24` 的 10 个未通过用例仍是 3 个结果映射、1 个多表达式 query、6 个 transmission 用例。由此可见,低分不是单一导入/安装故障,而是多个功能面未完成。
   - **判据①信号**:当前 `agent_task` 只概括要求结果翻译和 query translator,没有给出此 connector 的目标 STIX 对象/属性、query payload 形状和操作语义。任务目录有更具体的 `interface.txt`,但它未进入 `run_once` 提示;因此目前不能把这 11 个映射/query 失分直接解释为任务本身不适配,也可能是交付给 agent 的规格不完整。
   - **判据④信号**:两个 ping 测试直接 patch 内部符号 `APIClient.ping_abuseipdb`;低分 run 实现的是 `ping_data_source`,测试在行为断言前因符号不存在而失败。其余 transmission 失败中,4 个因生成的配置 schema 要求 `connection.host` / `configuration.auth.api_key`,而测试 fixture 使用 namespace 和 `configuration.auth.key`。目标接口说明/官方配置与这些约定有关,但 agent prompt 没给出这些约定。该部分提示存在隐藏实现接口或未交付接口契约的问题,需在任务保留前做 test-neutrality 审核。
   - **处理决定**:用户决定保留 AbuseIPDB。旧低分不用于淘汰;是否补齐 prompt 契约、如何处理两个 ping 私有方法断言,待后续实现前审查。之后应使用 C0 可探索的 direct agent 做 2–3 次校准。
2. **新任务接入**沿用 SWE-rebench 既有流程与坑(`docs/swe-rebench-screen-20260924.md` §5:F2P 要 gate A 重导出、PRs 问题陈述防泄漏、patch 剥家务)。候选池:`demo/datasets/swe_rebench_screen/*.parquet`;家族优先 streamlink(池内 52)、litellm(26)、stix-shifter 其余 connector。**家族形态好 ≠ 任务合适(xsdata 教训),逐任务校准不可省。**


   **候选补丁级静态门审(2026-09-27;用户选择 LiteLLM 12832 + FastMCP 99;尚未接入 tasks.json):**

   - `berriai__litellm-12832`：候选摘要为 medium、12 个新增测试函数、3 个新增 Python 文件;C0 为 `6a1b2323`。C0 有 OpenAI / Azure / Xinference 等图像生成实现与 `BaseImageGenerationConfig`,另有图像生成/编辑相关代码与测试路径;Signal 可用范例较充足。问题是新增 `TestRecraftImageGeneration` 继承 `BaseImageGenTest.test_basic_image_generation`,会调用真实 `litellm.aimage_generation` 并要求 Recraft 凭证和外网;按当前离线 Gate A 不能直接通过。另有 12 个转换器单元测试直接导入 `RecraftImageGenerationConfig` 并调用内部方法,任务 prompt 需双臂明确传递接口契约。可保留为条件候选:先用本地 mock 替换在线集成断言,并保留 API 路由/响应的离线验证后再重导出 Gate A。
   - `jlowin__fastmcp-99`：候选摘要为 medium、20 个新增测试函数、6 个新增 Python 文件;C0 为 `4f6aefed`。C0 总共 41 个跟踪文件,测试树只有 `tests/__init__.py`;`src/fastmcp/tools/tool_manager.py` 为空,也没有本地 Prompt/Resource manager 范例。因此 C0 内可供 Signal 提取的同族实现很弱,且没有既有回归测试。PR 的新增测试检查 `_tool_manager._tools`、`_resource_manager._resources`、`_prompt_manager._prompts` 等私有存储;另有 3 个测试函数放在 `tests/tools/tool_manager.py`,文件名不符合 pytest 默认发现模式。公开 PR 说明只有 `main_app.mount("sub", sub_app)` 工具示例,资源/模板/prompt 的前缀语义仍须在任务规格里明确。整个 PR 还混有 AI labeler、README、Windows CI 和 readline 依赖调整,构造 future patch 时须剥离。当前不推荐作为 Signal 复用候选。
   - 门审依据为两份公开 PR diff 与各自精确 C0 Git tree;没有执行测试、调用模型或做校准。候选 Parquet 只有摘要;尝试读取 V2-PRs 的完整 `problem_statement` / `interface` / `test_patch` 记录时,HF viewer 返回“dataset index is loading”,故两条任务的最终测试中立性判定仍未完成。当前结论:LiteLLM 为条件候选,FastMCP 暂缓;二者都尚未入选。
3. **N≥2 任务**:moto-8848(一 PR 三服务)不拆,合并为单任务——复用价值随 N 增长,N=1 且有兄弟时抄改永远占优;固定预算下 direct 三份实现被摊薄,这是通过率差距被拉开的机制。
4. **校准硬门禁**:每个新任务用 `run_once` 的 agent loop 在 C0 仓库可见条件下 direct 盲跑 2–3 次(未来测试不提供给 agent;命令:`TASKS=... VARIANT=direct REPS=2 BATCH=calib sbatch docs/run_all_once.sh`),rescore 后不落 50–85% 带的淘汰。
5. **覆盖矩阵**(判据③):入选任务人工构建"断言 ↔ 组件 API / 指导条目"映射,覆盖率 ≥70% 进正式批,记录实际值。

## Signal 当前不增分的瓶颈(离线复盘,2026-09-27)

**结论范围**:现有结果说明 Signal 尚未稳定提高通过率,不证明 Signal 方法原则上无效。当前主要问题是上下文压缩没有转成测试相关的信息覆盖或可复用代码;任务分数也常在地板/天花板区间。

1. **组件覆盖不到主要新行为**:AbuseIPDB 的 Signal 输入含 12 个 C0 文件。发现阶段把 5 个 `APIClient` 聚成相关 pattern,但旧 pack 因 `typing` / `Any` 等标准库名字被误判为 ungrounded 而丢弃;最终 `SKILL.md` 只列两个 `EntryPoint` 模板。该任务的 future suite 测结果映射、STIX query 与 transmission,大部分关键行为不在送达 pack 中。当前 `_validate_common_reference` 已允许标准库,但默认 `run_once` 只要发现 shared context 有 `SKILL.md` 就直接复用;不会因 validator 逻辑变化重新验证/生成,所以旧 pack 仍然生效。
2. **读到组件不等于在实现中复用**:组件和 snippets 被复制到 C0 仓库的 `.downstream/signal-context/`,没有部署到目标 package 的 import 路径。Signal prompt 让 agent 自行判断组件是否适用;抽查 AbuseIPDB、Intezer、Plopp 轨迹时,agent 都读过组件文件,但最终改动没有显式 import / 继承 / 调用 common 模块。以本计划的收据定义,当前没有结构化复用收据。Signal 可以提供参考,但落到新代码仍需 agent 手工搬运/改造。
3. **提示混淆了验收责任与任务特化责任**:Signal 附加文案说接口不匹配可由测试发现、“不需要自行核对组件与仓库”。common.py 的抽取正确性、grounding、导入与放置性应由 Signal 生产/发布流程负责验收,不应要求 agent 重新验收组件本身;agent 的责任是依据已说明的变化点将已验收组件特化到新任务,并完成目标功能所需的仓库集成。当前只读参考交付没有清楚说明这条边界。
4. **C0 已让 direct 拥有同一发现渠道,部分 signal 信息是冗余的**:两臂都在同一个完整 C0 仓库工作。AbuseIPDB 代表 run 中 direct 做了 132 次工具调用,signal 做了 115 次;signal 读了索引、一个 common 和一份 snippet,但固定分数仍都是 `7/24`。这说明 signal 在压缩探索/降低 token 上可能有帮助,却没有给该 run 增加能覆盖失分断言的新知识。相邻 connector 源码仍可由 direct 搜索到。
5. **任务集区分力不足**:现有比较中 Intezer 是主要正向差异(约 +5–7pp),Plopp 约 +0.3pp;Chordparser 与 AbuseIPDB 持平,Pints 与 Pymdown 小幅下降,另外 4 个任务两臂都为 0%。低分地板和 Plopp 高分天花板都压缩了可见差异;每任务重复数也有限,目前只能定位瓶颈,不能作“必然无效”的因果结论。

**建议的下一步顺序**:按计划统一重建 pack 后,先做“测试断言 ↔ pack pattern”覆盖矩阵。每个新 pack 在进入实验前由生产/发布流程执行一次阶段 0 验收(grounding、导入和放置性);未通过的组件不作为可复用代码部署。prompt 应说明组件已通过相应验收,agent 只需处理明确的变化点和目标任务集成,无需重复验收组件本身。若要验证代码复用对通过率的影响,再按阶段 3 将已验收组件部署到可导入位置并记录 AST 复用收据。只有覆盖矩阵合格的任务才进入正式 A/B。

## 本轮代码改造边界

1. **组件合格检验**:沿用生成期的 compile、grounding 校验;首次进入复用交付前,生产端把 common module 放进一次性 C0 副本,执行 import 和可发现的成员既有测试,记录 qualification。仅通过的 pattern 才部署;没有匹配测试的 pattern 明确标注为 import-only。
2. **实际复用通道**:`SIGNAL_DELIVERY=reuse` 时将通过验收的模块复制到 manifest 指定成员目录,并向 agent 提供模块路径、API、指导和源码证据;默认 `reference` 行为保留。
3. **自动复用收据**:agent 完成后扫描其新增/修改 Python 文件,为每个模块记录导入、继承、调用/符号使用、是否只导入、模块是否被改写,并保存 JSON artifact。

## 阶段 3:通道改造 + 正面 A/B

1. **通道**:`run_once` 增加 `SIGNAL_DELIVERY=reuse` 选项——首次交付前完成阶段 0 生产端验收,再把通过的 `patterns/*_common.py` 复制进 workspace source 的成员目录;任务 prompt 说明它是可继承/扩展的基础模块。
2. **任务集**:阶段 2 校准通过的 3–5 个任务 + N≥2 任务。
3. **批**:当前既有任务先用 `bash docs/run_existing_signal_reuse_once.sh` 做每任务一次的 reuse 通道检查(默认排除 Planet);后续正式 A/B 再用 `SIGNAL_DELIVERY=reuse BATCH=reuse REPS=3 VARIANT="direct signal" sbatch docs/run_all_once.sh`。通用脚本默认 `reference`,保持现有对照不变;`reuse` 只对 signal 臂传入。
4. **收据分析**:runner 自动对每 run 的最终改动(git diff + 新增 untracked `.py`)做 AST 扫描,查组件模块的 `import` / 继承 / 调用 / 符号引用;写入 `reuse_receipts.json` 并在 `run.json` 登记。组件文件若被 agent 改写也会记录 hash 变化。
5. **判读规则**(预注册式):
   - 复用发生 = 收据存在;
   - 复用划算 = 有收据的 run 在通过率 / LOC / 迭代次数上优于 direct 对照(特化者应写更少代码);
   - 无收据但赢 → 记 guidance 的功,不算组件复用;
   - 有收据但输 → 结合资格报告、任务变化点与仓库集成诊断;收据本身不等于组件导致失败。

## 各阶段 go/no-go 汇总

| 阶段 | 放行条件 | 打回条件 |
|---|---|---|
| 0 | ≥1 个 pattern 可放置/任务 | 大面积不可放置 → 修蒸馏 |
| 1 | 仅作单轮提示材料烟雾检查,不放行/否决组件 | 无 |
| 2 | 任务规格与测试中立性审查通过;真实 C0 agent direct 落 50–85% 带;矩阵 ≥70% | 规格/测试契约不清先修任务构造;校准带外或覆盖不足再淘汰该任务 |
| 3 | 有收据且优于对照 | 无收据 → 通道仍未通;有收据但输 → 查组件或指导 |

## 不做清单

- 双 0% 任务不再进 A/B;新目标下不给单范本任务(chordparser/pints/plopp 形态)砸预算;
- 重合度不再当复用证据;
- 已落地的 10 个 pack 不重新蒸馏(保持与历史可比);
- chordparser / pints / plopp 保留为 demo 边界展示("不伤通过率 + 省 token"),诚实报边界。

## 已知坑(操作层)

- GPU 批一律 sbatch 提交(docs/run_all_once.sh 已内置:unset 代理、DEMO_EMBED_PYTHON、RUNNER_PY=memorybench、PATH 加 qwen-gguf 的 mini);
- runner 拒绝覆盖已存在路径(run-id 时间戳防撞,勿手工复用目录);
- 评分 junit 的 classname 需剥 `.source.` 前缀(`junit_keys` 的 repo_prefix 参数);
- 改 pack prompt 文件会触发蒸馏缓存清空重生成(prompt hash 失效机制),阶段 1 不要动 prompts/;
- 本地跑 LLM 相关命令注意 no_proxy(14514 代理只在本机,节点上要 unset)。

## 可选并行

round2 效率批:`BATCH=round2 REPS=3 VARIANT="direct signal" sbatch docs/run_all_once.sh`,把"通过率不降 + 迭代/ token 压缩"的底线主张钉死(~5h,一个 GPU 批)。
