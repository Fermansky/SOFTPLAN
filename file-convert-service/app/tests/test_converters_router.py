import os
import sys
from types import SimpleNamespace
from unittest import TestCase

from fastapi import HTTPException
from starlette.requests import Request

CURRENT_DIR = os.path.dirname(__file__)
SERVICE_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, "..", ".."))
if SERVICE_ROOT not in sys.path:
    sys.path.insert(0, SERVICE_ROOT)

from app.api.routers import converters  # noqa: E402


class _StorageStub:
    def __init__(self, *, exists: bool = True, payload: bytes = b"%PDF-1.7\n"):
        self.exists = exists
        self.payload = payload

    def object_exists(self, storage_key: str) -> bool:
        return self.exists

    def download_bytes(self, storage_key: str) -> bytes:
        return self.payload


class _ConverterStub:
    def __init__(self):
        self.calls = []

    def convert(self, pdf_payload: bytes):
        self.calls.append(pdf_payload)
        return SimpleNamespace(markdown="# ok", image_hashes={}, uploaded_images=[])


class ConvertersRouterModelTests(TestCase):
    def _build_request(self) -> Request:
        return Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/internal/converters/pdf-to-markdown",
                "headers": [],
            }
        )

    def test_resolve_pdf_model_defaults_to_marker(self):
        self.assertEqual(converters._resolve_pdf_model(None), "marker")
        self.assertEqual(converters._resolve_pdf_model("   "), "marker")

    def test_resolve_pdf_model_accepts_marker(self):
        self.assertEqual(converters._resolve_pdf_model("marker"), "marker")
        self.assertEqual(converters._resolve_pdf_model("Marker"), "marker")

    def test_resolve_pdf_model_rejects_other_values(self):
        with self.assertRaises(HTTPException) as ctx:
            converters._resolve_pdf_model("other")

        self.assertEqual(ctx.exception.status_code, 422)
        self.assertIn("Only 'marker' is supported", ctx.exception.detail)

    def test_convert_pdf_to_markdown_from_storage_accepts_explicit_marker(self):
        storage = _StorageStub()
        converter = _ConverterStub()

        response = converters.convert_pdf_to_markdown_from_storage(
            payload=converters.ConvertPdfToMarkdownRequest(storage_key="documents/demo.pdf", model="marker"),
            request=self._build_request(),
            storage=storage,
            converter=converter,
        )

        self.assertEqual(response.storage_key, "documents/demo.pdf")
        self.assertEqual(response.markdown, "# ok")
        self.assertEqual(converter.calls, [b"%PDF-1.7\n"])
