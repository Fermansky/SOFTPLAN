# Softplan Scaffold

## Start

Create a local `.env` file before starting Docker:

```powershell
Copy-Item .env.example .env
```

Set `LLM_API_KEY` in `.env` to a real upstream key. Docker Compose reads `.env` automatically; it does not load `.env.example` by itself.

Then start the stack:

```bash
docker compose up --build
```

`docker compose` no longer starts `file-convert-service` by default. If you want document parsing to work, run that service separately and set `FILE_CONVERT_SERVICE_BASE_URL` in `.env`.

The repository now only guarantees first-time initialization on a fresh database. Startup no longer includes in-app schema migration logic for older database layouts.

## Services

- Frontend: http://localhost:3000
- Backend: http://localhost:8000/health
- Backend Docs: http://localhost:8000/docs
- MinIO API: http://localhost:10000
- MinIO Console: http://localhost:10001

Optional external service:

- File Convert Service: configure `FILE_CONVERT_SERVICE_BASE_URL` to your independently deployed instance, for example `http://file-convert-host:8000`

## Logging

- Python 服务统一日志规范见 [docs/python-services-logging-guide.md](docs/python-services-logging-guide.md)
- `backend` 在仓库内自管日志实现，可通过 `APP_ENV`、`APP_LOG_LEVEL`、`APP_LOG_FORMAT`、`APP_LOG_ACCESS_ENABLED` 控制日志行为
- 统一请求链路头为 `X-Request-ID`；对外部 `file-convert-service` 的兼容旧头 `X-Convert-Task-Id` 仅保留在 `backend` 的 HTTP 客户端透传逻辑中

## Notes

- `backend` 现在是唯一的 LLM 宿主，负责上游模型调用和 `llm_chat_records` 审计落库。
- 数据库初始化仅覆盖当前 schema 的首次建表；若要恢复旧快照，请按当前 schema 重建或手工处理升级。
- 未配置 `FILE_CONVERT_SERVICE_BASE_URL` 时，API 仍可正常启动，但 `/document-parsing/*` 相关能力会在执行阶段失败，`/document-parsing/availability` 会返回不可用。
- `GET /llm/availability` 检查的是 `backend` 内嵌 LLM 模块的本地配置。
- 如果 `POST /llm/chat` 返回 502，优先检查 `api` 容器日志；如果返回 500，说明上游成功但审计持久化失败。
- `POST /extracted-images/{image_id}/semantic-description` 继续基于已落库图片执行语义描述，但底层调用已经直接走 `backend` 内嵌 LLM 模块。
