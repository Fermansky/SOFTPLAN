from unittest import TestCase
from unittest.mock import patch

import httpx

from backend.app.api.routers import converters
from backend.app.services.file_convert_service import FileConvertServiceClient


class _ResponseStub:
    def __init__(self, payload: dict[str, str], status_code: int = 200):
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
    def __init__(self, available: bool, error: str | None = None):
        self.available = available
        self.error = error

    def check_availability(self) -> tuple[bool, str | None]:
        return self.available, self.error


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


class ConvertersRouterTests(TestCase):
    def test_get_converter_availability_available_true(self):
        response = converters.get_converter_availability(client=_ClientStub(available=True))

        self.assertTrue(response.available)
        self.assertEqual(response.service, "file-convert-service")
        self.assertEqual(response.health_path, "/health")
        self.assertIsNone(response.error)

    def test_get_converter_availability_available_false(self):
        response = converters.get_converter_availability(
            client=_ClientStub(available=False, error="connection failed")
        )

        self.assertFalse(response.available)
        self.assertEqual(response.service, "file-convert-service")
        self.assertEqual(response.error, "connection failed")
