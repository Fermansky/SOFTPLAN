# Agent 层（当前实现）

Agent 位于 `backend/app/agents/`，每个 Agent 是一个独立子目录，包含 `service.py`（调用逻辑）和 `prompting.py`（Prompt 加载）。

Agent 与 Service 的区别：Agent 直接调用 LLM 完成一个具体的智能任务，Service 负责业务编排。

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
- `pipeline.py`：`build_logical_file_pipeline(until=...)` 按已注册步骤顺序组装 `AgentPipeline`，支持按短名截断（调试用）。

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

### 调试端点

`POST /agents/ifpug/logical-file/debug-run`：
- 入参支持 `until` 参数，按短名（`"s1_1"`）截断流水线，便于逐步调优
- 返回完整的 ctx 快照：候选实体（含 exclusions）、活跃实体 id 列表、所有 `step_records`（含 model/prompt_hash/usage/metrics）、累计 usage 和 abort 信息

**测试**：`backend/tests/test_ifpug_s1_1_agent.py`（17 个用例）覆盖领域结构、Context id 分配、Prompt 加载、Agent 字段校验、Step 写回 ctx、Pipeline 装配。
