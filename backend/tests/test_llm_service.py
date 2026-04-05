from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import httpx
from fastapi import HTTPException
from minio.error import S3Error

from backend.app.api.routers import llm as llm_router
from backend.app.services import LlmImageUrlInputPart, LlmServiceClient, LlmTextInputPart


class _ResponseStub:
    def __init__(self, payload: dict[str, object], status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "error",
                request=httpx.Request("POST", "http://llm-service:8000/internal/llm/chat"),
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
        text: str = "",
        model: str = "gpt-test",
        usage: dict[str, int] | None = None,
        request_id: str | None = None,
        chat_error: str | None = None,
    ):
        self.available = available
        self.availability_error = availability_error
        self.text = text
        self.model = model
        self.usage = usage or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self.request_id = request_id
        self.chat_error = chat_error
        self.last_input_parts = None

    def check_availability(self) -> tuple[bool, str | None]:
        return self.available, self.availability_error

    def chat(
        self,
        *,
        prompt: str,
        system_prompt: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        request_id: str | None = None,
        input_parts=None,
    ):
        self.last_input_parts = input_parts
        if self.chat_error is not None:
            return None, self.chat_error
        usage_obj = llm_router.LlmUsageRead(**self.usage)
        result = llm_router.LlmChatRead(
            text=self.text,
            model=model or self.model,
            usage=usage_obj,
            request_id=request_id or self.request_id,
        )
        return result, None


class _StorageStub:
    def __init__(self, *, payload: bytes = b"", error: S3Error | None = None):
        self.payload = payload
        self.error = error
        self.calls: list[dict[str, str | None]] = []

    def download_bytes(self, storage_key: str, *, bucket: str | None = None) -> bytes:
        self.calls.append({"storage_key": storage_key, "bucket": bucket})
        if self.error is not None:
            raise self.error
        return self.payload


class _MinioError(S3Error):
    def __init__(self, code: str):
        self._code = code

    @property
    def code(self) -> str:
        return self._code


class LlmServiceClientTests(TestCase):
    def test_check_availability_returns_true_when_health_ok(self):
        client = LlmServiceClient(base_url="http://llm-service:8000", timeout_seconds=3.0)

        with patch("backend.app.services.llm_service.httpx.get", return_value=_ResponseStub({"status": "ok"})):
            available, error = client.check_availability()

        self.assertTrue(available)
        self.assertIsNone(error)

    def test_chat_parses_payload(self):
        client = LlmServiceClient(base_url="http://llm-service:8000", timeout_seconds=3.0)

        with patch(
            "backend.app.services.llm_service.httpx.post",
            return_value=_ResponseStub(
                {
                    "text": "hello",
                    "model": "gpt-test",
                    "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
                    "request_id": "req-1",
                }
            ),
        ):
            result, error = client.chat(prompt="hello")

        self.assertIsNone(error)
        self.assertIsNotNone(result)
        self.assertEqual(result.text, "hello")
        self.assertEqual(result.model, "gpt-test")
        self.assertEqual(result.usage.prompt_tokens, 1)
        self.assertEqual(result.usage.completion_tokens, 2)
        self.assertEqual(result.usage.total_tokens, 3)
        self.assertEqual(result.request_id, "req-1")

    def test_chat_serializes_input_parts(self):
        client = LlmServiceClient(base_url="http://llm-service:8000", timeout_seconds=3.0)

        with patch(
            "backend.app.services.llm_service.httpx.post",
            return_value=_ResponseStub(
                {
                    "text": "hello",
                    "model": "gpt-test",
                    "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
                    "request_id": "req-1",
                }
            ),
        ) as post_mock:
            result, error = client.chat(
                prompt="describe this image",
                input_parts=[
                    LlmTextInputPart(text="describe this image"),
                    LlmImageUrlInputPart(url="data:image/png;base64,AAAA"),
                ],
            )

        self.assertIsNone(error)
        self.assertIsNotNone(result)
        payload = post_mock.call_args.kwargs["json"]
        self.assertEqual(payload["input_parts"][0], {"type": "text", "text": "describe this image"})
        self.assertEqual(
            payload["input_parts"][1],
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        )

    def test_chat_returns_error_on_http_error(self):
        client = LlmServiceClient(base_url="http://llm-service:8000", timeout_seconds=3.0)

        with patch(
            "backend.app.services.llm_service.httpx.post",
            side_effect=httpx.ConnectError("down"),
        ):
            result, error = client.chat(prompt="hello")

        self.assertIsNone(result)
        self.assertIn("down", error or "")


