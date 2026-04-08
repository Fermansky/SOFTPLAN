import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

import httpx
from fastapi import HTTPException
from fastapi.testclient import TestClient
from minio.error import S3Error
from sqlalchemy import inspect
from sqlmodel import Session, create_engine, select

import backend.app.database as database_module
import backend.app.main as main_module
import backend.app.services.llm_service as llm_service_module
from backend.app.api.routers import llm as llm_router
from backend.app.models import LlmChatRecord, LlmChatRecordStatus
from backend.app.services import LlmChatPersistenceError, LlmImageUrlInputPart, LlmTextInputPart
from backend.app.services.extracted_image_semantic_service import load_extracted_image_semantic_prompt
from backend.app.services.llm_service import get_llm_service_client


class _ResponseStub:
    def __init__(self, payload, status_code: int = 200, headers: dict[str, str] | None = None, text: str = ""):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://example.com/v1/chat/completions")
            response = httpx.Response(self.status_code, text=self.text, headers=self.headers, request=request)
            raise httpx.HTTPStatusError("upstream error", request=request, response=response)

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
        persistence_error: str | None = None,
    ):
        self.available = available
        self.availability_error = availability_error
        self.text = text
        self.model = model
        self.usage = usage or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self.request_id = request_id
        self.chat_error = chat_error
        self.persistence_error = persistence_error
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
        caller_service: str | None = None,
    ):
        self.last_input_parts = input_parts
        if self.persistence_error is not None:
            raise LlmChatPersistenceError(self.persistence_error)
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


