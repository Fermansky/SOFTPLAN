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

## Notes

- `GET /health` on `llm-service` is only a liveness check for the service itself. It does not verify that the upstream LLM base URL, API key, or model are valid.
- If `POST /internal/llm/chat` returns 502, check the `llm-service` container logs first. The service now logs missing-key, upstream HTTP, timeout, and payload-shape failures with request metadata but without logging prompts or secrets.
- `POST /extracted-images/{image_id}/semantic-description` generates a Chinese semantic description from an existing extracted image in MinIO.
- Use `EXTRACTED_IMAGE_SEMANTIC_PROMPT_PATH` to point to the versioned prompt file, and optionally set `EXTRACTED_IMAGE_SEMANTIC_MODEL` to override the default model for image semantic extraction.
