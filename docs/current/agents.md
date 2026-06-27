# Agent 层（当前实现）

Agent 位于 `backend/app/agents/`，每个 Agent 是一个独立子目录，包含 `service.py`（调用逻辑）和 `prompting.py`（Prompt 加载）。

Agent 与 Service 的区别：Agent 直接调用 LLM 完成一个具体的智能任务，Service 负责业务编排。

## 共享工具：`agents/_common/`

跨 Agent 复用的纯函数工具集合（不强制基类，按需引用）：

- `PromptLoader`：统一封装 prompt 文件的"env 覆盖路径 + lru_cache 加载 + sha256 快照 + 自定义错误类型"。每个 Agent 实例化一份并把 `cached_loader` / `snapshot` 等再导出为既有公开名（如 `load_text_summary_prompt`），对外契约不变。注意：每个实例持有**独立**的 lru_cache，避免不同 Agent 共用缓存导致 prompt 串位；`snapshot` 故意不走缓存，保证运维替换 prompt 文件后能立刻拿到新指纹。
- 后续如新增多采样汇总 / 多次投票 / usage 累加等共享逻辑，应优先沉到此目录，仍以"纯函数 + 不可变 dataclass"为主，避免过早抽象基类。

---

## DocumentStructuringAgent

**目录**：`backend/app/agents/document_structuring/`

**触发时机**：由 `/agents/document-structuring/debug-run` 端点调用，当前为调试接口，尚未集成进文档解析主流程。

**输入**：
- `source_text`：待整理的原始文档文本（最大 200,000 字符）
- `config_id`：可选，指定使用哪个 LlmConfig；为 None 时使用激活配置
- `model`：可选，覆盖 LlmConfig 的默认模型

**输出**：`DocumentStructuringAgentResult`
- `output_markdown`：整理后的 Markdown 文本
- `model`：实际使用的模型标识
- `usage`：token 消耗
- `prompt_path` / `prompt_hash`：使用的 Prompt 文件路径和内容哈希

**Prompt 策略**：
- System Prompt 从文件加载，路径默认为 `backend/app/prompts/document_structuring_agent.txt`
- 可通过环境变量 `DOCUMENT_STRUCTURING_AGENT_PROMPT_PATH` 覆盖路径
- Prompt 文件内容用 `lru_cache` 缓存，进程内只读一次
- User Prompt：固定模板，将 source_text 包裹在 `<<<SOURCE_TEXT>>>` 标记内

**LLM 依赖**：通过 `get_llm_service_client()` 获取，使用全局激活的 LlmConfig 或指定配置。Temperature 默认 0.1。

---

## TextSummaryAgent

**目录**：`backend/app/agents/text_summary/`

**触发时机**：由 `/agents/text-summary/debug-run` 端点调用，当前为调试接口，尚未集成进主流程。

**输入**：
- `source_text`：待摘要的文本（最大 200,000 字符）
- `config_id`：可选
- `model`：可选

**输出**：`TextSummaryAgentResult`
- `title`：文档标题（最大 60 字符）
- `summary`：摘要文本（最大 200 字符）
- `model` / `usage` / `prompt_path` / `prompt_hash`：同 DocumentStructuringAgent

**Prompt 策略**：
- System Prompt 从文件加载，路径默认为 `backend/app/prompts/text_summary_agent.txt`
- 可通过环境变量 `TEXT_SUMMARY_AGENT_PROMPT_PATH` 覆盖
- LLM 返回值预期为 JSON 格式：`{"title": "...", "summary": "..."}`
- 使用 `LlmJsonParser.parse_object()` 解析响应，失败抛 `TextSummaryAgentError`

**LLM 依赖**：同 DocumentStructuringAgent。Temperature 默认 0.1。

---

## Agent 通用约定

1. **错误分类**：每个 Agent 定义自己的 `XxxAgentError`，上层路由按此分类映射 HTTP 状态码
2. **Prompt 不内联**：所有 System Prompt 存放在 `backend/app/prompts/` 目录下的 `.txt` 文件
3. **LLM 审计**：所有 LLM 调用经过 `LlmService`，自动写入 `llm_chat_records`
4. **无副作用持久化**：当前两个 Agent 均不写业务数据库表，只返回结果；业务写入由上层编排
5. **Prompt 哈希追踪**：每次调用记录 `prompt_path` 和 `prompt_hash`，便于排查 Prompt 变更对结果的影响

---

## Pipeline 框架（通用编排层）

**目录**：`backend/app/agents/pipeline/`

通用的、与具体业务无关的 Agent 编排框架。用于把多个 Agent 串成顺序执行的流水线，并通过一个共享的可变 Context 在步骤间传递数据。

