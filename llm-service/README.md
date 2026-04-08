# llm-service

Compatibility shell for legacy `/internal/llm/*` callers.

## Local Run

Create a `.env` file from the repo root example before running Docker Compose:

```powershell
Copy-Item .env.example .env
```

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Environment Variables

- `BACKEND_BASE_URL` (default: `http://api:8000`)
- `BACKEND_PROXY_TIMEOUT_SECONDS` (default: `30`)

## Internal APIs

- `GET /health`
  - Proxies to `backend GET /internal/llm/health`
- `POST /internal/llm/chat`
  - Proxies to `backend POST /internal/llm/chat`
  - Request and response schema remain unchanged for compatibility callers

## Notes

- `llm-service` no longer calls the upstream model API directly.
- `llm-service` no longer connects to PostgreSQL or owns `llm_chat_records` table creation.
- Real LLM execution and audit persistence are now hosted inside `backend`.
