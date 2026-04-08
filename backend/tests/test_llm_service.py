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

        configs = self._load_configs()
        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0].code, "default")
        self.assertTrue(configs[0].is_active)
        self.assertEqual(configs[0].base_url, "https://example.com/v1")
        self.assertEqual(configs[0].default_model, "gpt-test")

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
        self.assertEqual(records[0].upstream_response_request_id, "req-header-1")
        self.assertEqual(records[0].upstream_response_id, "chatcmpl-1")

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

    def test_config_crud_activation_and_soft_delete_flow(self):
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
        created_payload = create_response.json()
        self.assertEqual(created_payload["base_url"], "https://tenant.example.com/v1")
        self.assertTrue(created_payload["has_api_key"])
        self.assertNotEqual(created_payload["api_key_masked"], "tenant-secret-1234")
        created_id = created_payload["id"]
        created_uuid = UUID(created_id)

        patch_response = self.client.patch(
            f"/llm/configs/{created_id}",
            json={
                "name": "Tenant A Updated",
                "base_url": "https://tenant2.example.com/v1/",
            },
        )
        self.assertEqual(patch_response.status_code, 200)
        self.assertEqual(patch_response.json()["name"], "Tenant A Updated")
        self.assertEqual(patch_response.json()["base_url"], "https://tenant2.example.com/v1")
        self.assertTrue(patch_response.json()["has_api_key"])

        with Session(self._engine) as session:
            updated = session.get(LlmConfig, created_uuid)
            self.assertEqual(updated.api_key, "tenant-secret-1234")

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



