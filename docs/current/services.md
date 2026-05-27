# 服务层（当前实现）

所有服务位于 `backend/app/services/`，只负责业务逻辑，不负责 HTTP 路由。

---

## 存储服务

### MinioStorage
**文件**：`backend/app/services/minio_storage.py`

**职责**：封装对 MinIO 的所有读写操作，维护对象 key 生成规则。不负责数据库记录。

**Key 规则**：
- 文档对象：`documents/{yyyy}/{mm}/{uuid}{ext}`
- 图片对象：`images/{sha256}{ext}`（图片按内容哈希寻址，天然去重）

**关键函数**：
- `upload_document_bytes()`：上传文档，总是生成新 key；是否复用 FileRecord 由上层决定
- `upload_image_bytes()`：上传图片，按 sha256 去重，已存在则直接复用
- `object_exists()`：仅在 NoSuchKey 类错误时返回 False，其他 S3Error 继续抛出
- `download_object()`：下载对象字节
- `get_presigned_url()`：生成预签名 URL

**外部依赖**：MinIO（通过环境变量 `MINIO_ENDPOINT`、`MINIO_ACCESS_KEY`、`MINIO_SECRET_KEY`、`MINIO_BUCKET` 配置）

---

## 文档上传服务

### DocumentUploadService
**文件**：`backend/app/services/document_upload_service.py`

**职责**：编排文档上传链路：文件去重判断 → MinIO 上传 → FileRecord 持久化 → Document 创建。不负责路由参数提取。

**关键函数**：
- `resolve_file_record()`：三分支逻辑：①新文件，新建 FileRecord + 上传 MinIO；②hash 命中，复用已有 FileRecord；③hash 命中但 MinIO 对象丢失，修复 FileRecord 指向。
- `persist_document()`：创建 Document 记录，处理并发冲突后的补偿清理（若 MinIO 上传了新对象但 DB 写入冲突，需回收孤儿对象）

**外部依赖**：DB（写 FileRecord + Document）、MinioStorage

---

## 文档解析任务服务

### DocumentParsingTaskService
**文件**：`backend/app/services/document_parsing_task_service.py`

**职责**：编排文档解析任务的完整生命周期：创建或复用任务、任务状态同步、图片项初始化、孤儿任务恢复。是最复杂的服务。

**关键函数**：
- `create_or_reuse_document_parsing_task()`：按复用维度查找已有任务，不能复用则创建新任务并绑定 LayoutAnalysisTask
- `sync_document_parsing_task_from_layout()`：版面分析完成后，将 markdown + image_hashes 同步到 DocumentParsingTask，并初始化图片项
- `claim_next_pending_layout_task()`：Worker 领取待处理版面分析任务（`FOR UPDATE SKIP LOCKED` 防并发重复消费）
- `recover_orphaned_tasks()`：进程重启后，将残留 running 状态的任务标记为 failed

**外部依赖**：DB（读写所有解析任务表）、file-convert-service（通过 `FileConvertService` 间接）

---

## 版面分析任务服务

### LayoutAnalysisTaskService
**文件**：`backend/app/services/layout_analysis_task_service.py`

**职责**：创建或复用版面分析任务，执行 file-convert-service 调用，持久化结果。

**关键函数**：
- `create_or_reuse_layout_analysis_task()`：先查 `file_id + layout_model_key` 维度的已成功任务，可复用则直接返回；否则按活动任务唯一约束创建新任务
- `execute_layout_analysis()`：调用 file-convert-service，将结果写回 LayoutAnalysisTask

**外部依赖**：DB、FileConvertService

---

## 文件转换服务客户端

### FileConvertService
**文件**：`backend/app/services/file_convert_service.py`

**职责**：封装对外部 file-convert-service 的 HTTP 调用，处理 PDF 下载和 Markdown 转换。不在此服务内实现 PDF 解析。

**配置**：`FILE_CONVERT_SERVICE_BASE_URL` 环境变量（为空时服务启动但版面分析任务会失败）

**关键行为**：透传 `X-Request-ID` 请求头；历史兼容头 `X-Convert-Task-Id` 仍附带（过渡期）

**外部依赖**：外部 file-convert-service HTTP API

---

## 图片相关服务

### ExtractedImagePersistenceService
**文件**：`backend/app/services/extracted_image_persistence_service.py`

**职责**：批量持久化从 file-convert-service 返回的图片元数据，按 `file_hash` 去重（INSERT 冲突时 DO NOTHING）。不中断主流程。

**外部依赖**：DB（写 extracted_images）

---

### ExtractedImageSemanticTaskService
**文件**：`backend/app/services/extracted_image_semantic_task_service.py`

**职责**：创建图片语义任务，执行 LLM 语义分析，写入语义快照。

**关键函数**：
- `create_or_reuse_semantic_task()`：查活动任务唯一约束，可复用则返回已有任务
- `execute_semantic_analysis()`：调用 LLM，写 ExtractedImageSemanticSnapshot，通知父任务聚合

**外部依赖**：DB、LlmService

---

### ExtractedImageSemanticService
**文件**：`backend/app/services/extracted_image_semantic_service.py`

**职责**：图片语义分析的高层编排，协调快照复用判断和任务分发。

**外部依赖**：DB、ExtractedImageSemanticTaskService

---

## LLM 服务

### LlmService（通过 `get_llm_service_client()` 获取）
**文件**：`backend/app/services/llm_service.py`

**职责**：封装一次 LLM 调用：解析配置 → 构造 httpx 客户端 → 调用 OpenAI-compatible API → 写审计记录。

**配置解析优先级**：
1. 调用方显式传入 `config_id` → 使用指定 LlmConfig
2. 无 config_id → 使用 `is_active=true` 的 LlmConfig
3. DB 无配置 → 回退到环境变量（`LLM_API_BASE_URL`、`LLM_API_KEY`、`LLM_DEFAULT_MODEL`）

**关键行为**：
- 所有调用后写 `llm_chat_records` 审计记录
- 失败统一转为 `LlmServiceExecutionError`，不在此层重试
- `probe_models()`：用于验证配置连通性，超时 5 秒

**外部依赖**：DB（读 LlmConfig、写 llm_chat_records）、上游 LLM API（HTTP）

---

### LlmConfigService
**文件**：`backend/app/services/llm_config_service.py`

**职责**：LLM 配置的 CRUD 和解析逻辑（增删改查 + 激活切换）。不负责实际调用。

**关键函数**：
- `resolve_llm_config()`：按优先级解析当前生效的 LlmConfig
- `get_llm_config_or_raise()`：按 id 或 code 查询，不存在则抛 404
- `validate_llm_config_values()`：校验配置值的格式（不发送网络请求）

**外部依赖**：DB

---

### LlmChatPersistence
**文件**：`backend/app/services/llm_chat_persistence.py`

**职责**：将 LLM 调用结果写入 `llm_chat_records` 审计表。仅负责持久化，不做业务判断。

**外部依赖**：DB

---

### LlmJsonParser
**文件**：`backend/app/services/llm_json_parser.py`

**职责**：从 LLM 返回的文本中提取 JSON，处理 markdown 代码块包裹（` ```json ` 等）的情况。

**关键函数**：
- `parse_object()`：提取并解析 JSON 对象，失败抛 `LlmJsonParseError`
- `parse_array()`：提取并解析 JSON 数组

**外部依赖**：无（纯计算）
