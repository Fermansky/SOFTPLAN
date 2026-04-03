from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

import httpx
from fastapi import HTTPException

from backend.app.api.routers import converters
from backend.app.models import Document, FileRecord
from backend.app.services.file_convert_service import FileConvertServiceClient, PdfToMarkdownResult


class _ResponseStub:
    def __init__(self, payload: dict[str, object], status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "error",
                request=httpx.Request("GET", "http://file-convert-service:8000/health"),
                response=httpx.Response(self.status_code),
            )

    def json(self):
        return self._payload


class _ClientStub:
    def __init__(
        self,
        *,
        available: bool = True,
        availability_error: str | None = None,
        markdown: str | None = None,
        image_hashes: dict[str, str] | None = None,
        convert_error: str | None = None,
    ):
        self.available = available
        self.availability_error = availability_error
        self.markdown = markdown
        self.image_hashes = image_hashes or {}
        self.convert_error = convert_error

    def check_availability(self) -> tuple[bool, str | None]:
        return self.available, self.availability_error

    def convert_pdf_to_markdown(self, *, storage_key: str) -> tuple[PdfToMarkdownResult | None, str | None]:
        if self.convert_error is not None:
            return None, self.convert_error
        return PdfToMarkdownResult(markdown=self.markdown or "", image_hashes=self.image_hashes), None


class FileConvertServiceClientTests(TestCase):
    def test_check_availability_returns_true_when_health_ok(self):
        client = FileConvertServiceClient(base_url="http://file-convert-service:8000", timeout_seconds=3.0)

        with patch("backend.app.services.file_convert_service.httpx.get", return_value=_ResponseStub({"status": "ok"})):
            available, error = client.check_availability()

        self.assertTrue(available)
        self.assertIsNone(error)

    def test_check_availability_returns_false_on_timeout(self):
        client = FileConvertServiceClient(base_url="http://file-convert-service:8000", timeout_seconds=3.0)

        with patch(
            "backend.app.services.file_convert_service.httpx.get",
            side_effect=httpx.TimeoutException("timed out"),
        ):
            available, error = client.check_availability()

        self.assertFalse(available)
        self.assertIn("timed out", error or "")

    def test_convert_pdf_to_markdown_returns_markdown_and_image_hashes_on_success(self):
        client = FileConvertServiceClient(base_url="http://file-convert-service:8000", convert_timeout_seconds=60)

        with patch(
            "backend.app.services.file_convert_service.httpx.post",
            return_value=_ResponseStub(
                {
                    "storage_key": "a.pdf",
                    "markdown": "# hello",
                    "image_hashes": {"0": "abc123"},
                }
            ),
        ):
            result, error = client.convert_pdf_to_markdown(storage_key="a.pdf")

        self.assertIsNotNone(result)
        self.assertEqual(result.markdown, "# hello")
        self.assertEqual(result.image_hashes, {"0": "abc123"})
        self.assertIsNone(error)

    def test_convert_pdf_to_markdown_defaults_image_hashes_to_empty_dict(self):
        client = FileConvertServiceClient(base_url="http://file-convert-service:8000", convert_timeout_seconds=60)

        with patch(
            "backend.app.services.file_convert_service.httpx.post",
            return_value=_ResponseStub({"storage_key": "a.pdf", "markdown": "# hello"}),
        ):
            result, error = client.convert_pdf_to_markdown(storage_key="a.pdf")

        self.assertIsNotNone(result)
        self.assertEqual(result.image_hashes, {})
        self.assertIsNone(error)

    def test_convert_pdf_to_markdown_returns_error_on_http_error(self):
        client = FileConvertServiceClient(base_url="http://file-convert-service:8000", convert_timeout_seconds=60)

        with patch(
            "backend.app.services.file_convert_service.httpx.post",
            side_effect=httpx.ConnectError("down"),
        ):
            result, error = client.convert_pdf_to_markdown(storage_key="a.pdf")

        self.assertIsNone(result)
        self.assertIn("down", error or "")


class ConvertersRouterTests(TestCase):
    def test_get_converter_availability_available_true(self):
        response = converters.get_converter_availability(client=_ClientStub(available=True))

        self.assertTrue(response.available)
        self.assertEqual(response.service, "file-convert-service")
        self.assertEqual(response.health_path, "/health")
        self.assertIsNone(response.error)

    def test_get_converter_availability_available_false(self):
        response = converters.get_converter_availability(
            client=_ClientStub(available=False, availability_error="connection failed")
        )

        self.assertFalse(response.available)
        self.assertEqual(response.service, "file-convert-service")
        self.assertEqual(response.error, "connection failed")

    def test_convert_pdf_to_markdown_success(self):
        document = Document(project_id=uuid4(), file_id=uuid4(), name="PRD")
        file_record = FileRecord(
            file_hash="hash",
            storage_bucket="softplan",
            storage_key="doc/a.pdf",
            file_size=10,
            content_type="application/pdf",
            extension=".pdf",
        )

        with patch.object(converters, "get_active_document_or_404", return_value=document):
            with patch.object(converters, "get_file_or_404", return_value=file_record):
                response = converters.convert_pdf_to_markdown(
                    payload=converters.PdfToMarkdownConvertRequest(document_id=document.id),
                    session=object(),
                    client=_ClientStub(markdown="# content", image_hashes={"img-1": "hash-1"}),
                )

        self.assertEqual(response.document_id, document.id)
        self.assertEqual(response.storage_key, "doc/a.pdf")
        self.assertEqual(response.markdown, "# content")
        self.assertEqual(response.image_hashes, {"img-1": "hash-1"})

    def test_convert_pdf_to_markdown_rejects_non_pdf(self):
        document = Document(project_id=uuid4(), file_id=uuid4(), name="PRD")
        file_record = FileRecord(
            file_hash="hash",
            storage_bucket="softplan",
            storage_key="doc/a.txt",
            file_size=10,
            content_type="text/plain",
            extension=".txt",
        )

        with patch.object(converters, "get_active_document_or_404", return_value=document):
            with patch.object(converters, "get_file_or_404", return_value=file_record):
                with self.assertRaises(HTTPException) as ctx:
                    converters.convert_pdf_to_markdown(
                        payload=converters.PdfToMarkdownConvertRequest(document_id=document.id),
                        session=object(),
                        client=_ClientStub(markdown="# content"),
                    )

        self.assertEqual(ctx.exception.status_code, 422)

    def test_convert_pdf_to_markdown_raises_502_on_downstream_error(self):
        document = Document(project_id=uuid4(), file_id=uuid4(), name="PRD")
        file_record = FileRecord(
            file_hash="hash",
            storage_bucket="softplan",
            storage_key="doc/a.pdf",
            file_size=10,
            content_type="application/pdf",
            extension=".pdf",
        )

        with patch.object(converters, "get_active_document_or_404", return_value=document):
            with patch.object(converters, "get_file_or_404", return_value=file_record):
                with self.assertRaises(HTTPException) as ctx:
                    converters.convert_pdf_to_markdown(
                        payload=converters.PdfToMarkdownConvertRequest(document_id=document.id),
                        session=object(),
                        client=_ClientStub(convert_error="convert failed"),
                    )

        self.assertEqual(ctx.exception.status_code, 502)