class BackendLlmIntegrationTests(TestCase):
    def setUp(self) -> None:
        get_llm_service_client.cache_clear()
        load_extracted_image_semantic_prompt.cache_clear()
        self._tmpdir = Path("backend/tests/runtime_cases") / f"case-{uuid4().hex}"
        self._tmpdir.mkdir(parents=True, exist_ok=True)
        db_path = self._tmpdir / "backend-llm-test.db"
        self._engine = create_engine(
            f"sqlite:///{db_path.as_posix()}",
            connect_args={"check_same_thread": False},
        )
        self._original_db_engine = database_module.engine
        self._original_service_engine = llm_service_module.engine
        database_module.engine = self._engine
        llm_service_module.engine = self._engine
        self._env_patcher = patch.dict(
            "os.environ",
            {
                "APP_ENV": "test",
                "APP_LOG_LEVEL": "INFO",
                "APP_LOG_FORMAT": "console",
                "APP_LOG_ACCESS_ENABLED": "true",
                "LLM_API_BASE_URL": "https://example.com/v1",
                "LLM_API_KEY": "test-key",
                "LLM_DEFAULT_MODEL": "gpt-test",
                "LLM_TIMEOUT_SECONDS": "30",
                "LAYOUT_ANALYSIS_TASK_WORKER_ENABLED": "false",
                "EXTRACTED_IMAGE_SEMANTIC_TASK_WORKER_ENABLED": "false",
                "DOCUMENT_PARSING_TASK_WORKER_ENABLED": "false",
            },
            clear=False,
        )
        self._env_patcher.start()
        self._db_init_patcher = patch.object(main_module, "create_db_and_tables", side_effect=self._create_test_tables)
        self._db_init_patcher.start()
        self.client_cm = TestClient(main_module.create_app())
        self.client = self.client_cm.__enter__()

    def tearDown(self) -> None:
        self.client_cm.__exit__(None, None, None)
        self._engine.dispose()
        database_module.engine = self._original_db_engine
        llm_service_module.engine = self._original_service_engine
        get_llm_service_client.cache_clear()
        load_extracted_image_semantic_prompt.cache_clear()
        self._db_init_patcher.stop()
        self._env_patcher.stop()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _create_test_tables(self) -> None:
        LlmChatRecord.metadata.create_all(self._engine, tables=[LlmChatRecord.__table__])

    def _load_records(self) -> list[LlmChatRecord]:
        with Session(self._engine) as session:
            statement = select(LlmChatRecord).order_by(LlmChatRecord.id.asc())
            return list(session.exec(statement).all())

    def test_startup_creates_llm_chat_records_table(self):
        inspector = inspect(self._engine)
        self.assertTrue(inspector.has_table("llm_chat_records"))

    def test_internal_health_returns_ok(self):
        response = self.client.get("/internal/llm/health", headers={"X-Request-ID": "req-health-1"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        self.assertEqual(response.headers["X-Request-ID"], "req-health-1")

    def test_internal_chat_persists_succeeded_record(self):
        with patch(
            "backend.app.services.llm_service.httpx.post",
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
                json={
                    "prompt": "Say hello",
                    "system_prompt": "You are helpful",
                    "request_id": "req-user-1",
                    "temperature": 0.2,
                    "max_tokens": 256,
                },
                headers={"X-Request-ID": "req-chain-1", "X-Caller-Service": "legacy-worker"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["text"], "hello world")
        self.assertEqual(payload["model"], "gpt-test")
        self.assertEqual(payload["usage"], {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30})
        self.assertEqual(payload["request_id"], "req-user-1")

        records = self._load_records()
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.status, LlmChatRecordStatus.succeeded)
        self.assertEqual(record.request_id, "req-user-1")
        self.assertEqual(record.caller_service, "legacy-worker")
        self.assertEqual(record.prompt, "Say hello")
        self.assertEqual(record.system_prompt, "You are helpful")
        self.assertEqual(record.resolved_model, "gpt-test")
        self.assertEqual(record.prompt_tokens, 10)
        self.assertEqual(record.completion_tokens, 20)
        self.assertEqual(record.total_tokens, 30)
        self.assertEqual(record.response_text, "hello world")
        self.assertEqual(record.upstream_response_request_id, "req-header-1")
        self.assertEqual(record.upstream_response_id, "chatcmpl-1")

    def test_internal_chat_redacts_multimodal_snapshot(self):
        image_url = "data:image/png;base64,AAAA"
        with patch(
            "backend.app.services.llm_service.httpx.post",
            return_value=_ResponseStub(
                {
                    "id": "chatcmpl-2",
                    "model": "gpt-test",
                    "choices": [{"message": {"content": "image summary"}}],
                    "usage": {"prompt_tokens": 11, "completion_tokens": 21, "total_tokens": 32},
                }
            ),
        ):
            response = self.client.post(
                "/internal/llm/chat",
                json={
                    "prompt": "Describe the image",
                    "request_id": "req-user-2",
                    "input_parts": [
                        {"type": "text", "text": "Describe the image"},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                },
            )

        self.assertEqual(response.status_code, 200)
        records = self._load_records()
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.input_part_count, 2)
        self.assertEqual(record.image_part_count, 1)
        self.assertEqual(record.input_parts_snapshot[0], {"type": "text", "text": "Describe the image"})
        self.assertEqual(record.input_parts_snapshot[1]["type"], "image_url")
        self.assertEqual(record.input_parts_snapshot[1]["url_kind"], "data_url")
        self.assertEqual(record.input_parts_snapshot[1]["content_type"], "image/png")
        self.assertIn("url_sha256", record.input_parts_snapshot[1])
        self.assertNotIn("url", record.input_parts_snapshot[1])
        self.assertNotIn("AAAA", str(record.input_parts_snapshot[1]))

    def test_internal_chat_returns_502_and_persists_failed_record_on_timeout(self):
        with patch(
            "backend.app.services.llm_service.httpx.post",
            side_effect=httpx.TimeoutException("timed out"),
        ):
            response = self.client.post("/internal/llm/chat", json={"prompt": "hello", "request_id": "req-timeout-1"})

        self.assertEqual(response.status_code, 502)
        self.assertIn("Upstream LLM request failed", response.json()["detail"])
        records = self._load_records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status, LlmChatRecordStatus.failed)
        self.assertEqual(records[0].request_id, "req-timeout-1")
        self.assertIn("Upstream timeout", records[0].error_message or "")

    def test_internal_chat_returns_500_when_persistence_fails(self):
        with patch(
            "backend.app.services.llm_service.httpx.post",
            return_value=_ResponseStub(
                {
                    "id": "chatcmpl-3",
                    "model": "gpt-test",
                    "choices": [{"message": {"content": "hello world"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
                }
            ),
        ):
            with patch(
                "backend.app.services.llm_service.persist_llm_chat_record",
                side_effect=LlmChatPersistenceError("chat persistence failed"),
            ):
                response = self.client.post("/internal/llm/chat", json={"prompt": "hello"})

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["detail"], "chat persistence failed")
        self.assertEqual(self._load_records(), [])


class BackendLlmRouterTests(TestCase):
    def test_get_llm_availability_returns_available_true(self):
        response = llm_router.get_llm_availability(client=_ClientStub(available=True))

        self.assertTrue(response.available)
        self.assertEqual(response.service, "backend")
        self.assertEqual(response.health_path, "/internal/llm/health")
        self.assertIsNone(response.error)

    def test_get_llm_availability_returns_available_false(self):
        response = llm_router.get_llm_availability(client=_ClientStub(available=False, availability_error="missing key"))

        self.assertFalse(response.available)
        self.assertEqual(response.service, "backend")
        self.assertEqual(response.error, "missing key")

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

    def test_chat_returns_500_on_persistence_error(self):
        with self.assertRaises(HTTPException) as ctx:
            llm_router.chat(
                payload=llm_router.LlmChatRequest(prompt="hello"),
                client=_ClientStub(persistence_error="chat persistence failed"),
            )

        self.assertEqual(ctx.exception.status_code, 500)
        self.assertEqual(ctx.exception.detail, "chat persistence failed")

    def test_chat_returns_502_on_service_error(self):
        with self.assertRaises(HTTPException) as ctx:
            llm_router.chat(
                payload=llm_router.LlmChatRequest(prompt="hello"),
                client=_ClientStub(chat_error="request failed"),
            )

        self.assertEqual(ctx.exception.status_code, 502)

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
