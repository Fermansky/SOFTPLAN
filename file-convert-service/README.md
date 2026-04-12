# file-convert-service

Independent FastAPI service scaffold for file conversion workloads.

This service is now deployed independently from the default project `docker compose` stack. The main stack no longer starts it automatically.

## Local Run

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Docker Run

Build the image from the repo root:

```bash
docker build -f file-convert-service/Dockerfile -t softplan-file-convert-service .
```

Run it separately and point backend `FILE_CONVERT_SERVICE_BASE_URL` to this host:

```bash
docker run --rm -p 8000:8000 softplan-file-convert-service
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

## Internal Service Functions

- `app.services.upload_image_bytes(payload, content_type=...)`
  - Uploads images to MinIO with dedupe key format: `images/{sha256}{ext}`

## Internal APIs

- `POST /internal/storage/objects` upload file to MinIO (compatibility path)
- `GET /internal/storage/objects/{storage_key}` download file bytes from MinIO
- `POST /internal/converters/pdf-to-markdown` convert a PDF in MinIO to Markdown text and image hash mapping
  - Request JSON: `{"storage_key": "<object-key>.pdf"}`
  - Optional trace header: `X-Convert-Task-Id: <backend-task-id>`
  - Response JSON: `{"storage_key": "<object-key>.pdf", "markdown": "...", "image_hashes": {"<images_key>": "<sha256>"}, "uploaded_images": [{"source_key": "...", "file_hash": "...", "storage_bucket": "...", "storage_key": "...", "file_size": 123, "content_type": "image/png", "extension": ".png", "width": 100, "height": 200}]}`
  - Rendered images are serialized by key suffix when recognized (`jpg/jpeg/png/webp/gif/bmp/tiff`); unknown or failed format serialization falls back to PNG.
- `POST /internal/converters/pdf-to-markdown/file` convert an uploaded PDF to Markdown text and return extracted images inline
  - Request type: `multipart/form-data`
  - Form fields: `file=<uploaded .pdf>`, optional `model=marker`
  - Response JSON: `{"filename": "demo.pdf", "markdown": "...", "image_hashes": {"<images_key>": "<sha256>"}, "images": [{"source_key": "...", "file_hash": "...", "file_size": 123, "content_type": "image/png", "extension": ".png", "width": 100, "height": 200, "content_base64": "..."}]}`
  - This path does not read or write MinIO, so it can be used when `file-convert-service` is deployed on a different host and cannot directly access the same object storage as the caller.
