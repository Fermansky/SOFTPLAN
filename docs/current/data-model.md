# 数据模型（当前实现）

数据库：PostgreSQL 15。ORM：SQLModel（SQLAlchemy + Pydantic）。
建表方式：启动时 `SQLModel.metadata.create_all(engine)`，**无迁移工具**，字段变更需手动处理存量数据库。

所有表均用 UUID 或 BIGINT 作主键。通用字段（id、created_at、updated_at）不重复描述。

---

## 基础业务表

### projects

**职责**：项目主实体，所有分析任务的顶级容器。

**关键字段**：
- `status`：`draft` | `analyzing` | `completed` | `archived`
- `current_version_id`：UUID，指向当前活跃版本（字段保留，版本机制尚未完整实现）
- `deleted_at`：软删除，查询默认过滤 `deleted_at IS NULL`

---

### softwares

**职责**：软件资产注册表，跨项目引用的唯一基准。

**关键字段**：
- `code`：唯一索引，人类可读标识（如 `SW-001`），用于跨项目引用
- `deleted_at`：软删除

---

### project_software_relation

**职责**：项目与软件的多对多关联，记录项目上下文中的软件版本号。

**关键字段**：
- 复合主键：`(project_id, software_id)`
- `version`：该软件在本项目中涉及的版本号（非 projects.current_version_id）

---

### files

**表名**：`files`（模型类名 `FileRecord`）

**职责**：物理文件的存储映射，与业务语义无关，专注去重和定位。

**关键约束**：`file_hash` 全局唯一索引，实现秒传：上传前计算 SHA-256，命中则直接复用现有记录。

**关键字段**：
- `file_hash`：SHA-256 hex，唯一
- `storage_bucket` / `storage_key`：MinIO 中的桶名和对象路径

---

### documents

**职责**：业务文档记录，关联物理文件，归属于项目（可选归属软件）。

**关键字段**：
- `file_id`：关联 `files.id`，可为 NULL（文件被删除后变成孤儿记录）
- `software_id`：可选，文档归属于哪个软件（NULL 则直属项目）
- `extra_info`：JSONB，存储附加元数据，结构由业务决定
- `deleted_at`：软删除

---

## 文档解析任务表

文档解析链路共四层，详细流程见 ADR-006（`docs/background/architecture.md`）。

### layout_analysis_tasks

**职责**：版面分析任务，调用 file-convert-service 将 PDF 转为 Markdown + 图片列表。

**关键约束**：活动任务唯一索引 `(document_id, layout_model_key)`，只允许一条 `pending/running` 记录。

**关键字段**：
- `requested_layout_model`：调用方传入的模型名（可为 NULL，表示用默认）
- `target_layout_model`：实际解析时使用的模型（默认 `marker`）
- `layout_model_key`：去重与复用的稳定键（通常等于 target_layout_model）
- `layout_result_source_task_id`：复用已有结果时，指向来源任务（标记来源，非父子关系）
- `force_layout_analysis`：强制重新分析，忽略复用
- `status`：`pending` | `running` | `succeeded` | `failed`
- `markdown`：产出的 Markdown 文本
- `image_hashes`：JSONB，`{source_key: file_hash}` 映射，版面分析产出的图片引用

**复用维度**：`file_id + layout_model_key`，同文件同模型的成功结果可复用。

---

### document_parsing_tasks

**职责**：文档解析聚合父任务，汇总版面分析 + 图片语义分析的整体完成状态。

**关键约束**：活动任务唯一索引 `(document_id, layout_model_key, image_model_key, image_llm_config_key, force_image_semantic_recognition)`。

**关键字段**：
- `layout_task_id`：绑定的 `LayoutAnalysisTask` ID（必填）
- `image_model_key`：图片语义模型键，默认值 `__LLM_SERVICE_DEFAULT__`（表示用当前激活 LLM 配置）
- `image_llm_config_key`：图片语义 LLM 配置键，默认值 `__ACTIVE_LLM_CONFIG__`
- `force_image_semantic_recognition`：强制重新执行图片语义分析
- `status`：`pending` | `running` | `succeeded` | `failed`（聚合状态，表示整次任务是否完成）
- `markdown`：从 LayoutAnalysisTask 同步过来的 Markdown
- `image_hashes`：从 LayoutAnalysisTask 同步过来的图片映射
- `image_total_count` / `image_succeeded_count` / `image_failed_count`：图片项进度统计

