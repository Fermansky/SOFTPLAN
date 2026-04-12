# Python 服务统一日志规范

## 1. 目标与非目标

### 目标

- 统一当前仓库内 `backend` 服务的日志配置方式，并约束它与外部 Python 服务的链路追踪行为。
- 统一日志字段、日志级别、请求链路追踪和脱敏规则。
- 让本地开发环境可读，生产环境可结构化采集。
- 明确路由层、服务层、worker 层、下游 client 层的打点边界。

### 非目标

- 本文档不覆盖 ELK、Loki、Sentry、OpenTelemetry 等中心化日志/可观测平台接入。
- 本文档不要求一次性重写所有历史日志，但新代码与热点模块必须遵守规范。

## 2. 实现基线

- 基线技术：Python 标准库 `logging` + `logging.config.dictConfig()`。
- 轻量依赖：
  - `python-json-logger`
  - `asgi-correlation-id`
- 当前仓库实现：
  - `backend` 的实现位于 `backend/app/core/logging.py`
  - 业务模块统一从 `backend.app.core.logging` 导入日志辅助函数
  - 当前模块暴露：
    - `configure_logging(service_name: str) -> None`
    - `install_request_id_middleware(app: FastAPI, *, legacy_header_name: str | None = None) -> None`
    - `get_request_id() -> str | None`
    - `build_log_extra(event: str, **fields) -> dict[str, Any]`
- 兼容策略：若当前环境尚未安装上述依赖，代码会回退到内置兼容实现，避免本地离线环境无法启动。
- 本地开发约定：
  - 直接安装 `backend/requirements.txt` 中依赖即可
  - Docker 镜像直接复制 `backend/app`，不再依赖仓库内公共 Python 包

## 3. 日志输出格式

### 环境变量

- `APP_ENV`：默认 `development`
- `APP_LOG_LEVEL`：默认 `INFO`
- `APP_LOG_FORMAT`：默认 `auto`
- `APP_LOG_ACCESS_ENABLED`：默认 `true`

### 格式决策

- `APP_LOG_FORMAT=auto` 时：
  - `development`、`test` 使用 console 格式
  - `staging`、`production` 使用 JSON 格式
- console 格式用于本地可读性。
- JSON 格式用于容器采集、检索与聚合。

## 4. 字段模型

### 必填字段

- `timestamp`
- `level`
- `service`
- `logger`
- `message`

### 条件字段

- `event`
- `request_id`
- `method`
- `path`
- `status_code`
- `duration_ms`

### 常见业务字段

- `document_id`
- `file_id`
- `image_id`
- `task_id`
- `storage_key`
- `storage_bucket`
- `model`
- `requested_model`
- `target_model`
- `attempt_count`
- `error`
- `error_code`

### 约束

- `event` 使用英文点号命名，如：
  - `llm.chat.started`
  - `document_parsing.sync.failed`
  - `pdf_to_markdown.succeeded`
- `message` 保持简短英文短句。
- 业务维度优先写入 `extra` 字段，不再拼进 message。

### 推荐示例

```python
logger.info(
    "Document parsing task submitted",
    extra=build_log_extra(
        "document_parsing.task_create.succeeded",
        document_id=str(document_id),
        file_id=str(file_id),
        task_id=str(task.id),
        reused=False,
    ),
)
```

## 5. 请求链路规范

### 统一请求头

- 主请求链路头：`X-Request-ID`
- `backend` 必须：
  - 优先复用入站 `X-Request-ID`
  - 缺失时自动生成
  - 在响应头中回写 `X-Request-ID`

### 跨服务透传

- `backend ->` 上游 LLM API 请求必须透传 `X-Request-ID`
- `backend -> file-convert-service` 必须透传 `X-Request-ID`
- worker 场景下若无上游请求上下文，可退化为使用任务 ID 作为链路标识

### 旧头兼容

- 外部 `file-convert-service` 历史上使用 `X-Convert-Task-Id`
- 当前兼容策略位于 `backend/app/services/file_convert_service.py`：
  - `backend` 出站请求继续附带 `X-Convert-Task-Id`
  - `backend` 自身日志与入站请求上下文统一使用 `X-Request-ID`
- `X-Convert-Task-Id` 为过渡头，不再作为新规范主头

## 6. 日志级别矩阵

### `DEBUG`

