import base64
import hashlib
from unittest import TestCase
from unittest.mock import patch

import httpx

from backend.app.services.file_convert_service import FileConvertServiceClient


class _ResponseStub:
    def __init__(self, payload, *, status_code: int = 200, method: str = "POST", url: str = "http://svc/convert"):
        self._payload = payload
        self.status_code = status_code
        self._method = method
        self._url = url

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request(self._method, self._url)
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("upstream error", request=request, response=response)

    def json(self):
        return self._payload


class FileConvertServiceClientTests(TestCase):
    def test_check_availability_returns_false_when_base_url_is_missing(self):
        client = FileConvertServiceClient(base_url=None)

        available, error = client.check_availability()

        self.assertFalse(available)
        self.assertEqual(error, "file-convert-service base URL is not configured")

    def test_check_availability_returns_false_when_base_url_is_invalid(self):
        client = FileConvertServiceClient(base_url="not-a-url")

        available, error = client.check_availability()

        self.assertFalse(available)
        self.assertEqual(error, "file-convert-service base URL is invalid: 'not-a-url'")

    def test_convert_pdf_to_markdown_from_file_returns_error_when_base_url_is_missing(self):
        client = FileConvertServiceClient(base_url=None)

        result, error = client.convert_pdf_to_markdown_from_file(filename="demo.pdf", payload=b"%PDF-1.7\n")

        self.assertIsNone(result)
        self.assertEqual(error, "file-convert-service base URL is not configured")

    def test_convert_pdf_to_markdown_from_file_parses_inline_images(self):
        client = FileConvertServiceClient(base_url="http://file-convert-service:8000")
        inline_payload = b"png-payload"
        inline_hash = hashlib.sha256(inline_payload).hexdigest()

        with patch(
            "backend.app.services.file_convert_service.httpx.post",
            return_value=_ResponseStub(
                {
                    "filename": "demo.pdf",
                    "markdown": "# ok",
                    "image_hashes": {"diagram.png": inline_hash},
                    "images": [
                        {
                            "source_key": "diagram.png",
                            "file_hash": inline_hash,
                            "file_size": len(inline_payload),
                            "content_type": "image/png",
                            "extension": ".png",
                            "width": 100,
                            "height": 200,
                            "content_base64": base64.b64encode(inline_payload).decode("ascii"),
                        }
                    ],
                }
            ),
        ) as post_mock:
            result, error = client.convert_pdf_to_markdown_from_file(
                filename="documents/demo.pdf",
                payload=b"%PDF-1.7\n",
                task_id="task-1",
                model="marker",
            )

        self.assertIsNone(error)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.markdown, "# ok")
        self.assertEqual(result.image_hashes, {"diagram.png": inline_hash})
        self.assertEqual(len(result.inline_images), 1)
        self.assertEqual(result.inline_images[0].payload, inline_payload)
        self.assertEqual(result.inline_images[0].extension, ".png")
        self.assertEqual(result.inline_images[0].width, 100)
        self.assertEqual(result.inline_images[0].height, 200)

        self.assertEqual(post_mock.call_args.args[0], "http://file-convert-service:8000/internal/converters/pdf-to-markdown/file")
        self.assertEqual(post_mock.call_args.kwargs["data"], {"model": "marker"})
        self.assertEqual(post_mock.call_args.kwargs["headers"]["X-Request-ID"], "task-1")
        self.assertEqual(post_mock.call_args.kwargs["headers"]["X-Convert-Task-Id"], "task-1")
        self.assertEqual(post_mock.call_args.kwargs["files"]["file"][0], "demo.pdf")
        self.assertEqual(post_mock.call_args.kwargs["files"]["file"][1], b"%PDF-1.7\n")
        self.assertEqual(post_mock.call_args.kwargs["files"]["file"][2], "application/pdf")

    def test_convert_pdf_to_markdown_from_file_rejects_invalid_base64(self):
        client = FileConvertServiceClient(base_url="http://file-convert-service:8000")
        inline_payload = b"png-payload"
        inline_hash = hashlib.sha256(inline_payload).hexdigest()

        with patch(
            "backend.app.services.file_convert_service.httpx.post",
            return_value=_ResponseStub(
                {
                    "markdown": "# ok",
                    "image_hashes": {"diagram.png": inline_hash},
                    "images": [
                        {
                            "source_key": "diagram.png",
                            "file_hash": inline_hash,
                            "file_size": len(inline_payload),
                            "content_type": "image/png",
                            "content_base64": "***",
                        }
                    ],
                }
            ),
        ):
            result, error = client.convert_pdf_to_markdown_from_file(filename="demo.pdf", payload=b"%PDF-1.7\n")

        self.assertIsNone(result)
        self.assertIn("Unexpected convert response payload", error or "")

    def test_check_availability_wraps_request_error(self):
        client = FileConvertServiceClient(base_url="http://file-convert-service:8000")

        with patch(
            "backend.app.services.file_convert_service.httpx.get",
            side_effect=httpx.ConnectError("connection refused"),
        ):
            available, error = client.check_availability()

        self.assertFalse(available)
        self.assertIn("file-convert-service health check failed", error or "")

    def test_convert_pdf_to_markdown_from_file_rejects_file_hash_mismatch(self):
        client = FileConvertServiceClient(base_url="http://file-convert-service:8000")
        inline_payload = b"png-payload"

        with patch(
            "backend.app.services.file_convert_service.httpx.post",
            return_value=_ResponseStub(
                {
                    "markdown": "# ok",
                    "image_hashes": {"diagram.png": "a" * 64},
                    "images": [
                        {
                            "source_key": "diagram.png",
                            "file_hash": "b" * 64,
                            "file_size": len(inline_payload),
                            "content_type": "image/png",
                            "content_base64": base64.b64encode(inline_payload).decode("ascii"),
                        }
                    ],
                }
            ),
        ):
            result, error = client.convert_pdf_to_markdown_from_file(filename="demo.pdf", payload=b"%PDF-1.7\n")

        self.assertIsNone(result)
        self.assertIn("Unexpected convert response payload", error or "")

    def test_convert_pdf_to_markdown_from_file_rejects_image_hash_mapping_mismatch(self):
        client = FileConvertServiceClient(base_url="http://file-convert-service:8000")
        inline_payload = b"png-payload"
        inline_hash = hashlib.sha256(inline_payload).hexdigest()

        with patch(
            "backend.app.services.file_convert_service.httpx.post",
            return_value=_ResponseStub(
                {
                    "markdown": "# ok",
                    "image_hashes": {"diagram.png": "f" * 64},
                    "images": [
                        {
                            "source_key": "diagram.png",
                            "file_hash": inline_hash,
                            "file_size": len(inline_payload),
                            "content_type": "image/png",
                            "content_base64": base64.b64encode(inline_payload).decode("ascii"),
                        }
                    ],
                }
            ),
        ):
            result, error = client.convert_pdf_to_markdown_from_file(filename="demo.pdf", payload=b"%PDF-1.7\n")

        self.assertIsNone(result)
        self.assertIn("Unexpected convert response payload", error or "")
