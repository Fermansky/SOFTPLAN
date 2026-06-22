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