- 高频但仅对深度排查有价值的临时调试信息
- 默认关闭，不作为常规业务日志依赖

### `INFO`

- 创建/更新/删除/提交任务成功
- 外部调用开始/成功
- 关键状态流转
- worker 启停
- 恢复孤儿任务的结果摘要
- 脱敏后的启动配置摘要

### `WARNING`

- 上游服务不可用
- 外部调用超时/退化/回退路径
- 可预期但需要关注的失败
- 用户请求中“值得排障”的异常输入
- 资源缺失但系统仍能给出清晰降级结果

### `ERROR`

- 真正的失败边界
- 已经影响本次业务请求或任务执行结果
- 仅在需要堆栈时使用 `logger.exception(...)`

### `CRITICAL`

- 缺失关键配置导致服务无法工作，并且准备终止启动

## 7. 分层打点规范

### 路由层

- 普通 GET、健康检查不额外打业务 INFO，依赖 access log 即可。
- 创建、修改、删除、提交任务类请求打 INFO。
- 常见 4xx 不打堆栈。
- 若 4xx 暗示环境问题、资源异常或协议偏差，可打 WARNING。

### 服务层

- 外部依赖调用前后、关键状态切换、去重复用、补偿修复打 INFO。
- 上游不可用、超时、退化、回退路径打 WARNING。
- 真正失败边界打 ERROR/EXCEPTION。

### Worker 层

- 启动、停止、领取任务、任务完成、孤儿任务恢复打 INFO/WARNING。
- 空轮询、无任务可取、重复状态检查不打日志。

### 下游 Client 层

- 记录请求目标、模型、状态码、错误类型、request_id。
- 不记录 prompt 正文、图片内容、完整响应体。
- 对大响应体仅允许截断摘要。

## 8. 脱敏红线

### 严禁记录

- prompt 正文
- system prompt 正文
- 图片二进制或 base64 正文
- API key
- `Authorization` 头
- Cookie
- 完整 markdown 正文
- 大体积响应体

### 允许记录的脱敏摘要

- `api_key_present=True/False`
- `prompt_length`
- `image_part_count`
- `input_part_count`
- `total_tokens`
- 截断后的上游错误 body

## 9. 实施规则

### MUST

- 新日志优先采用 `message + extra=build_log_extra(...)` 风格。
- 异常边界只记录一次堆栈，避免多层重复打印同一异常。
- 所有跨服务 HTTP 调用透传 `X-Request-ID`。
- `backend` 启动时最早调用 `configure_logging(service_name)`。
- `backend` 在 FastAPI 应用创建后立即安装 request id 中间件。

### MUST NOT

- 不把业务字段硬编码进 message 模板作为唯一结构化来源。
- 不在健康检查、空轮询、普通查询路径刷大量 INFO。
- 不记录 secrets、原始 prompt、二进制正文。
- 不在捕获异常后重复 `logger.exception(...)` 多次。

## 10. 代码示例

### 路由层

```python
logger.info(
    "Forwarding llm chat request",
    extra=build_log_extra(
        "llm.chat.started",
        request_id=resolved_request_id,
        prompt_length=len(prompt),
        extracted_image_count=len(extracted_image_ids),
        has_custom_model=bool(payload.model),
    ),
)
```

### 服务层

```python
logger.warning(
    "LLM upstream HTTP error",
    extra=build_log_extra(
        "llm.upstream.http_error",
        request_id=resolved_request_id,
        base_url=self.base_url,
        target_model=target_model,
        status_code=status_code,
        response_body=body_text or "no body",
    ),
)
```

### Worker 层

```python
logger.info(
    "Document parsing task worker started",
    extra=build_log_extra("document_parsing.worker.started"),
)
```

## 11. 迁移清单

### 已完成热点模块

- `backend/app/main.py`
- `backend/app/api/routers/llm.py`
- `backend/app/api/routers/document_parsing.py`
- `backend/app/api/routers/extracted_images.py`
- `backend/app/services/llm_service.py`
- `backend/app/services/file_convert_service.py`

### 后续建议

- 继续迁移 `backend` 中历史较早的 CRUD 路由与任务服务日志。
- 为 worker 服务层补齐统一 `event` 命名与 `extra` 字段。
- 若后续接入集中式日志平台，可直接消费 JSON 日志，无需重做字段模型。
