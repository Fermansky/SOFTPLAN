import asyncio
import io
import os
import sys
from types import SimpleNamespace
from unittest import TestCase

from fastapi import HTTPException
from fastapi import UploadFile
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
        self.render_calls = []

    def convert(self, pdf_payload: bytes):
        self.calls.append(pdf_payload)
        return SimpleNamespace(markdown="# ok", image_hashes={}, uploaded_images=[])

    def render(self, pdf_payload: bytes):
        self.render_calls.append(pdf_payload)
        return SimpleNamespace(
            markdown="# ok",
            image_hashes={"diagram.png": "abc123"},
            images=[
                SimpleNamespace(
                    source_key="diagram.png",
                    file_hash="abc123",
                    payload=b"png-payload",
                    file_size=11,
                    content_type="image/png",
                    extension=".png",
                    width=100,
                    height=200,
                )
            ],
        )


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

    def _build_upload_file(self, *, filename: str | None, payload: bytes) -> UploadFile:
        return UploadFile(filename=filename, file=io.BytesIO(payload))

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

    def test_convert_pdf_to_markdown_from_file_returns_inline_images(self):
        converter = _ConverterStub()

        response = asyncio.run(
            converters.convert_pdf_to_markdown_from_file(
                request=self._build_request(),
                file=self._build_upload_file(filename="demo.pdf", payload=b"%PDF-1.7\n"),
                model="marker",
                converter=converter,
            )
        )

        self.assertEqual(response.filename, "demo.pdf")
        self.assertEqual(response.markdown, "# ok")
        self.assertEqual(response.image_hashes, {"diagram.png": "abc123"})
        self.assertEqual(len(response.images), 1)
        self.assertEqual(response.images[0].source_key, "diagram.png")
        self.assertEqual(response.images[0].file_hash, "abc123")
        self.assertEqual(response.images[0].content_type, "image/png")
        self.assertEqual(response.images[0].extension, ".png")
        self.assertEqual(response.images[0].width, 100)
        self.assertEqual(response.images[0].height, 200)
        self.assertEqual(response.images[0].content_base64, "cG5nLXBheWxvYWQ=")
        self.assertEqual(converter.render_calls, [b"%PDF-1.7\n"])

    def test_convert_pdf_to_markdown_from_file_rejects_empty_upload(self):
        converter = _ConverterStub()

        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(
                converters.convert_pdf_to_markdown_from_file(
                    request=self._build_request(),
                    file=self._build_upload_file(filename="demo.pdf", payload=b""),
                    model="marker",
                    converter=converter,
                )
            )

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.detail, "Uploaded file is empty")

    def test_convert_pdf_to_markdown_from_file_rejects_non_pdf_filename(self):
        converter = _ConverterStub()

        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(
                converters.convert_pdf_to_markdown_from_file(
                    request=self._build_request(),
                    file=self._build_upload_file(filename="demo.txt", payload=b"not-empty"),
                    model="marker",
                    converter=converter,
                )
            )

        self.assertEqual(ctx.exception.status_code, 422)
        self.assertEqual(ctx.exception.detail, "Only PDF file is supported")

    def test_convert_pdf_to_markdown_from_file_rejects_unsupported_model(self):
        converter = _ConverterStub()

        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(
                converters.convert_pdf_to_markdown_from_file(
                    request=self._build_request(),
                    file=self._build_upload_file(filename="demo.pdf", payload=b"%PDF-1.7\n"),
                    model="other",
                    converter=converter,
                )
            )

        self.assertEqual(ctx.exception.status_code, 422)
        self.assertIn("Only 'marker' is supported", ctx.exception.detail)
