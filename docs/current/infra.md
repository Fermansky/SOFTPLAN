# 基础设施（当前实现）

## 服务组成

| 服务 | 容器名 | 端口 | 说明 |
|------|--------|------|------|
| 前端 (Next.js) | softplan-web | 3000 | 前端应用 |
| 后端 (FastAPI) | softplan-api | 8000 | 后端 API 和 Worker |
| PostgreSQL 15 | softplan-postgres | 5432 | 主数据库 |
| MinIO | softplan-minio | 10000 (API) / 10001 (控制台) | 对象存储 |
| file-convert-service | 独立进程（不在 compose 内） | 自定义 | PDF 解析服务，需单独部署 |

**启动顺序**：api 依赖 postgres（health check 通过后才启动）和 minio（service_started 即可）。

---

## 后端环境变量

### 数据库
```
DATABASE_URL=postgresql+psycopg://softplan:softplan@postgres:5432/softplan
```

### MinIO
```
MINIO_ENDPOINT=minio:9000       # 容器内访问地址（本地开发用 localhost:10000）
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET=softplan
```

### 日志
```
APP_ENV=development              # development | staging | production
APP_LOG_LEVEL=INFO               # DEBUG | INFO | WARNING | ERROR | CRITICAL
APP_LOG_FORMAT=auto              # auto | console | json（auto 时开发用 console，生产用 json）
APP_LOG_ACCESS_ENABLED=true      # 是否记录 access log
```

### LLM（环境变量配置为兜底，优先使用数据库中的 LlmConfig）
```
LLM_API_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=                     # 必填
LLM_DEFAULT_MODEL=gpt-4o-mini
LLM_TIMEOUT_SECONDS=30
```

### 文档解析 Worker
```
FILE_CONVERT_SERVICE_BASE_URL=   # file-convert-service 的地址，为空则版面分析任务无法执行
DOCUMENT_PARSING_TASK_WORKER_ENABLED=true
DOCUMENT_PARSING_TASK_WORKER_POLL_INTERVAL_SECONDS=1.0
```

### Agent Prompt 路径（可选）
```
DOCUMENT_STRUCTURING_AGENT_PROMPT_PATH=   # 默认 backend/app/prompts/document_structuring_agent.txt
TEXT_SUMMARY_AGENT_PROMPT_PATH=           # 默认 backend/app/prompts/text_summary_agent.txt
```

---

## 前端环境变量

```
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000   # 前端访问后端 API 的基础 URL
```

---

## 内置 Worker

后端进程在 FastAPI startup 事件中启动以下后台 Worker（独立 asyncio 任务，与 API 同进程）：

- **LayoutAnalysisTaskWorker**：轮询 `layout_analysis_tasks`，领取 pending 任务，调用 file-convert-service 执行版面分析
- **ExtractedImageSemanticTaskWorker**：轮询 `extracted_image_semantic_tasks`，领取 pending 任务，调用 LLM 执行图片语义分析

Worker 通过 `FOR UPDATE SKIP LOCKED` 实现并发安全领取。进程重启时恢复孤儿任务（将残留 running 标记为 failed）。

---

## 本地开发启动

```bash
# 复制环境变量
cp .env.example .env
# 编辑 .env，至少填写 LLM_API_KEY 和 FILE_CONVERT_SERVICE_BASE_URL

# 启动所有容器
docker compose up --build
```

**注意**：file-convert-service 不在 docker-compose 中，需要单独启动并配置 `FILE_CONVERT_SERVICE_BASE_URL`。若不启动，版面分析任务会一直 pending 或 failed，但其他功能正常。
