import os
import sys
from types import SimpleNamespace
from unittest import TestCase

from fastapi.testclient import TestClient

CURRENT_DIR = os.path.dirname(__file__)
SERVICE_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, "..", ".."))
if SERVICE_ROOT not in sys.path:
    sys.path.insert(0, SERVICE_ROOT)

from app.api.dependencies import get_marker_pdf_to_markdown_converter, get_minio_storage  # noqa: E402
from app.main import create_app  # noqa: E402


class _StorageStub:
    def object_exists(self, storage_key: str) -> bool:
        return True

    def download_bytes(self, storage_key: str) -> bytes:
        return b"%PDF-1.7\n"


class _ConverterStub:
    def convert(self, payload: bytes):
        return SimpleNamespace(markdown="# ok", image_hashes={}, uploaded_images=[])


class FileConvertRequestIdMiddlewareTests(TestCase):
    def setUp(self) -> None:
        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        self.client.app.dependency_overrides.clear()

    def test_health_reuses_request_id_header(self):
        response = self.client.get("/health", headers={"X-Request-ID": "req-file-1"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Request-ID"], "req-file-1")

    def test_health_generates_request_id_header(self):
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertIn("X-Request-ID", response.headers)
        self.assertTrue(response.headers["X-Request-ID"])

    def test_legacy_convert_task_id_falls_back_to_request_id(self):
        self.client.app.dependency_overrides[get_minio_storage] = lambda: _StorageStub()
        self.client.app.dependency_overrides[get_marker_pdf_to_markdown_converter] = lambda: _ConverterStub()

        response = self.client.post(
            "/internal/converters/pdf-to-markdown",
            json={"storage_key": "a.pdf"},
            headers={"X-Convert-Task-Id": "legacy-task-1"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Request-ID"], "legacy-task-1")
