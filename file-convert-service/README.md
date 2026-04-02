# file-convert-service

Independent FastAPI service scaffold for file conversion workloads.

## Local Run

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## MinIO Config

The service uses these environment variables:

- `MINIO_ENDPOINT` (default: `localhost:10000`)
- `MINIO_ACCESS_KEY` or `MINIO_ROOT_USER`
- `MINIO_SECRET_KEY` or `MINIO_ROOT_PASSWORD`
- `MINIO_BUCKET` (default: `softplan`)
- `MINIO_SECURE` (default: `false`)

## Marker Dependency

- `marker-pdf` is included in `requirements.txt`.

## Internal APIs

- `POST /internal/storage/objects` upload file to MinIO
- `GET /internal/storage/objects/{storage_key}` download file bytes from MinIO
- `POST /internal/converters/pdf-to-markdown` convert a PDF in MinIO to Markdown text
  - Request JSON: `{"storage_key": "<object-key>.pdf"}`