**核心组成**：
- `BasePipelineContext`：跨切关注点容器（`run_id` / `request_id` / `step_records` / `total_usage` / `aborted`）。业务 Context 通过组合（`base: BasePipelineContext` 字段）而非继承来复用，保持业务对象扁平、易序列化。
- `StepRecord`：每一步执行的结构化痕迹，含状态、耗时、`model` / `prompt_hash` / `usage` / `metrics` 等字段，JSON 友好。
- `PipelineStep`：基于 `Protocol` 的鸭子类型契约（具备 `name` 属性与 `run(ctx) -> StepRecord` 方法），不强制继承。
- `AgentPipeline.run(ctx)`：顺序执行 step，自动计时、累加 `total_usage`、记录 `StepRecord`、短路 abort。
- `PipelineAbort`：步骤主动中止流水线（不视为失败）。
- `PipelineStepError`：步骤抛出未预期异常时由 runner 包装并向上抛，携带 `step_name`，便于路由层映射 HTTP。

**当前范围（PR1）**：
- 仅提供框架本身，不绑定任何业务 Agent；不支持重试、并行、DAG（YAGNI，等业务驱动后再扩）
- 业务步骤（如 IFPUG 各 step）后续按"读 ctx → 调 `run_xxx_agent` → 写 ctx + 返回 StepRecord"的薄包装方式实现，独立子目录维护

**测试**：`backend/tests/test_agent_pipeline.py` 覆盖顺序执行、usage 累加、`PipelineAbort` 短路、未预期异常包装、Context 校验等场景。

---

## IFPUG 流水线（逻辑文件识别）

**目录**：`backend/app/agents/ifpug/`

封装 IFPUG 功能点分析中"逻辑文件识别"任务的多步流水线，与通用 `pipeline/` 框架配合使用。当前阶段（PR2）仅落地 **子任务 1.1**（候选数据实体识别），后续 PR 将增量补齐 1.2 ~ 1.7。

### 模块划分

- `domain.py`：领域 dataclass —— `DataEntity` / `Attribute` / `SourceRef` / `EntityRelation` / `LogicalFile` / `Exclusion`，以及统一的排除标签常量。
- `context.py`：`IfpugContext`，组合 `BasePipelineContext`；提供 `next_entity_id()` / `next_logical_file_id()` 稳定 id 分配器和 `active_entities()` / `active_logical_files()` 漏斗视图。
- `steps/s1_1_identify_entities.py`：子任务 1.1 的 agent 函数 + Step 包装。
- `steps/s1_2_filter_unmaintained.py`：子任务 1.2，过滤"本应用不维护"的候选实体。
- `steps/s1_3_merge_duplicates.py`：子任务 1.3，合并语义重复的候选实体（代码侧并查集传递闭包）。
- `steps/s1_4_filter_code_data.py`：子任务 1.4，过滤"代码 / 参考数据"（字典、枚举、常量表）。
- `steps/s1_5_filter_not_user_required.py`：子任务 1.5，过滤"非用户业务需求驱动"的实体（系统日志、技术快照等）。
- `steps/s1_6_filter_associative.py`：子任务 1.6，过滤"关联实体"（典型连接表，只承载外键的关系实体）。
- `pipeline.py`：`build_logical_file_pipeline(until=...)` 按已注册步骤顺序组装 `AgentPipeline`，支持按短名截断（调试用）。当前已注册：`s1_1` → `s1_2` → `s1_3` → `s1_4` → `s1_5` → `s1_6`。

### 设计要点

1. **不删除原则**：所有过滤类步骤（1.2 / 1.4 / 1.5 / 1.6）只往 `DataEntity.exclusions` 或 `LogicalFile.exclusions` 追加 `Exclusion(tag, rationale, step)`，原始候选始终保留。"未被任何标签命中"的对象由 `ctx.active_entities()` / `ctx.active_logical_files()` 给出。
2. **稳定 id**：LLM 仅输出实体的语义字段，**id 由代码侧分配**（`E001` / `LF001` 序列）；后续步骤一律通过 id 引用对象，避免 LLM "改名漂移"。
3. **rationale 是一等公民**：所有判定决策都必须写入 `rationale` / `classification_rationale`，下游 UI 才能解释"为什么被排除 / 被分类为 ILF"。
4. **JSON 严格校验**：每个步骤都对 LLM 返回值做逐字段类型与长度校验，任何字段缺失/类型错误都抛出该步骤自身的 `XxxAgentError`，由 runner 包装为 `PipelineStepError(step_name=...)`。

### 子任务 1.1（IdentifyEntities）

**触发时机**：由 `/agents/ifpug/logical-file/debug-run` 端点调用（`until="s1_1"` 仅跑此步）。

**输入**（来自 `IfpugContext`）：
- `source_document`：已结构化文档（建议使用 `DocumentStructuringAgent` 的输出）
- `counting_scope`：用户描述的计数范围
- `user_requirements`：用户需求描述

