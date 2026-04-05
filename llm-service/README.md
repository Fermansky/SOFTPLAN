# llm-service

Independent FastAPI service scaffold for remote LLM requests.

## Local Run

Create a `.env` file from the repo root example before running Docker Compose:

```powershell
Copy-Item .env.example .env
```

Set `LLM_API_KEY` in `.env` to a real upstream key. Docker Compose does not read `.env.example` automatically.

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Environment Variables

- `LLM_API_BASE_URL` (default: `https://api.openai.com/v1`)
- `LLM_API_KEY` (required for upstream call)
- `LLM_DEFAULT_MODEL` (default: `gpt-4o-mini`)
- `LLM_TIMEOUT_SECONDS` (default: `30`)

## Internal APIs

- `GET /health`
  - Liveness only; this does not validate upstream credentials or model availability.
- `POST /internal/llm/chat`
  - Request JSON:
    - `prompt` (required)
    - `system_prompt` (optional)
    - `model` (optional)
    - `temperature` (optional)
    - `max_tokens` (optional)
    - `request_id` (optional)
  - Response JSON:
    - `text`
    - `model`
    - `usage` (`prompt_tokens`, `completion_tokens`, `total_tokens`)
    - `request_id`

## Logging

- Startup logs include the configured upstream base URL, default model, timeout, and whether an API key is present.
- Chat failure logs include request metadata, upstream HTTP status, timeout or request error details, and truncated upstream response bodies.
- Prompts and API keys are never logged.