class LlmRouterTests(TestCase):
    def test_get_llm_availability_returns_available_true(self):
        response = llm_router.get_llm_availability(client=_ClientStub(available=True))

        self.assertTrue(response.available)
        self.assertEqual(response.service, "llm-service")
        self.assertEqual(response.health_path, "/health")
        self.assertIsNone(response.error)

    def test_get_llm_availability_returns_available_false(self):
        response = llm_router.get_llm_availability(
            client=_ClientStub(available=False, availability_error="connection failed")
        )

        self.assertFalse(response.available)
        self.assertEqual(response.service, "llm-service")
        self.assertEqual(response.error, "connection failed")

    def test_chat_returns_result(self):
        response = llm_router.chat(
            payload=llm_router.LlmChatRequest(prompt="hello", request_id="req-1"),
            client=_ClientStub(
                text="world",
                usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
                request_id="req-1",
            ),
        )

        self.assertEqual(response.text, "world")
        self.assertEqual(response.model, "gpt-test")
        self.assertEqual(response.usage.prompt_tokens, 10)
        self.assertEqual(response.usage.completion_tokens, 20)
        self.assertEqual(response.usage.total_tokens, 30)
        self.assertEqual(response.request_id, "req-1")

    def test_chat_builds_input_parts_from_extracted_images(self):
        client = _ClientStub(text="world")
        storage = _StorageStub(payload=b"png-bytes")
        extracted_image = SimpleNamespace(
            id=1,
            content_type="image/png",
            storage_bucket="softplan",
            storage_key="images/hash-1.png",
        )

        with patch.object(llm_router, "get_extracted_image_or_404", return_value=extracted_image):
            response = llm_router.chat(
                payload=llm_router.LlmChatRequest(prompt="hello", extracted_image_ids=[1]),
                client=client,
                session=object(),
                storage=storage,
            )

        self.assertEqual(response.text, "world")
        self.assertIsNotNone(client.last_input_parts)
        self.assertEqual(len(client.last_input_parts), 2)
        self.assertIsInstance(client.last_input_parts[0], LlmTextInputPart)
        self.assertEqual(client.last_input_parts[0].text, "hello")
        self.assertIsInstance(client.last_input_parts[1], LlmImageUrlInputPart)
        self.assertTrue(client.last_input_parts[1].url.startswith("data:image/png;base64,"))
        self.assertEqual(storage.calls, [{"storage_key": "images/hash-1.png", "bucket": "softplan"}])

    def test_chat_rejects_blank_prompt(self):
        with self.assertRaises(HTTPException) as ctx:
            llm_router.chat(
                payload=llm_router.LlmChatRequest(prompt="   "),
                client=_ClientStub(text="world"),
            )

        self.assertEqual(ctx.exception.status_code, 422)

    def test_chat_rejects_too_many_extracted_images(self):
        with self.assertRaises(HTTPException) as ctx:
            llm_router.chat(
                payload=llm_router.LlmChatRequest(prompt="hello", extracted_image_ids=[1, 2, 3, 4, 5]),
                client=_ClientStub(text="world"),
                session=object(),
                storage=_StorageStub(payload=b"png-bytes"),
            )

        self.assertEqual(ctx.exception.status_code, 422)

    def test_chat_rejects_non_image_extracted_resource(self):
        extracted_image = SimpleNamespace(
            id=1,
            content_type="text/plain",
            storage_bucket="softplan",
            storage_key="images/hash-1.txt",
        )

        with patch.object(llm_router, "get_extracted_image_or_404", return_value=extracted_image):
            with self.assertRaises(HTTPException) as ctx:
                llm_router.chat(
                    payload=llm_router.LlmChatRequest(prompt="hello", extracted_image_ids=[1]),
                    client=_ClientStub(text="world"),
                    session=object(),
                    storage=_StorageStub(payload=b"not-an-image"),
                )

        self.assertEqual(ctx.exception.status_code, 422)

    def test_chat_returns_502_on_extracted_image_download_failure(self):
        extracted_image = SimpleNamespace(
            id=1,
            content_type="image/png",
            storage_bucket="softplan",
            storage_key="images/hash-1.png",
        )

        with patch.object(llm_router, "get_extracted_image_or_404", return_value=extracted_image):
            with self.assertRaises(HTTPException) as ctx:
                llm_router.chat(
                    payload=llm_router.LlmChatRequest(prompt="hello", extracted_image_ids=[1]),
                    client=_ClientStub(text="world"),
                    session=object(),
                    storage=_StorageStub(error=_MinioError("NoSuchKey")),
                )

        self.assertEqual(ctx.exception.status_code, 502)
        self.assertIn("Extracted image storage download failed", ctx.exception.detail)

    def test_chat_returns_502_on_service_error(self):
        with self.assertRaises(HTTPException) as ctx:
            llm_router.chat(
                payload=llm_router.LlmChatRequest(prompt="hello"),
                client=_ClientStub(chat_error="request failed"),
            )

        self.assertEqual(ctx.exception.status_code, 502)

