# Softplan 技术栈及架构方案 (V0.1)

## 1. 总体设计原则

- **工程与研究并重**：后端使用 Python 生态，便于集成最新的大模型（LLM）分析框架与算法研究成果。
- **分析工作台体验**：前端采用 Next.js 响应式架构，支撑复杂的“分析 -> 修正 -> 重新计算”交互流。
- **严谨的数据血缘**：通过关系型数据库 PostgreSQL 确保项目、版本、修改记录的强一致性。

## 2. 前端技术栈 (Frontend)

追求“干净、简洁、专业”的 UI 风格，同时保证处理复杂分析数据的性能。

- **核心框架**: **Next.js 14+ (App Router)**
  - 利用 SSR/SSG 提升首屏加载，API Routes 处理轻量级前端逻辑。
- **样式处理**: **Tailwind CSS**
  - 原子化 CSS，快速实现 PRD 要求的高密度、专业化布局（如分栏分析工作台）。
- **组件库**: **Shadcn UI (基于 Radix UI)**
  - 提供高度可定制的 UI 组件（Table, Dialog, Tabs, Card），符合“简洁严谨”的视觉要求。
- **状态管理与数据流**:
  - **TanStack Query (React Query)**: 负责异步数据的获取、缓存及同步，特别适合处理耗时的 LLM 分析状态流转。
  - **Zustand**: 处理轻量级的全局 UI 状态（如侧边栏收缩、当前版本切换）。
- **图标库**: **Lucide React** (轻量、现代)。

## 3. 后端技术栈 (Backend)

负责复杂的业务逻辑编排、文档解析流以及 LLM 的调用与后处理。

- **核心框架**: **FastAPI (Python 3.10+)**
  - 高性能异步框架，原生支持 Pydantic 数据校验，适合构建与大模型交互的 IO 密集型服务。
- **AI/LLM 编排**:
  - **LangChain / LlamaIndex**: 负责 Prompt 模板管理、解析链（Chains）构建及长文本分段。
  - **LiteLLM**: 统一 API 调用（兼容 OpenAI, Claude, DeepSeek 等），方便研究者切换不同基座模型。
- **文档解析引擎**:
  - **PyMuPDF (fitz)** / **Unstructured**: 用于 PDF 文档的提取与结构化转化。
- **任务队列 (可选)**:
  - **Celery + Redis**: 处理超过 30 秒的长耗时分析任务，避免接口超时，配合 WebSocket 或轮询返回进度。

## 4. 数据库与存储 (Data Layer)

满足 PRD 中“版本管理”和“修改记录追踪”的严谨性要求。

- **数据库**: **PostgreSQL 15+**
  - **JSONB 支持**: 用于存储 LLM 输出的原始结构化 JSON 片段，兼顾灵活性。
  - **关系约束**: 严格管理 `Project -> Document -> Version -> FunctionalPoint` 的层级关系。
- **ORM**: **SQLModel (SQLAlchemy + Pydantic)**
  - FastAPI 社区的首选，通过 Python 类定义实现数据库表与 API 模型的高度统一。
- **迁移工具**: **Alembic**。
- **文件存储**:
  - **本地存储 / MinIO**: 存储原始上传的 PDF 协议文件。

## 5. 核心架构与核心表设计思路

### 5.1 架构示意

```
用户浏览器 (Next.js) <-> REST API (FastAPI) <-> 任务链 (LangChain) <-> 数据库 (Postgres)
```

### 5.2 核心数据对象逻辑

1. **Project (项目)**: 基础元数据。
2. **Document (文档)**: 上传的 PDF 记录，包含结构化后的文本。
3. **AnalysisVersion (分析版本)**: 每次重算或关键修正生成一个 Snapshot。
4. **RequirementItem (需求项)**: 从文档提取的原子需求。
5. **FunctionalPoint (功能点)**: 识别出的 FP 记录，包含 `type` (EI/EO/EQ/ILF/EIF) 和 `complexity`。
6. **AuditLog (审计日志)**: 记录用户对 AI 生成结果的每一次人工干预。

## 6. 环境与部署 (DevOps)

- **容器化**: Docker & Docker Compose (包含 Web, API, Postgres, Redis)。
- **环境管理**: `.env` 分离模型 API Key 与数据库配置。
- **API 文档**: FastAPI 自动生成的 Swagger UI (`/docs`)。