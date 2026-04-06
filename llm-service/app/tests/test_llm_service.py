import os
import sys
from unittest import TestCase
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

CURRENT_DIR = os.path.dirname(__file__)
SERVICE_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, "..", ".."))
if SERVICE_ROOT not in sys.path:
    sys.path.insert(0, SERVICE_ROOT)

from app.main import create_app  # noqa: E402
from app.services.llm_client import get_openai_compatible_llm_client  # noqa: E402


class _ResponseStub:
    def __init__(self, payload, status_code: int = 200, headers: dict[str, str] | None = None, text: str = ""):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "upstream error",
                request=httpx.Request("POST", "https://example.com/v1/chat/completions"),
                response=httpx.Response(self.status_code, text=self.text),
            )

    def json(self):
        return self._payload


class LlmServiceRouterTests(TestCase):
    def setUp(self) -> None:
        get_openai_compatible_llm_client.cache_clear()
        self._env = {
            "APP_ENV": "test",
            "APP_LOG_LEVEL": "INFO",
            "APP_LOG_FORMAT": "console",
            "APP_LOG_ACCESS_ENABLED": "true",
            "LLM_API_BASE_URL": "https://example.com/v1",
            "LLM_API_KEY": "test-key",
            "LLM_DEFAULT_MODEL": "gpt-test",
            "LLM_TIMEOUT_SECONDS": "30",
        }
        self._patcher = patch.dict(os.environ, self._env, clear=False)
        self._patcher.start()
        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        get_openai_compatible_llm_client.cache_clear()
        self._patcher.stop()

    def test_health_reuses_request_id_header(self):
        response = self.client.get("/health", headers={"X-Request-ID": "req-health-1"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Request-ID"], "req-health-1")

    def test_health_generates_request_id_header(self):
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertIn("X-Request-ID", response.headers)
        self.assertTrue(response.headers["X-Request-ID"])

    def test_chat_returns_text_model_usage_and_request_id(self):
        with patch(
            "app.services.llm_client.httpx.post",
            return_value=_ResponseStub(
                {
                    "id": "chatcmpl-1",
                    "model": "gpt-test",
                    "choices": [{"message": {"content": "hello world"}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
                },
                headers={"x-request-id": "req-header-1"},
            ),
        ):
            response = self.client.post(
                "/internal/llm/chat",
                json={"prompt": "Say hello", "request_id": "req-user-1"},
                headers={"X-Request-ID": "req-header-local"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["text"], "hello world")
        self.assertEqual(payload["model"], "gpt-test")
        self.assertEqual(
            payload["usage"],
            {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        )
        self.assertEqual(payload["request_id"], "req-user-1")
        self.assertEqual(response.headers["X-Request-ID"], "req-header-local")

    def test_chat_sends_multimodal_input_parts_to_upstream(self):
        with patch(
            "app.services.llm_client.httpx.post",
            return_value=_ResponseStub(
                {
                    "id": "chatcmpl-1",
                    "model": "gpt-test",
                    "choices": [{"message": {"content": "image summary"}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
                },
                headers={"x-request-id": "req-header-1"},
            ),
        ) as post_mock:
            response = self.client.post(
                "/internal/llm/chat",
                json={
                    "prompt": "Describe the image",
                    "request_id": "req-user-2",
                    "input_parts": [
                        {"type": "text", "text": "Describe the image"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
                    ],
                },
            )

        self.assertEqual(response.status_code, 200)
        upstream_payload = post_mock.call_args.kwargs["json"]
        self.assertEqual(upstream_payload["messages"][0]["content"][0], {"type": "text", "text": "Describe the image"})
        self.assertEqual(
            upstream_payload["messages"][0]["content"][1],
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        )

    def test_chat_forwards_request_id_header_to_upstream(self):
        with patch(
            "app.services.llm_client.httpx.post",
            return_value=_ResponseStub(
                {
                    "id": "chatcmpl-1",
                    "model": "gpt-test",
                    "choices": [{"message": {"content": "hello world"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
                },
                headers={"x-request-id": "req-header-1"},
            ),
        ) as post_mock:
            response = self.client.post(
                "/internal/llm/chat",
                json={"prompt": "hello"},
                headers={"X-Request-ID": "req-chain-1"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(post_mock.call_args.kwargs["headers"]["X-Request-ID"], "req-chain-1")

    def test_chat_returns_422_when_prompt_is_blank_after_strip(self):
        response = self.client.post("/internal/llm/chat", json={"prompt": "   "})

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"], "prompt is required")

    def test_chat_returns_502_on_timeout(self):
        with patch(
            "app.services.llm_client.httpx.post",
            side_effect=httpx.TimeoutException("timed out"),
        ):
            response = self.client.post("/internal/llm/chat", json={"prompt": "hello"})

        self.assertEqual(response.status_code, 502)
        self.assertIn("Upstream timeout", response.json()["detail"])

    def test_chat_returns_502_on_connection_error(self):
        with patch(
            "app.services.llm_client.httpx.post",
            side_effect=httpx.ConnectError("connection failed"),
        ):
            response = self.client.post("/internal/llm/chat", json={"prompt": "hello"})

        self.assertEqual(response.status_code, 502)
        self.assertIn("Upstream request error", response.json()["detail"])

    def test_chat_returns_502_on_http_error(self):
        with patch(
            "app.services.llm_client.httpx.post",
            return_value=_ResponseStub(
                {"error": {"message": "bad request"}},
                status_code=400,
                text='{"error":{"message":"bad request"}}',
            ),
        ):
            response = self.client.post("/internal/llm/chat", json={"prompt": "hello"})

        self.assertEqual(response.status_code, 502)
        self.assertIn("Upstream returned HTTP 400", response.json()["detail"])

    def test_chat_returns_502_when_api_key_is_missing(self):
        with patch.dict(os.environ, {"LLM_API_KEY": ""}, clear=False):
            get_openai_compatible_llm_client.cache_clear()
            with TestClient(create_app()) as client:
                with self.assertLogs("app.services.llm_client", level="WARNING") as captured_logs:
                    response = client.post(
                        "/internal/llm/chat",
                        json={"prompt": "hello", "request_id": "req-missing-key"},
                    )

        self.assertEqual(response.status_code, 502)
        self.assertIn("LLM_API_KEY is not configured", response.json()["detail"])
        self.assertTrue(
            any("LLM upstream request aborted" in message for message in captured_logs.output),
            captured_logs.output,
        )

    def test_startup_logs_warn_when_api_key_is_missing(self):
        with patch.dict(os.environ, {"LLM_API_KEY": ""}, clear=False):
            get_openai_compatible_llm_client.cache_clear()
            with self.assertLogs("app.services.llm_client", level="INFO") as captured_logs:
                with TestClient(create_app()):
                    pass

        self.assertTrue(
            any("LLM upstream configuration loaded" in message for message in captured_logs.output),
            captured_logs.output,
        )
        self.assertTrue(
            any("LLM_API_KEY is not configured" in message for message in captured_logs.output),
            captured_logs.output,
        )
