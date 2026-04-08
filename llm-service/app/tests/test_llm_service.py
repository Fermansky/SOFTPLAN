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
from app.services.llm_client import get_backend_proxy_client  # noqa: E402


class _ResponseStub:
    def __init__(self, payload, status_code: int = 200, headers: dict[str, str] | None = None):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        return self._payload


class LlmServiceProxyTests(TestCase):
    def setUp(self) -> None:
        get_backend_proxy_client.cache_clear()
        self._patcher = patch.dict(
            os.environ,
            {
                "APP_ENV": "test",
                "APP_LOG_LEVEL": "INFO",
                "APP_LOG_FORMAT": "console",
                "APP_LOG_ACCESS_ENABLED": "true",
                "BACKEND_BASE_URL": "http://api:8000",
                "BACKEND_PROXY_TIMEOUT_SECONDS": "30",
            },
            clear=False,
        )
        self._patcher.start()
        self.client_cm = TestClient(create_app())
        self.client = self.client_cm.__enter__()

    def tearDown(self) -> None:
        self.client_cm.__exit__(None, None, None)
        get_backend_proxy_client.cache_clear()
        self._patcher.stop()

    def test_health_proxies_backend_health(self):
        with patch(
            "app.services.llm_client.httpx.get",
            return_value=_ResponseStub({"status": "ok"}, headers={"X-Request-ID": "req-health-1"}),
        ) as get_mock:
            response = self.client.get("/health", headers={"X-Request-ID": "req-health-1"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        self.assertEqual(response.headers["X-Request-ID"], "req-health-1")
        self.assertEqual(get_mock.call_args.kwargs["headers"], {"X-Request-ID": "req-health-1"})

    def test_chat_proxies_payload_and_headers_to_backend(self):
        with patch(
            "app.services.llm_client.httpx.post",
            return_value=_ResponseStub(
                {
                    "text": "hello world",
                    "model": "gpt-test",
                    "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
                    "request_id": "req-chat-1",
                },
                headers={"X-Request-ID": "req-chat-1"},
            ),
        ) as post_mock:
            response = self.client.post(
                "/internal/llm/chat",
                json={
                    "prompt": "Say hello",
                    "system_prompt": "You are helpful",
                    "request_id": "req-chat-1",
                    "input_parts": [{"type": "text", "text": "Say hello"}],
                },
                headers={"X-Request-ID": "req-chain-1", "X-Caller-Service": "backend"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["text"], "hello world")
        self.assertEqual(response.headers["X-Request-ID"], "req-chain-1")
        self.assertEqual(post_mock.call_args.kwargs["headers"], {"X-Request-ID": "req-chat-1", "X-Caller-Service": "backend"})
        self.assertEqual(
            post_mock.call_args.kwargs["json"],
            {
                "prompt": "Say hello",
                "system_prompt": "You are helpful",
                "request_id": "req-chat-1",
                "input_parts": [{"type": "text", "text": "Say hello"}],
            },
        )

    def test_chat_returns_backend_error_payload_unchanged(self):
        with patch(
            "app.services.llm_client.httpx.post",
            return_value=_ResponseStub({"detail": "chat persistence failed"}, status_code=500),
        ):
            response = self.client.post("/internal/llm/chat", json={"prompt": "hello"})

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"detail": "chat persistence failed"})

    def test_chat_returns_502_when_backend_is_unreachable(self):
        with patch(
            "app.services.llm_client.httpx.post",
            side_effect=httpx.ConnectError("backend down"),
        ):
            response = self.client.post("/internal/llm/chat", json={"prompt": "hello"})

        self.assertEqual(response.status_code, 502)
        self.assertIn("backend chat proxy failed", response.json()["detail"])

    def test_startup_does_not_require_database_or_upstream_env(self):
        with patch.dict(
            os.environ,
            {
                "BACKEND_BASE_URL": "http://api:8000",
                "DATABASE_URL": "",
                "LLM_API_BASE_URL": "",
                "LLM_API_KEY": "",
            },
            clear=False,
        ):
            get_backend_proxy_client.cache_clear()
            with TestClient(create_app()) as client:
                with patch(
                    "app.services.llm_client.httpx.get",
                    return_value=_ResponseStub({"status": "ok"}),
                ):
                    response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

