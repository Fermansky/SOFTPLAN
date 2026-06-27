# API 端点（当前实现）

后端服务地址：`http://localhost:8000`。完整交互文档见 `/docs`（Swagger UI）。

前缀约定：所有路由无统一前缀，直接挂在根路径下。

---

## 健康检查

```
GET /health
```
返回服务运行状态，无参数。

---

## 项目管理 `/projects`

```
GET    /projects                    # 列表，默认过滤软删除
POST   /projects                    # 创建，body: ProjectCreate
GET    /projects/{project_id}       # 详情
PATCH  /projects/{project_id}       # 局部更新，body: ProjectUpdate
DELETE /projects/{project_id}       # 软删除（写入 deleted_at）
```

---

## 软件资产 `/softwares`

```
GET    /softwares                   # 列表
POST   /softwares                   # 创建，body: SoftwareCreate
GET    /softwares/{software_id}     # 详情
PATCH  /softwares/{software_id}     # 局部更新
DELETE /softwares/{software_id}     # 软删除
```

---

## 项目软件关联 `/project-software-relations`

```
GET    /project-software-relations                          # 按 project_id 过滤
POST   /project-software-relations                          # 创建关联
DELETE /project-software-relations/{project_id}/{software_id}  # 删除关联
```

---

## 文档管理 `/documents`

```
POST   /documents                   # 上传文档，multipart/form-data（文件 + 元数据）
GET    /documents                   # 列表，支持 project_id / software_id 过滤
GET    /documents/{document_id}     # 详情
PATCH  /documents/{document_id}     # 更新元数据（名称、描述等）
DELETE /documents/{document_id}     # 软删除
GET    /documents/{document_id}/download  # 从 MinIO 下载原始文件
```

---

## 抽取图片 `/extracted-images`

```
GET    /extracted-images/{image_id}  # 获取图片元数据（含语义描述）
GET    /extracted-images/{image_id}/download  # 下载图片
```

---

## 版面分析任务 `/layout-analysis`

关注单一阶段：PDF → Markdown + 图片列表。

```
POST   /layout-analysis/tasks                   # 创建或复用版面分析任务
GET    /layout-analysis/tasks/{task_id}         # 获取任务状态和结果
GET    /layout-analysis/results/{document_id}   # 获取文档最新成功的版面分析结果
```

---

## 文档解析任务 `/document-parsing`

关注两阶段聚合（版面分析 + 图片语义），是完整解析的主入口。

```
POST   /document-parsing/tasks                  # 创建或复用文档解析任务
GET    /document-parsing/tasks/{task_id}        # 获取任务状态（含 layout_status、image_analysis_status、图片进度）
GET    /document-parsing/results/{document_id}  # 获取文档最新成功的解析结果
```

响应中的关键字段：
- `layout_status`：版面分析阶段状态（独立于整体 status）
- `image_analysis_status`：图片语义阶段状态
- `image_total_count` / `image_succeeded_count` / `image_failed_count`：图片进度
- `markdown`：版面分析完成后即可读取（不等待图片语义完成）

---

## LLM 配置 `/llm`

```
GET    /llm/configs                 # 列表（api_key 脱敏）
POST   /llm/configs                 # 创建配置
GET    /llm/configs/{config_id}     # 详情
PATCH  /llm/configs/{config_id}     # 更新
DELETE /llm/configs/{config_id}     # 软删除
POST   /llm/configs/{config_id}/activate    # 设为激活配置
POST   /llm/configs/{config_id}/probe-models  # 测试连通性，返回可用模型列表
GET    /llm/availability            # 当前激活配置是否可用
POST   /llm/chat                    # 直接发送 LLM 聊天请求（用于前端测试）
```

---

## Agent 调试接口 `/agents`

当前为调试用，不是生产流程入口。

```
POST   /agents/document-structuring/debug-run    # 直接运行文档结构化 Agent
POST   /agents/text-summary/debug-run            # 直接运行文本摘要 Agent
POST   /agents/ifpug/logical-file/debug-run      # 运行 IFPUG 逻辑文件识别流水线
```

`/agents/document-structuring/debug-run` 与 `/agents/text-summary/debug-run` 均接受 `source_text`、可选 `config_id`、`model`、`temperature`、`max_tokens`。

`/agents/ifpug/logical-file/debug-run` 入参：
- `source_document`：已结构化文档文本（必填，最大 200,000 字符）
- `counting_scope` / `user_requirements`：计数范围与用户需求描述（最大 8000 字符，可空）
- `config_id` / `model` / `temperature` / `max_tokens` / `request_id`：与其他 Agent 一致
- `until`：可选，按短名截断流水线。当前可选值：`"s1_1"`（仅识别候选）、`"s1_2"`（含未维护过滤）、`"s1_3"`（含同义合并）。

返回：完整 ctx 快照，含 `candidate_entities`（每个含 `id` / `attributes` / `source_refs` / `exclusions`）、`active_entity_ids`、`relations`（如 s1_3 写入的 `duplicate_of` 关系）、`step_records`（每步的 `model` / `prompt_hash` / `usage` / `metrics`，metrics 字段随 step 类型而不同 —— 提取型给 `entities_in/out`，过滤型给 `entities_excluded` 与可选的 `warnings`，合并型给 `groups_proposed/applied`、`entities_merged`、`canonical_ids`）、`total_usage`、`aborted` / `abort_reason` / `aborted_step` 与 `registered_steps`。
