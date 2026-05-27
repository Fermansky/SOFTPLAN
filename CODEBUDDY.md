# Softplan 代码库说明

本文件供 AI 代码助手（CodeBuddy）读取，帮助 AI 理解项目背景并遵守维护规则。

---

## 快速定位

| 想了解什么 | 读哪个文件 |
|-----------|-----------|
| 产品是做什么的、不做什么 | `docs/background/product.md` |
| 为什么选这个技术，架构怎么演进的 | `docs/background/architecture.md` |
| 数据库表结构和字段含义 | `docs/current/data-model.md` |
| 各 service 的职责和函数签名 | `docs/current/services.md` |
| 各 agent 的功能和 Prompt 策略 | `docs/current/agents.md` |
| API 端点列表 | `docs/current/api.md` |
| 环境变量、服务端口、Worker 说明 | `docs/current/infra.md` |
| 日志怎么写 | `docs/standards/logging.md` |
| 注释怎么写 | `docs/standards/commenting.md` |
| 怎么新增一个 Agent | `docs/standards/how-to-add-agent.md` |

---

## 文档维护规则（AI 必须遵守）

每次修改代码后，检查下表，**更新对应文档**。只更新被影响的部分，不重写整个文档。

| 触发条件 | 必须更新的文档 |
|---------|-------------|
| 新增或修改 `backend/app/models/*.py` | `docs/current/data-model.md` 对应表的描述 |
| 新增或修改 `backend/app/services/*.py` | `docs/current/services.md` 对应 service 条目 |
| 新增或修改 `backend/app/agents/` 下任何文件 | `docs/current/agents.md` 对应 agent 条目 |
| 新增或修改 `backend/app/api/routers/*.py` | `docs/current/api.md` 对应端点 |
| 修改 `docker-compose.yml` 或 `.env.example` | `docs/current/infra.md` |
| 做出重大技术决策（换技术栈、改核心架构） | `docs/background/architecture.md` 追加 ADR |

### 文档写法约定

**data-model.md**：只写代码读不到的信息（字段含义、约束理由、枚举语义、复用规则）。不列 id/created_at/updated_at 等通用字段。

**services.md**：描述职责边界（做什么、不做什么）和关键函数的输入输出语义，注明副作用（写 DB、调用外部服务）。

**agents.md**：描述触发时机、输入输出、Prompt 策略（不复制 Prompt 原文，写设计意图）。

**architecture.md（ADR）**：用固定格式追加条目，包含背景、决策、放弃的方案、后果。

---

## 项目概况

- **类型**：全栈 Web 应用（前端 Next.js 14 + 后端 FastAPI + PostgreSQL + MinIO）
- **后端入口**：`backend/app/main.py`
- **数据库初始化**：启动时 `SQLModel.metadata.create_all(engine)`，无 Alembic
- **LLM 调用**：通过 `LlmService`（OpenAI 兼容协议），配置存在 `llm_configs` 表，审计写 `llm_chat_records`
- **Worker**：与 API 同进程，FastAPI startup 事件启动，处理版面分析和图片语义任务
- **PDF 解析**：外包给独立的 `file-convert-service`（HTTP 调用）
