# Softplan

基于大模型的**软件项目规模与成本估算分析平台**。从项目文档中自动提取需求信息，进行功能点分析与成本估算，支持对中间结果审阅、修正和重新估算。

## 技术栈

| 层 | 技术 |
|----|------|
| 前端 | Next.js 14 (App Router) · React 18 · TypeScript · Tailwind CSS · Shadcn/UI |
| 后端 | FastAPI · SQLModel · Python |
| 数据库 | PostgreSQL 15 |
| 对象存储 | MinIO (S3 兼容) |
| LLM | OpenAI 兼容协议，支持多配置，调用结果全量审计 |
| PDF 解析 | 独立 file-convert-service（HTTP 调用） |

## 快速启动

**前置要求**：Docker、Docker Compose、以及单独运行的 file-convert-service。

```bash
cp .env.example .env
# 编辑 .env，填写 LLM_API_KEY 和 FILE_CONVERT_SERVICE_BASE_URL

docker compose up --build
```

| 服务 | 地址 |
|------|------|
| 前端 | http://localhost:3000 |
| 后端 API | http://localhost:8000 |
| MinIO 控制台 | http://localhost:10001 |

> `file-convert-service` 不在 docker-compose 中，需单独部署。若未配置，PDF 解析任务会 pending/failed，其他功能正常。

## 关键环境变量

```bash
# 数据库（docker-compose 内默认已配置）
DATABASE_URL=postgresql+psycopg://softplan:softplan@postgres:5432/softplan

# MinIO（docker-compose 内默认已配置）
MINIO_ENDPOINT=minio:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET=softplan

# LLM（必填）
LLM_API_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=sk-...
LLM_DEFAULT_MODEL=gpt-4o-mini

# PDF 解析服务（必填，否则文档解析不可用）
FILE_CONVERT_SERVICE_BASE_URL=http://host:port

# 前端（本地开发）
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

## 项目结构

```
backend/
  app/
    models/       # SQLModel 数据模型（同时作为 API schema）
    services/     # 业务逻辑层
    agents/       # LLM Agent（每个 agent 含 service.py + prompting.py）
    api/routers/  # FastAPI 路由
    prompts/      # Agent prompt 文件
frontend/
  src/app/        # Next.js App Router 页面
docs/
  background/     # 产品定位、架构决策（ADR）
  current/        # 数据模型、服务、API、基础设施现状
  standards/      # 编码规范、新增 Agent 指南
CODEBUDDY.md      # AI 助手读取入口
```

## 当前实现范围

- 项目与软件管理
- PDF 文档上传与解析（文件按 SHA-256 去重）
- 文档结构化 / 文本摘要（LLM Agent）
- LLM 配置管理（支持多配置，DB 优先于环境变量）

**尚未实现**：IFPUG 功能点分析、成本估算、报告生成、人工修正 UI。

## 文档

AI 助手从 `CODEBUDDY.md` 入口读取，`docs/` 目录按三层组织：背景（为什么）→ 现状（是什么）→ 规范（怎么做）。
