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
- LLM Service: http://localhost:8020/health
- MinIO API: http://localhost:10000
- MinIO Console: http://localhost:10001

## Logging

- Python 服务统一日志规范见 [docs/python-services-logging-guide.md](docs/python-services-logging-guide.md)
- 共享日志基础设施位于 `common/softplan_common/`，本地独立运行 Python 服务前可先在对应虚拟环境执行 `pip install -e .`
- 可通过 `APP_ENV`、`APP_LOG_LEVEL`、`APP_LOG_FORMAT`、`APP_LOG_ACCESS_ENABLED` 控制三套 Python 服务的日志行为
- 统一请求链路头为 `X-Request-ID`；`file-convert-service` 兼容一轮旧头 `X-Convert-Task-Id`

## Notes

- `GET /health` on `llm-service` is only a liveness check for the service itself. It does not verify that the upstream LLM base URL, API key, or model are valid.
- If `POST /internal/llm/chat` returns 502, check the `llm-service` container logs first. The service now logs missing-key, upstream HTTP, timeout, and payload-shape failures with request metadata but without logging prompts or secrets.
- `POST /extracted-images/{image_id}/semantic-description` generates a Chinese semantic description from an existing extracted image in MinIO.
- Use `EXTRACTED_IMAGE_SEMANTIC_PROMPT_PATH` to point to the versioned prompt file, and optionally set `EXTRACTED_IMAGE_SEMANTIC_MODEL` to override the default model for image semantic extraction.