**状态判定规则**：
- `succeeded`：LayoutAnalysisTask succeeded + 所有图片项 succeeded（或无图片项）
- `failed`：LayoutAnalysisTask failed，或任一必要图片项 failed
- `running`：版面分析完成但图片项未全部完成

**复用维度**：`file_id + layout_model_key + image_model_key`，需要两段都成功才可整体复用。

---

### document_parsing_image_items

**职责**：文档解析任务下每张图片的状态记录，连接父任务与图片语义任务。

**关键约束**：唯一索引 `(document_parsing_task_id, source_key)`，同一父任务下 source_key 不重复。

**主键**：BIGINT，IDENTITY（自增）。

**关键字段**：
- `source_key`：图片在版面分析结果中的来源标识（`image_hashes` 的 key）
- `file_hash`：图片的 SHA-256
- `extracted_image_id`：关联 `extracted_images.id`
- `semantic_task_id`：关联 `extracted_image_semantic_tasks.id`，可为 NULL（命中快照则不建任务）
- `result_source`：`semantic_snapshot`（命中快照）| `reused_semantic_task`（复用进行中任务）| `submitted_semantic_task`（新建任务）
- `status`：`pending` | `running` | `succeeded` | `failed`

---

## 图片语义表

### extracted_images

**职责**：从文档中抽取的图片对象注册表，供语义分析任务引用。

**关键约束**：`file_hash`（64位 SHA-256）全局唯一，去重。

**主键**：BIGINT，IDENTITY（自增）。

**关键字段**：
- `storage_bucket` / `storage_key`：MinIO 位置
- `width` / `height`：像素尺寸（可为 NULL）
- `semantic_description` / `semantic_description_model`：**遗留字段**，仍保留但不再作为主复用依据（主复用依据改为 `ExtractedImageSemanticSnapshot`）

---

### extracted_image_semantic_tasks

**职责**：单张图片的语义分析任务，调用 LLM 生成图片描述。

**关键约束**：活动任务唯一索引 `(extracted_image_id, target_model_key, llm_config_key, overwrite_existing_snapshot)`。

**主键**：UUID。

**关键字段**：
- `target_model_key`：语义分析的模型键，用于复用判断
- `llm_config_key`：LLM 配置键，`__ACTIVE_LLM_CONFIG__` 表示用激活配置
- `overwrite_existing_snapshot`：是否强制覆盖已有快照
- `prompt_path` / `prompt_hash`：记录本次使用的 Prompt 文件路径和内容哈希，便于追踪 Prompt 变更
- `description`：任务成功后产出的图片语义描述

---

### extracted_image_semantic_snapshots

**职责**：图片语义结果的模型级快照，实现跨任务复用。

**关键约束**：唯一索引 `(extracted_image_id, target_model_key)`，同图片同模型只保留一条。

**主键**：BIGINT，IDENTITY（自增）。

**关键字段**：
- `target_model_key`：快照所属的模型键
- `result_model`：实际产出快照时使用的模型标识（可能与 target_model_key 不同）
- `description`：图片语义描述文本
- `source_task_id`：生成本快照的任务 ID（标记来源）

**复用规则**：当 `DocumentParsingImageItem` 初始化时，先查此表，命中则直接标记 `result_source=semantic_snapshot`，不新建语义任务。

---

## LLM 相关表

### llm_configs

**职责**：LLM 提供商配置表，支持多套配置并标记激活态。

**关键字段**：
- `code`：唯一索引，稳定的人类可读标识，服务间用此引用配置
- `provider`：目前只支持 `openai_compatible`
- `is_active`：标记当前激活配置（全局只应有一条 is_active=true）
- `enabled`：整体启停开关
- `api_key`：**不对外暴露**，Read 模型用 `has_api_key` + `api_key_masked` 代替
- `deleted_at`：软删除

---

### llm_chat_records

**职责**：所有 LLM 调用的审计记录，用于排障和成本统计。

**主键**：INT，自增。

**关键字段**：
- `caller_service`：调用方服务名（如 `backend.agent.document_structuring`）
- `request_id`：链路追踪 ID
- `llm_config_id` / `llm_config_code`：调用时使用的配置
- `requested_model` / `resolved_model`：请求的模型 vs 实际解析使用的模型
- `prompt_tokens` / `completion_tokens` / `total_tokens`：token 消耗
- `reasoning_content`：模型的 thinking/reasoning 内容（支持推理模型）
- `input_parts_snapshot`：调用时的输入片段快照（JSON），结构由调用方约定
- `duration_ms`：耗时毫秒
