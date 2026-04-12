import shutil
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
from uuid import UUID, uuid4

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import inspect
from sqlmodel import Session, create_engine, select

import backend.app.database as database_module
import backend.app.main as main_module
import backend.app.services.llm_config_service as llm_config_service_module
import backend.app.services.llm_service as llm_service_module
from backend.app.models import LlmChatRecord, LlmChatRecordStatus, LlmConfig, LlmConfigProvider


class _ResponseStub:
    def __init__(
        self,
        payload,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        text: str = "",
        method: str = "POST",
        url: str = "https://example.com/v1/chat/completions",
    ):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text
        self._method = method
        self._url = url

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request(self._method, self._url)
            response = httpx.Response(self.status_code, text=self.text, headers=self.headers, request=request)
            raise httpx.HTTPStatusError("upstream error", request=request, response=response)

    def json(self):
        return self._payload


class BackendLlmIntegrationTests(TestCase):
    def setUp(self) -> None:
        self._tmpdir = Path("backend/tests/runtime_cases") / f"case-{uuid4().hex}"
        self._tmpdir.mkdir(parents=True, exist_ok=True)
        db_path = self._tmpdir / "backend-llm-test.db"
        self._engine = create_engine(
            f"sqlite:///{db_path.as_posix()}",
            connect_args={"check_same_thread": False},
        )
        self._original_db_engine = database_module.engine
        self._original_service_engine = llm_service_module.engine
        self._original_config_service_engine = llm_config_service_module.engine
        database_module.engine = self._engine
        llm_service_module.engine = self._engine
        llm_config_service_module.engine = self._engine
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
        self.client_cm = None
        self.client = None

    def tearDown(self) -> None:
        if self.client_cm is not None:
            self.client_cm.__exit__(None, None, None)
        self._engine.dispose()
        database_module.engine = self._original_db_engine
        llm_service_module.engine = self._original_service_engine
        llm_config_service_module.engine = self._original_config_service_engine
        self._db_init_patcher.stop()
        self._env_patcher.stop()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _create_test_tables(self) -> None:
        LlmConfig.metadata.create_all(self._engine, tables=[LlmConfig.__table__])
        LlmChatRecord.metadata.create_all(self._engine, tables=[LlmChatRecord.__table__])

    def _start_client(self) -> None:
        if self.client_cm is not None:
            return
        self.client_cm = TestClient(main_module.create_app())
        self.client = self.client_cm.__enter__()

    def _load_records(self) -> list[LlmChatRecord]:
        with Session(self._engine) as session:
            statement = select(LlmChatRecord).order_by(LlmChatRecord.id.asc())
            return list(session.exec(statement).all())

    def _load_configs(self) -> list[LlmConfig]:
        with Session(self._engine) as session:
            statement = select(LlmConfig).order_by(LlmConfig.created_at.asc())
            return list(session.exec(statement).all())

    def _create_config(
        self,
        *,
        code: str,
        name: str,
        base_url: str = "https://provider.example.com/v1",
        api_key: str = "provider-key",
        default_model: str = "provider-model",
        enabled: bool = True,
        is_active: bool = False,
    ) -> LlmConfig:
        with Session(self._engine) as session:
            config = LlmConfig(
                code=code,
                name=name,
                provider=LlmConfigProvider.openai_compatible,
                base_url=base_url,
                api_key=api_key,
                default_model=default_model,
                timeout_seconds=45.0,
                enabled=enabled,
                is_active=is_active,
            )
            session.add(config)
            session.commit()
            session.refresh(config)
            return config

    def test_startup_creates_tables_and_bootstraps_default_config(self):
        self._start_client()

        inspector = inspect(self._engine)
        self.assertTrue(inspector.has_table("llm_chat_records"))
        self.assertTrue(inspector.has_table("llm_configs"))
        llm_chat_record_columns = {column["name"] for column in inspector.get_columns("llm_chat_records")}
        self.assertIn("reasoning_content", llm_chat_record_columns)

        configs = self._load_configs()
        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0].code, "default")
        self.assertTrue(configs[0].is_active)
        self.assertEqual(configs[0].base_url, "https://example.com/v1")
        self.assertEqual(configs[0].default_model, "gpt-test")

    def test_bootstrap_without_api_key_creates_disabled_draft_config(self):
        with patch.dict("os.environ", {"LLM_API_KEY": ""}, clear=False):
            self._start_client()

        configs = self._load_configs()
        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0].code, "default")
        self.assertFalse(configs[0].enabled)
        self.assertFalse(configs[0].is_active)
        self.assertEqual(configs[0].api_key, "")

    def test_bootstrap_skips_when_config_already_exists(self):
        self._create_test_tables()
        self._create_config(code="preseeded", name="Preseeded Config", is_active=True)

        self._start_client()

        configs = self._load_configs()
        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0].code, "preseeded")

    def test_chat_persists_succeeded_record_with_resolved_config_snapshot(self):
        self._start_client()

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
                "/llm/chat",
                json={
                    "prompt": "Say hello",
                    "system_prompt": "You are helpful",
                    "request_id": "req-user-1",
                    "temperature": 0.2,
                    "max_tokens": 256,
                },
                headers={"X-Request-ID": "req-chain-1"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["text"], "hello world")
        self.assertEqual(payload["model"], "gpt-test")
        self.assertEqual(payload["usage"], {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30})
        self.assertEqual(payload["request_id"], "req-user-1")

        records = self._load_records()
        self.assertEqual(len(records), 1)
        configs = self._load_configs()
        self.assertEqual(records[0].status, LlmChatRecordStatus.succeeded)
        self.assertEqual(records[0].llm_config_id, configs[0].id)
        self.assertEqual(records[0].llm_config_code, "default")
        self.assertEqual(records[0].response_text, "hello world")
        self.assertIsNone(records[0].reasoning_content)
        self.assertEqual(records[0].upstream_response_request_id, "req-header-1")
        self.assertEqual(records[0].upstream_response_id, "chatcmpl-1")

    def test_chat_persists_explicit_reasoning_content(self):
        self._start_client()

        with patch(
            "backend.app.services.llm_service.httpx.post",
            return_value=_ResponseStub(
                {
                    "id": "chatcmpl-reasoning",
                    "model": "gpt-test",
                    "choices": [
                        {
                            "message": {
                                "content": "final answer",
                                "reasoning_content": "reasoning steps",
                            }
                        }
                    ],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
                }
            ),
        ):
            response = self.client.post("/llm/chat", json={"prompt": "hello"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["text"], "final answer")

        records = self._load_records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].response_text, "final answer")
        self.assertEqual(records[0].reasoning_content, "reasoning steps")

    def test_chat_splits_reasoning_content_from_think_tag(self):
        self._start_client()

        with patch(
            "backend.app.services.llm_service.httpx.post",
            return_value=_ResponseStub(
                {
                    "id": "chatcmpl-think",
                    "model": "gpt-test",
                    "choices": [{"message": {"content": "<think>chain of thought</think>final answer"}}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 6, "total_tokens": 11},
                }
            ),
        ):
            response = self.client.post("/llm/chat", json={"prompt": "hello"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["text"], "final answer")

        records = self._load_records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].response_text, "final answer")
        self.assertEqual(records[0].reasoning_content, "<think>chain of thought")

    def test_chat_keeps_plain_content_when_no_reasoning_content_exists(self):
        self._start_client()

        with patch(
            "backend.app.services.llm_service.httpx.post",
            return_value=_ResponseStub(
                {
                    "id": "chatcmpl-plain",
                    "model": "gpt-test",
                    "choices": [{"message": {"content": "plain answer"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }
            ),
        ):
            response = self.client.post("/llm/chat", json={"prompt": "hello"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["text"], "plain answer")

        records = self._load_records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].response_text, "plain answer")
        self.assertIsNone(records[0].reasoning_content)

    def test_chat_normalizes_list_content_before_reasoning_split(self):
        self._start_client()

        with patch(
            "backend.app.services.llm_service.httpx.post",
            return_value=_ResponseStub(
                {
                    "id": "chatcmpl-list",
                    "model": "gpt-test",
                    "choices": [
                        {
                            "message": {
                                "content": [
                                    {"type": "text", "text": "<think>reasoning"},
                                    {"type": "text", "text": "</think>final"},
                                    {"type": "text", "text": " answer"},
                                ]
                            }
                        }
                    ],
                    "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
                }
            ),
        ):
            response = self.client.post("/llm/chat", json={"prompt": "hello"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["text"], "final answer")

        records = self._load_records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].response_text, "final answer")
        self.assertEqual(records[0].reasoning_content, "<think>reasoning")

    def test_chat_returns_502_and_persists_failed_record_on_timeout(self):
        self._start_client()

        with patch(
            "backend.app.services.llm_service.httpx.post",
            side_effect=httpx.TimeoutException("timed out"),
        ):
            response = self.client.post("/llm/chat", json={"prompt": "hello", "request_id": "req-timeout-1"})

        self.assertEqual(response.status_code, 502)
        self.assertIn("llm chat failed: Upstream timeout", response.json()["detail"])
        records = self._load_records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status, LlmChatRecordStatus.failed)
        self.assertEqual(records[0].llm_config_code, "default")
        self.assertIn("Upstream timeout", records[0].error_message or "")

    def test_chat_uses_requested_config_id_without_restart(self):
        self._start_client()
        custom_config = self._create_config(
            code="custom-1",
            name="Custom One",
            base_url="https://custom.example.com/v1",
            api_key="custom-key",
            default_model="gpt-custom",
            enabled=True,
            is_active=False,
        )

        with patch(
            "backend.app.services.llm_service.httpx.post",
            return_value=_ResponseStub(
                {
                    "id": "chatcmpl-custom",
                    "model": "gpt-custom",
                    "choices": [{"message": {"content": "custom world"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
                }
            ),
        ) as mock_post:
            response = self.client.post(
                "/llm/chat",
                json={
                    "prompt": "hello",
                    "config_id": str(custom_config.id),
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["model"], "gpt-custom")
        self.assertEqual(mock_post.call_args.kwargs["json"]["model"], "gpt-custom")
        self.assertEqual(mock_post.call_args.kwargs["headers"]["Authorization"], "Bearer custom-key")
        self.assertEqual(mock_post.call_args.args[0], "https://custom.example.com/v1/chat/completions")

        records = self._load_records()
        self.assertEqual(records[0].llm_config_id, custom_config.id)
        self.assertEqual(records[0].llm_config_code, "custom-1")

    def test_availability_returns_503_when_no_active_config_exists(self):
        self._start_client()

        with Session(self._engine) as session:
            for config in session.exec(select(LlmConfig)).all():
                config.is_active = False
                session.add(config)
            session.commit()

        response = self.client.get("/llm/availability")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "No active LLM config is configured")

    def test_availability_returns_false_when_probe_request_fails(self):
        self._start_client()

        request = httpx.Request("GET", "https://example.com/v1/models")
        with patch(
            "backend.app.services.llm_service.httpx.get",
            side_effect=httpx.ConnectError("dns failed", request=request),
        ):
            response = self.client.get("/llm/availability")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["available"])
        self.assertIn("Upstream request error", response.json()["error"])

    def test_validate_endpoint_reports_basic_success(self):
        self._start_client()
        active_config = self._load_configs()[0]

        with patch(
            "backend.app.services.llm_service.httpx.get",
            return_value=_ResponseStub(
                {"data": [{"id": "gpt-test"}]},
                method="GET",
                url="https://example.com/v1/models",
            ),
        ):
            response = self.client.post(f"/llm/configs/{active_config.id}/validate?depth=basic")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["valid"])
        self.assertEqual(payload["stage"], "network")
        self.assertFalse(payload["model_checked"])

    def test_validate_endpoint_reports_model_not_found_on_strict_probe(self):
        self._start_client()
        active_config = self._load_configs()[0]

        with patch(
            "backend.app.services.llm_service.httpx.get",
            return_value=_ResponseStub(
                {"data": [{"id": "other-model"}]},
                method="GET",
                url="https://example.com/v1/models",
            ),
        ):
            response = self.client.post(f"/llm/configs/{active_config.id}/validate?depth=strict")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["valid"])
        self.assertEqual(payload["stage"], "model")
        self.assertEqual(payload["error_code"], "model_not_found")

    def test_validate_endpoint_falls_back_to_chat_probe_when_models_endpoint_is_unavailable(self):
        self._start_client()
        active_config = self._load_configs()[0]

        with patch(
            "backend.app.services.llm_service.httpx.get",
            return_value=_ResponseStub(
                {"error": "not found"},
                status_code=404,
                text="not found",
                method="GET",
                url="https://example.com/v1/models",
            ),
        ), patch(
            "backend.app.services.llm_service.httpx.post",
            return_value=_ResponseStub(
                {
                    "id": "chatcmpl-probe",
                    "model": "gpt-test",
                    "choices": [{"message": {"content": "ok"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                },
                method="POST",
                url="https://example.com/v1/chat/completions",
            ),
        ):
            response = self.client.post(f"/llm/configs/{active_config.id}/validate?depth=strict")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["valid"])
        self.assertEqual(payload["stage"], "model")
        self.assertTrue(payload["model_checked"])

    def test_validate_endpoint_reports_auth_failure(self):
        self._start_client()
        active_config = self._load_configs()[0]

        with patch(
            "backend.app.services.llm_service.httpx.get",
            return_value=_ResponseStub(
                {"error": "unauthorized"},
                status_code=401,
                text="unauthorized",
                method="GET",
                url="https://example.com/v1/models",
            ),
        ):
            response = self.client.post(f"/llm/configs/{active_config.id}/validate?depth=basic")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["valid"])
        self.assertEqual(payload["stage"], "auth")
        self.assertEqual(payload["error_code"], "auth_failed")

    def test_models_endpoint_uses_requested_config_and_returns_model_ids(self):
        self._start_client()
        custom_config = self._create_config(
            code="custom-models",
            name="Custom Models",
            base_url="https://custom.example.com/v1",
            api_key="custom-models-key",
            default_model="gpt-custom-models",
            enabled=True,
            is_active=False,
        )

        with patch(
            "backend.app.services.llm_service.httpx.get",
            return_value=_ResponseStub(
                {"data": [{"id": "gpt-custom-models"}, {"id": "gpt-custom-vision"}]},
                method="GET",
                url="https://custom.example.com/v1/models",
            ),
        ) as mock_get:
            response = self.client.get(f"/llm/configs/{custom_config.id}/models")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["normalized_base_url"], "https://custom.example.com/v1")
        self.assertEqual(payload["model_ids"], ["gpt-custom-models", "gpt-custom-vision"])
        self.assertEqual(mock_get.call_args.args[0], "https://custom.example.com/v1/models")
        self.assertEqual(mock_get.call_args.kwargs["headers"]["Authorization"], "Bearer custom-models-key")

    def test_models_endpoint_supports_models_payload_and_dedupes_stably(self):
        self._start_client()
        active_config = self._load_configs()[0]

        with patch(
            "backend.app.services.llm_service.httpx.get",
            return_value=_ResponseStub(
                {"models": ["gpt-test", "gpt-test", {"id": "gpt-4.1"}, {"name": "gpt-test"}, {"name": "gpt-4o"}]},
                method="GET",
                url="https://example.com/v1/models",
            ),
        ):
            response = self.client.get(f"/llm/configs/{active_config.id}/models")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["model_ids"], ["gpt-test", "gpt-4.1", "gpt-4o"])

    def test_models_endpoint_returns_failed_result_on_auth_error(self):
        self._start_client()
        active_config = self._load_configs()[0]

        with patch(
            "backend.app.services.llm_service.httpx.get",
            return_value=_ResponseStub(
                {"error": "unauthorized"},
                status_code=401,
                text="unauthorized",
                method="GET",
                url="https://example.com/v1/models",
            ),
        ):
            response = self.client.get(f"/llm/configs/{active_config.id}/models")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["success"])
        self.assertEqual(payload["model_ids"], [])
        self.assertEqual(payload["http_status"], 401)
        self.assertEqual(payload["error_code"], "auth_failed")

    def test_models_endpoint_returns_failed_result_on_request_error(self):
        self._start_client()
        active_config = self._load_configs()[0]
        request = httpx.Request("GET", "https://example.com/v1/models")

        with patch(
            "backend.app.services.llm_service.httpx.get",
            side_effect=httpx.ConnectError("dns failed", request=request),
        ):
            response = self.client.get(f"/llm/configs/{active_config.id}/models")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["success"])
        self.assertEqual(payload["model_ids"], [])
        self.assertEqual(payload["error_code"], "request_error")
        self.assertIn("Upstream request error", payload["error_message"])

    def test_models_endpoint_returns_failed_result_on_invalid_json(self):
        self._start_client()
        active_config = self._load_configs()[0]

        broken_response = _ResponseStub(
            {"ignored": True},
            method="GET",
            url="https://example.com/v1/models",
        )
        broken_response.json = lambda: (_ for _ in ()).throw(ValueError("bad json"))

        with patch(
            "backend.app.services.llm_service.httpx.get",
            return_value=broken_response,
        ):
            response = self.client.get(f"/llm/configs/{active_config.id}/models")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["success"])
        self.assertEqual(payload["model_ids"], [])
        self.assertEqual(payload["error_code"], "invalid_json")

    def test_models_endpoint_returns_failed_result_when_no_model_identifiers_exist(self):
        self._start_client()
        active_config = self._load_configs()[0]

        with patch(
            "backend.app.services.llm_service.httpx.get",
            return_value=_ResponseStub(
                {"data": [{"owned_by": "tenant"}]},
                method="GET",
                url="https://example.com/v1/models",
            ),
        ):
            response = self.client.get(f"/llm/configs/{active_config.id}/models")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["success"])
        self.assertEqual(payload["model_ids"], [])
        self.assertEqual(payload["error_code"], "invalid_models_payload")

    def test_models_endpoint_returns_404_for_missing_config(self):
        self._start_client()

        response = self.client.get(f"/llm/configs/{uuid4()}/models")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "LLM config not found")

    def test_models_endpoint_returns_409_for_disabled_config(self):
        self._start_client()
        disabled_config = self._create_config(code="disabled-models", name="Disabled Models", enabled=False)

        response = self.client.get(f"/llm/configs/{disabled_config.id}/models")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "LLM config is disabled")

    def test_preview_models_endpoint_returns_model_ids(self):
        self._start_client()

        with patch(
            "backend.app.services.llm_service.httpx.get",
            return_value=_ResponseStub(
                {"data": [{"id": "gpt-preview"}, {"id": "gpt-preview-vision"}]},
                method="GET",
                url="https://preview.example.com/v1/models",
            ),
        ) as mock_get:
            response = self.client.post(
                "/llm/models/preview",
                json={
                    "provider": "openai_compatible",
                    "base_url": "https://preview.example.com/v1",
                    "api_key": "preview-key",
                    "timeout_seconds": 15,
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["normalized_base_url"], "https://preview.example.com/v1")
        self.assertEqual(payload["model_ids"], ["gpt-preview", "gpt-preview-vision"])
        self.assertEqual(mock_get.call_args.args[0], "https://preview.example.com/v1/models")
        self.assertEqual(mock_get.call_args.kwargs["headers"]["Authorization"], "Bearer preview-key")

    def test_preview_models_endpoint_returns_failed_result_on_auth_error(self):
        self._start_client()

        with patch(
            "backend.app.services.llm_service.httpx.get",
            return_value=_ResponseStub(
                {"error": "unauthorized"},
                status_code=401,
                text="unauthorized",
                method="GET",
                url="https://preview.example.com/v1/models",
            ),
        ):
            response = self.client.post(
                "/llm/models/preview",
                json={
                    "provider": "openai_compatible",
                    "base_url": "https://preview.example.com/v1",
                    "api_key": "preview-key",
                    "timeout_seconds": 15,
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["success"])
        self.assertEqual(payload["http_status"], 401)
        self.assertEqual(payload["error_code"], "auth_failed")
        self.assertEqual(payload["model_ids"], [])

    def test_preview_models_endpoint_rejects_invalid_base_url(self):
        self._start_client()

        response = self.client.post(
            "/llm/models/preview",
            json={
                "provider": "openai_compatible",
                "base_url": "ftp://preview.example.com/v1",
                "api_key": "preview-key",
                "timeout_seconds": 15,
            },
        )

        self.assertEqual(response.status_code, 422)
        self.assertIn("must use http or https", response.json()["detail"])

    def test_preview_models_endpoint_rejects_missing_api_key(self):
        self._start_client()

        response = self.client.post(
            "/llm/models/preview",
            json={
                "provider": "openai_compatible",
                "base_url": "https://preview.example.com/v1",
                "api_key": "   ",
                "timeout_seconds": 15,
            },
        )

        self.assertEqual(response.status_code, 422)
        self.assertIn("api key is required", response.json()["detail"])

    def test_preview_models_endpoint_rejects_invalid_timeout(self):
        self._start_client()

        response = self.client.post(
            "/llm/models/preview",
            json={
                "provider": "openai_compatible",
                "base_url": "https://preview.example.com/v1",
                "api_key": "preview-key",
                "timeout_seconds": 0,
            },
        )

        self.assertEqual(response.status_code, 422)

    def test_chat_returns_409_for_disabled_requested_config(self):
        self._start_client()
        disabled_config = self._create_config(code="disabled-1", name="Disabled", enabled=False)

        response = self.client.post(
            "/llm/chat",
            json={
                "prompt": "hello",
                "config_id": str(disabled_config.id),
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "LLM config is disabled")

    def test_create_and_update_validate_static_fields(self):
        self._start_client()

        create_invalid_url = self.client.post(
            "/llm/configs",
            json={
                "code": "bad-url",
                "name": "Bad Url",
                "provider": "openai_compatible",
                "base_url": "ftp://tenant.example.com/v1",
                "api_key": "tenant-secret",
                "default_model": "gpt-tenant",
                "timeout_seconds": 12,
                "enabled": True,
                "is_active": False,
            },
        )
        self.assertEqual(create_invalid_url.status_code, 422)
        self.assertIn("must use http or https", create_invalid_url.json()["detail"])

        create_missing_key = self.client.post(
            "/llm/configs",
            json={
                "code": "missing-key",
                "name": "Missing Key",
                "provider": "openai_compatible",
                "base_url": "https://tenant.example.com/v1",
                "api_key": "   ",
                "default_model": "gpt-tenant",
                "timeout_seconds": 12,
                "enabled": True,
                "is_active": False,
            },
        )
        self.assertEqual(create_missing_key.status_code, 422)
        self.assertIn("api key is required", create_missing_key.json()["detail"])

        create_response = self.client.post(
            "/llm/configs",
            json={
                "code": "tenant-a",
                "name": "Tenant A",
                "provider": "openai_compatible",
                "base_url": "https://tenant.example.com/v1/",
                "api_key": "tenant-secret-1234",
                "default_model": "gpt-tenant",
                "timeout_seconds": 12,
                "enabled": True,
                "is_active": False,
            },
        )
        self.assertEqual(create_response.status_code, 201)

        update_invalid_model = self.client.patch(
            f"/llm/configs/{create_response.json()['id']}",
            json={"default_model": "   "},
        )
        self.assertEqual(update_invalid_model.status_code, 422)
        self.assertIn("default model is required", update_invalid_model.json()["detail"])

    def test_activation_requires_strict_probe_success(self):
        self._start_client()

        create_response = self.client.post(
            "/llm/configs",
            json={
                "code": "tenant-a",
                "name": "Tenant A",
                "provider": "openai_compatible",
                "base_url": "https://tenant.example.com/v1/",
                "api_key": "tenant-secret-1234",
                "default_model": "gpt-tenant",
                "timeout_seconds": 12,
                "enabled": True,
                "is_active": False,
            },
        )
        self.assertEqual(create_response.status_code, 201)
        created_id = create_response.json()["id"]
        created_uuid = UUID(created_id)

        with patch(
            "backend.app.services.llm_service.httpx.get",
            return_value=_ResponseStub(
                {"data": [{"id": "other-model"}]},
                method="GET",
                url="https://tenant.example.com/v1/models",
            ),
        ):
            activate_failure = self.client.post(f"/llm/configs/{created_id}/activate")

        self.assertEqual(activate_failure.status_code, 409)
        self.assertIn("validation failed at model", activate_failure.json()["detail"])

        with Session(self._engine) as session:
            created = session.get(LlmConfig, created_uuid)
            default_active = session.exec(select(LlmConfig).where(LlmConfig.code == "default")).one()
            self.assertFalse(created.is_active)
            self.assertTrue(default_active.is_active)

        with patch(
            "backend.app.services.llm_service.httpx.get",
            return_value=_ResponseStub(
                {"data": [{"id": "gpt-tenant"}]},
                method="GET",
                url="https://tenant.example.com/v1/models",
            ),
        ):
            activate_success = self.client.post(f"/llm/configs/{created_id}/activate")

        self.assertEqual(activate_success.status_code, 200)
        self.assertTrue(activate_success.json()["is_active"])

    def test_config_crud_activation_and_soft_delete_flow(self):
        self._start_client()

        create_response = self.client.post(
            "/llm/configs",
            json={
                "code": "tenant-b",
                "name": "Tenant B",
                "provider": "openai_compatible",
                "base_url": "https://tenant-b.example.com/v1/",
                "api_key": "tenant-secret-5678",
                "default_model": "gpt-tenant-b",
                "timeout_seconds": 12,
                "enabled": True,
                "is_active": False,
            },
        )
        self.assertEqual(create_response.status_code, 201)
        created_payload = create_response.json()
        self.assertEqual(created_payload["base_url"], "https://tenant-b.example.com/v1")
        self.assertTrue(created_payload["has_api_key"])
        self.assertNotEqual(created_payload["api_key_masked"], "tenant-secret-5678")
        created_id = created_payload["id"]
        created_uuid = UUID(created_id)

        patch_response = self.client.patch(
            f"/llm/configs/{created_id}",
            json={
                "name": "Tenant B Updated",
                "base_url": "https://tenant-b2.example.com/v1/",
            },
        )
        self.assertEqual(patch_response.status_code, 200)
        self.assertEqual(patch_response.json()["name"], "Tenant B Updated")
        self.assertEqual(patch_response.json()["base_url"], "https://tenant-b2.example.com/v1")
        self.assertTrue(patch_response.json()["has_api_key"])

        with Session(self._engine) as session:
            updated = session.get(LlmConfig, created_uuid)
            self.assertEqual(updated.api_key, "tenant-secret-5678")

        with patch(
            "backend.app.services.llm_service.httpx.get",
            return_value=_ResponseStub(
                {"data": [{"id": "gpt-tenant-b"}]},
                method="GET",
                url="https://tenant-b2.example.com/v1/models",
            ),
        ):
            activate_response = self.client.post(f"/llm/configs/{created_id}/activate")
        self.assertEqual(activate_response.status_code, 200)
        self.assertTrue(activate_response.json()["is_active"])

        active_delete_response = self.client.delete(f"/llm/configs/{created_id}")
        self.assertEqual(active_delete_response.status_code, 409)
        self.assertEqual(active_delete_response.json()["detail"], "Active LLM config cannot be deleted")

        configs_response = self.client.get("/llm/configs")
        self.assertEqual(configs_response.status_code, 200)
        self.assertGreaterEqual(len(configs_response.json()), 2)

        default_config = next(item for item in configs_response.json() if item["code"] == "default")
        with patch(
            "backend.app.services.llm_service.httpx.get",
            return_value=_ResponseStub(
                {"data": [{"id": "gpt-test"}]},
                method="GET",
                url="https://example.com/v1/models",
            ),
        ):
            switch_back_response = self.client.post(f"/llm/configs/{default_config['id']}/activate")
        self.assertEqual(switch_back_response.status_code, 200)

        delete_response = self.client.delete(f"/llm/configs/{created_id}")
        self.assertEqual(delete_response.status_code, 204)

        get_deleted_response = self.client.get(f"/llm/configs/{created_id}")
        self.assertEqual(get_deleted_response.status_code, 404)

        with Session(self._engine) as session:
            deleted = session.get(LlmConfig, created_uuid)
            self.assertIsNotNone(deleted.deleted_at)

    def test_get_active_config_endpoint_returns_current_active_config(self):
        self._start_client()

        response = self.client.get("/llm/configs/active")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["code"], "default")
        self.assertTrue(response.json()["is_active"])
