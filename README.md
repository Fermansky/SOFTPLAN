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

## Services

- Frontend: http://localhost:3000
- Backend: http://localhost:8000/health
- Backend Docs: http://localhost:8000/docs
- File Convert Service: http://localhost:8010/health
- MinIO API: http://localhost:10000
- MinIO Console: http://localhost:10001

## Logging

- Python 服务统一日志规范见 [docs/python-services-logging-guide.md](docs/python-services-logging-guide.md)
- 共享日志基础设施位于 `common/softplan_common/`，本地独立运行 Python 服务前可先在对应虚拟环境执行 `pip install -e .`
- 可通过 `APP_ENV`、`APP_LOG_LEVEL`、`APP_LOG_FORMAT`、`APP_LOG_ACCESS_ENABLED` 控制三套 Python 服务的日志行为
- 统一请求链路头为 `X-Request-ID`；`file-convert-service` 兼容一轮旧头 `X-Convert-Task-Id`

## Notes

- `backend` 现在是唯一的 LLM 宿主，负责上游模型调用和 `llm_chat_records` 审计落库。
- `GET /llm/availability` 检查的是 `backend` 内嵌 LLM 模块的本地配置。
- 如果 `POST /llm/chat` 返回 502，优先检查 `api` 容器日志；如果返回 500，说明上游成功但审计持久化失败。
- `POST /extracted-images/{image_id}/semantic-description` 继续基于已落库图片执行语义描述，但底层调用已经直接走 `backend` 内嵌 LLM 模块。