**输出**（写回 `IfpugContext.candidate_entities`）：
- 每个 `DataEntity` 包含稳定 id、`name` / `description` / `attributes` / `source_refs`

**Prompt 策略**：
- System Prompt：`backend/app/prompts/ifpug_s1_1_identify_entities.txt`，可通过环境变量 `IFPUG_S1_1_IDENTIFY_ENTITIES_PROMPT_PATH` 覆盖；进程内 `lru_cache`。
- User Prompt：用三段 `<<<COUNTING_SCOPE>>>` / `<<<USER_REQUIREMENTS>>>` / `<<<SOURCE_DOCUMENT>>>` 标签包裹三段输入，避免拼接歧义。
- 强制 JSON 输出，仅允许顶层字段 `entities`；要求 LLM **不要输出 id**（id 由代码分配）。

**StepRecord.metrics**：`entities_in` / `entities_out` / `entities_excluded`，便于在 debug 端点画"漏斗"。

### 子任务 1.2（FilterUnmaintained）

**形态**：**分类型 / 标签型** step。一次性把所有活跃候选实体打包送 LLM，让其在全局视角下判定哪些"本应用不维护"。

**输入**（来自 ctx）：当前活跃实体 `ctx.active_entities()`、`counting_scope`、`user_requirements`。

**输出**：给被判定为未维护的实体追加 `Exclusion(EXCLUDED_BY_UNMAINTAINED, rationale, step)`；**不删除元素**。

**Prompt 策略**：
- System Prompt：`backend/app/prompts/ifpug_s1_2_filter_unmaintained.txt`，可通过 `IFPUG_S1_2_FILTER_UNMAINTAINED_PROMPT_PATH` 覆盖；走通用 `PromptLoader`。
- User Prompt：候选实体打成紧凑 JSON 通过 `<<<CANDIDATE_ENTITIES>>>` 标签注入，确保 LLM 引用的是稳定 id。
- LLM 只输出**应被排除**的实体（`{"excluded": [{"id": "E001", "rationale": "..."}]}`），未列出 = 保留。

**容错策略（关键）**：LLM 返回的"未知 id / 同次重复 id / 已被前置步骤排除的 id"都写入 `StepRecord.metrics.warnings`，但**不阻断步骤** —— 分类型决策容错优先，避免单点错误炸掉整条流水线。

**短路**：活跃实体为空时返回 `SKIPPED` 的 StepRecord，不调 LLM。

**StepRecord.metrics**：`entities_in` / `entities_excluded` / `entities_out`；异常时附 `warnings.{unknown_ids, duplicate_ids, already_excluded_ids}`。

### 子任务 1.3（MergeDuplicates）

**形态**：**合并型** step。LLM 给等价组，代码侧用**并查集传递闭包**做最终合并，canonical 选举与属性归并完全由代码决定（不交给 LLM）。

**输入**：当前活跃实体。**至少 2 个**活跃实体才会调 LLM，否则返回 `SKIPPED`。

**合并语义（必读）**：
- **canonical 选举**：合并组中 **id 字典序最小** 的实体作为 canonical（实现：并查集 union 时 `min(rootA, rootB)` 作为新 root）。**LLM 输出的 `canonical_name` 仅作为 rationale 提示，不会覆盖 canonical 自身的 name**。
- **传递闭包**：LLM 给出的多个等价组在 id 维度有重叠时（如 `{E1,E2}` 与 `{E2,E3}`），并查集自动闭合为 `{E1,E2,E3}`。`metrics.groups_applied` 反映闭包后的最终组数，可能少于 `groups_proposed`。
- **不删除 + 标签**：被合并实体打 `Exclusion(EXCLUDED_BY_DUPLICATE, rationale="merged into <canonical_id> ...", step="ifpug.s1_3_merge_duplicates")`。
- **关系沉淀**：每个被合并实体写一条 `EntityRelation(from_id=被合并, to_id=canonical, relation_type="duplicate_of", rationale=...)`，记入 `ctx.relations`。
- **属性 / source_refs 并集**：被合并实体的 `attributes`（按 name 去重）与 `source_refs`（按 `(quote, location)` 去重）追加到 canonical。属性顺序遵循插入顺序，便于审计。

**Prompt 策略**：
- System Prompt：`backend/app/prompts/ifpug_s1_3_merge_duplicates.txt`，可通过 `IFPUG_S1_3_MERGE_DUPLICATES_PROMPT_PATH` 覆盖。
- 要求 LLM 输出 `{"groups": [{"members": ["E001", "E003"], "canonical_name": "...", "rationale": "..."}]}`，每组至少 2 个互不相同的 id。

**容错策略**：未知 id 与已被前置步骤排除的 id 都写入 `metrics.warnings`；单组在清洗后剩余 ≤ 1 个有效成员时该组被丢弃（不入并查集）。

