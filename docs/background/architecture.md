# 架构决策记录（ADR）

每次做出重大技术选型或架构调整时，在此追加一条 ADR。
格式：标题、日期、背景、决策、放弃的方案、后果。

---

## ADR-001: 使用 FastAPI + SQLModel 作为后端框架
**日期**：2026-03-08  
**状态**：已采纳

**背景**：需要一个 Python 后端框架，能方便集成 LLM，同时支持 async。

**决策**：FastAPI + SQLModel（SQLAlchemy + Pydantic 的统一封装）。

**放弃了什么**：Django（太重，ORM 不适合 Pydantic 生态）；Flask（无 async 原生支持）。

**后果**：数据模型类同时是 API 的 response schema，减少重复。但 SQLModel 目前不支持 Alembic 的自动迁移，改为 `SQLModel.metadata.create_all()` 直接建表。

---

## ADR-002: 不使用 Alembic，用 create_all() 直接建表
**日期**：2026-03-08  
**状态**：已采纳

**背景**：SQLModel 与 Alembic 的集成有已知兼容问题，当前项目处于快速迭代期。

**决策**：启动时调用 `SQLModel.metadata.create_all(engine)`，仅支持"新库全量初始化"，不支持增量迁移。

**放弃了什么**：Alembic 提供的精确版本化迁移。

**后果**：字段变更必须手动处理旧数据库（当前阶段可接受）。迁移到生产环境前需要补 Alembic。

---

## ADR-003: 文件存储使用 MinIO，不使用本地磁盘
**日期**：2026-03-11  
**状态**：已采纳

**背景**：PDF 文件需要持久化存储，并支持容器化部署场景下的共享访问。

**决策**：所有上传文件存储在 MinIO，通过 `file_hash`（SHA-256）去重。`files` 表记录物理文件，`documents` 表记录业务信息，一个物理文件可对应多个业务文档记录。

**放弃了什么**：本地磁盘挂载（容器部署不友好）；直接存 DB BLOB（文件太大）。

**后果**：需要 MinIO 服务常驻。文件去重通过 `file_hash` 唯一索引实现"秒传"语义。

---

## ADR-004: PDF 解析外包给独立的 file-convert-service
**日期**：2026-04-12  
**状态**：已采纳

**背景**：PDF → Markdown 的版面分析（Layout Analysis）依赖 Python 的视觉理解库（如 marker），这些库体积大、启动慢，且与主业务无关。

**决策**：将 PDF 转 Markdown 的能力拆分为独立的 `file-convert-service` 微服务，通过 HTTP 调用（`FILE_CONVERT_SERVICE_BASE_URL` 环境变量配置）。主服务只保留任务调度和结果持久化。

**放弃了什么**：在主服务内直接集成 PyMuPDF / Unstructured（耦合度高、镜像太大）。

**后果**：部署时需要额外启动 file-convert-service。本地开发如果不启动该服务，文档解析任务会 pending 或 failed。`X-Request-ID` 需要透传。

---

## ADR-005: LLM 调用自建 LlmService，不使用 LiteLLM/LangChain
**日期**：2026-04-19  
**状态**：已采纳

**背景**：规划阶段考虑过 LiteLLM 和 LangChain，但实际需求是：支持多套 LLM 配置、运行时切换模型、审计所有调用记录。

**决策**：自建 `LlmService`（`backend/app/services/llm_service.py`），通过 `LlmConfig` 数据库表管理多套 provider 配置，每次调用写 `llm_chat_records` 审计表。

**放弃了什么**：LiteLLM（黑盒，不方便审计）；LangChain（对当前场景过重，Prompt 管理另建 `prompts/` 目录替代）。

**后果**：需要自己维护各 provider 的 API 协议兼容（当前只支持 OpenAI 兼容协议）。新增 provider 需要修改 `LlmService`。

---

## ADR-006: 文档解析采用聚合父任务模型（四层任务架构）
**日期**：2026-04-10  
**状态**：已采纳

**背景**：文档解析实际包含两个串联阶段：版面分析（PDF → Markdown + 图片列表）和图片语义分析（每张图片用 LLM 生成描述）。早期把两者混在一个任务里，导致状态语义混乱。

**决策**：拆分为四层模型：
- `LayoutAnalysisTask`：版面分析，产出 Markdown + image_hashes
- `DocumentParsingTask`：聚合父任务，绑定 layout_task，追踪图片项完成状态
- `DocumentParsingImageItem`：每张图片的状态记录，隶属于 DocumentParsingTask
- `ExtractedImageSemanticSnapshot`：图片语义结果的模型级快照，用于跨任务复用

详细说明见 `docs/current/data-model.md`。

**放弃了什么**：单一任务表同时记录版面分析和图片语义状态。

**后果**：任务状态聚合逻辑复杂，但复用粒度更精确（版面分析按 `file_id + layout_model_key` 复用，图片语义按 `extracted_image_id + target_model_key` 复用）。

---

## ADR-007: Agent 系统采用模块化目录结构
**日期**：2026-04-19  
**状态**：已采纳

**背景**：随着 LLM 功能增多，需要一个统一的 Agent 组织方式，避免 Prompt 和调用逻辑散落各处。

**决策**：每个 Agent 是 `backend/app/agents/` 下的独立子目录，包含 `service.py`（调用逻辑）和 `prompting.py`（Prompt 管理）。

**放弃了什么**：在 service 层直接内联 Prompt 字符串。

**后果**：新增 Agent 有固定模板可遵循，见 `docs/standards/how-to-add-agent.md`。