**StepRecord.metrics**：`entities_in` / `groups_proposed` / `groups_applied` / `entities_merged` / `entities_out` / `canonical_ids`；异常时附 `warnings.{unknown_ids, inactive_ids}`。

### 子任务 1.4 / 1.5 / 1.6（标签型过滤族）

三个步骤的**代码骨架与 s1_2 完全一致**，区别仅在 prompt 判断口径与写入的 `Exclusion.tag`：

| Step | 短名 | 标签 (`Exclusion.tag`) | 判断口径要点 |
|------|------|------------------------|----------------|
| `FilterCodeDataStep` | s1_4 | `EXCLUDED_BY_CODE_DATA` | 字典 / 枚举 / 常量表（省份、状态码、币种等），不参与业务过程更新 |
| `FilterNotUserRequiredStep` | s1_5 | `EXCLUDED_BY_NOT_USER_REQUIRED` | 非用户业务需求驱动（系统日志、内部缓存、会话等），除非用户合规需求显式要求保留 |
| `FilterAssociativeStep` | s1_6 | `EXCLUDED_BY_ASSOCIATIVE` | 仅承载实体间关联关系的实体（连接表），属性主要是外键、无独立业务概念 |

**共享行为**（与 s1_2 完全相同）：
- 打包一次 LLM 调用，把全部活跃候选作为 JSON 数组送入；要求 LLM 仅输出"应被排除"的 id 列表。
- 严格 JSON 校验（id / rationale 类型与长度），不合规字段抛对应的 `XxxAgentError`。
- 不删除元素，只追加 `Exclusion(tag, rationale, step)`。
- 未知 id / 同次重复 id / 已被前置步骤排除的 id 都写入 `metrics.warnings`，**不阻断**步骤。
- 活跃集为空时返回 `SKIPPED`，不调 LLM。
- StepRecord.metrics 字段与 s1_2 一致：`entities_in` / `entities_excluded` / `entities_out`，异常时附 `warnings.{unknown_ids, duplicate_ids, already_excluded_ids}`。

**Prompt 文件与 env 覆盖变量**：
- s1_4：`ifpug_s1_4_filter_code_data.txt` / `IFPUG_S1_4_FILTER_CODE_DATA_PROMPT_PATH`
- s1_5：`ifpug_s1_5_filter_not_user_required.txt` / `IFPUG_S1_5_FILTER_NOT_USER_REQUIRED_PROMPT_PATH`
- s1_6：`ifpug_s1_6_filter_associative.txt` / `IFPUG_S1_6_FILTER_ASSOCIATIVE_PROMPT_PATH`

> **设计说明**：四个分类型 step（s1_2 / s1_4 / s1_5 / s1_6）目前是结构同形的副本。未抽出基类的原因详见 PR3 讨论的"延迟抽象"原则——等到第二种形态（如多次采样投票）出现时再做"提取基类"的机械重构，比现在猜接口形状更安全。`metrics` 字段命名（`entities_in/out/excluded`, `warnings.*`）已统一，便于未来抽象时直接复用。

### 调试端点

`POST /agents/ifpug/logical-file/debug-run`：
- 入参支持 `until` 参数，按短名（`"s1_1"` ~ `"s1_6"`）截断流水线，便于逐步调优
- 返回完整的 ctx 快照：候选实体（含 exclusions）、活跃实体 id 列表、`relations`（s1_3 写入的 `duplicate_of` 关系）、所有 `step_records`（含 model/prompt_hash/usage/metrics）、累计 usage 和 abort 信息

**测试**：
- `test_ifpug_s1_1_agent.py`：领域结构、Context id 分配、s1_1 Prompt 加载、字段校验、Step 写回 ctx、Pipeline 装配。
- `test_ifpug_s1_2_agent.py`：s1_2 完整路径（Prompt 加载、JSON 严格校验、不删除写回、unknown / duplicate / already_excluded id warnings、空活跃集短路、截断装配）—— **作为标签型 step 的范式测试**。
- `test_ifpug_s1_3_agent.py`：s1_3 并查集合并、传递闭包（`{E1,E2}` + `{E2,E3}` → `{E1,E2,E3}`）、canonical = 最小 id、属性 / source_refs 去重并集、`EntityRelation(duplicate_of)` 写入、活跃实体 < 2 短路。
- `test_ifpug_s1_4_agent.py` / `test_ifpug_s1_5_agent.py` / `test_ifpug_s1_6_agent.py`：与 s1_2 形态相同，仅做核心 5 用例回归（标签 / step name 正确、SKIPPED 短路、warnings 路径），详细路径已在 s1_2 覆盖。
- `test_ifpug_pipeline_assembly.py`：全部 6 step 的注册顺序、`until=` 截断在每个短名上的等价性、类型匹配。
