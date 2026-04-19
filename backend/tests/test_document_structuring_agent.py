import os
import shutil
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine, select

import backend.app.database as database_module
import backend.app.main as main_module
import backend.app.services.llm_config_service as llm_config_service_module
import backend.app.services.llm_service as llm_service_module
from backend.app.agents.document_structuring import (
    DocumentStructuringAgentError,
    DocumentStructuringPromptError,
    load_document_structuring_prompt,
    run_document_structuring_agent,
)
from backend.app.models import LlmChatRecord, LlmConfig, LlmConfigProvider
from backend.app.services import LlmChatPersistenceError, LlmChatResult, LlmUsage


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


class _ClientStub:
    def __init__(self, *, config_id=None, config_code=None):
        self.config_id = config_id
        self.config_code = config_code
        self.last_call = None

    def chat(self, **kwargs):
        self.last_call = kwargs
        return (
            LlmChatResult(
                text="# Structured\n\ncontent",
                model="gpt-structured",
                usage=LlmUsage(prompt_tokens=11, completion_tokens=22, total_tokens=33),
                request_id=kwargs.get("request_id", "req-structured"),
            ),
            None,
        )


class DocumentStructuringPromptTests(TestCase):
    def setUp(self) -> None:
        load_document_structuring_prompt.cache_clear()
        self._temp_root = Path(os.getcwd()) / "backend" / "tests" / ".tmp"
        self._temp_root.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        load_document_structuring_prompt.cache_clear()

    def _write_prompt_file(self, contents: str) -> Path:
        prompt_path = self._temp_root / f"document-structuring-{uuid4().hex}.txt"
        prompt_path.write_text(contents, encoding="utf-8")
        self.addCleanup(lambda: prompt_path.unlink(missing_ok=True))
        return prompt_path

    def test_load_prompt_reads_configured_file(self):
        prompt_path = self._write_prompt_file("system prompt")

        with patch.dict(os.environ, {"DOCUMENT_STRUCTURING_AGENT_PROMPT_PATH": str(prompt_path)}, clear=False):
            prompt = load_document_structuring_prompt()

        self.assertEqual(prompt, "system prompt")

    def test_load_prompt_raises_when_file_missing(self):
        missing_path = self._temp_root / "missing-document-structuring-prompt.txt"
        missing_path.unlink(missing_ok=True)

        with patch.dict(os.environ, {"DOCUMENT_STRUCTURING_AGENT_PROMPT_PATH": str(missing_path)}, clear=False):
            with self.assertRaises(DocumentStructuringPromptError):
                load_document_structuring_prompt()

    def test_load_prompt_raises_when_file_empty(self):
        prompt_path = self._write_prompt_file("   \n")

        with patch.dict(os.environ, {"DOCUMENT_STRUCTURING_AGENT_PROMPT_PATH": str(prompt_path)}, clear=False):
            with self.assertRaises(DocumentStructuringPromptError):
                load_document_structuring_prompt()


class DocumentStructuringServiceTests(TestCase):
    def tearDown(self) -> None:
        load_document_structuring_prompt.cache_clear()

    def test_run_rejects_blank_source_text(self):
        with self.assertRaises(DocumentStructuringAgentError) as ctx:
            run_document_structuring_agent(
                source_text="   ",
                session=object(),
            )

        self.assertEqual(str(ctx.exception), "source_text is required")

    def test_run_success_builds_expected_llm_call(self):
        client = _ClientStub(config_id=uuid4(), config_code="default")
        session = object()
        config_id = uuid4()

        with patch(
            "backend.app.agents.document_structuring.service.load_document_structuring_prompt",
            return_value="system prompt",
        ), patch(
            "backend.app.agents.document_structuring.service.get_document_structuring_prompt_snapshot",
            return_value=("backend/app/prompts/document_structuring_agent.txt", "hash-123"),
        ), patch(
            "backend.app.agents.document_structuring.service.get_llm_service_client",
            return_value=client,
        ) as get_client_mock:
            result = run_document_structuring_agent(
                source_text="  第一章  绪论\n页码 1  ",
                session=session,
                config_id=config_id,
                model=" custom-model ",
                request_id="req-1",
            )

        get_client_mock.assert_called_once_with(config_id=config_id, session=session)
        self.assertEqual(result.output_markdown, "# Structured\n\ncontent")
        self.assertEqual(result.model, "gpt-structured")
        self.assertEqual(result.request_id, "req-1")
        self.assertEqual(result.effective_config_id, client.config_id)
        self.assertEqual(result.effective_config_code, "default")
        self.assertEqual(result.prompt_path, "backend/app/prompts/document_structuring_agent.txt")
        self.assertEqual(result.prompt_hash, "hash-123")
        self.assertEqual(client.last_call["caller_service"], "backend.agent.document_structuring")
        self.assertEqual(client.last_call["system_prompt"], "system prompt")
        self.assertEqual(client.last_call["model"], "custom-model")
        self.assertEqual(client.last_call["temperature"], 0.1)
        self.assertIn("<<<SOURCE_TEXT>>>", client.last_call["prompt"])
        self.assertIn("第一章  绪论\n页码 1", client.last_call["prompt"])


class DocumentStructuringApiTests(TestCase):
    def setUp(self) -> None:
        load_document_structuring_prompt.cache_clear()
        self._tmpdir = Path("backend/tests/runtime_cases") / f"case-{uuid4().hex}"
        self._tmpdir.mkdir(parents=True, exist_ok=True)
        db_path = self._tmpdir / "backend-document-structuring-test.db"
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
        load_document_structuring_prompt.cache_clear()
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
            return list(session.exec(select(LlmChatRecord).order_by(LlmChatRecord.id.asc())).all())

    def _load_configs(self) -> list[LlmConfig]:
        with Session(self._engine) as session:
            return list(session.exec(select(LlmConfig).order_by(LlmConfig.created_at.asc())).all())

    def _create_config(
        self,
        *,
        code: str,
        name: str,
        enabled: bool = True,
        is_active: bool = False,
    ) -> LlmConfig:
        with Session(self._engine) as session:
            config = LlmConfig(
                code=code,
                name=name,
                provider=LlmConfigProvider.openai_compatible,
                base_url="https://provider.example.com/v1",
                api_key="provider-key",
                default_model="provider-model",
                timeout_seconds=45.0,
                enabled=enabled,
                is_active=is_active,
            )
            session.add(config)
            session.commit()
            session.refresh(config)
            return config

    def test_debug_run_returns_structured_markdown_and_persists_chat_record(self):
        self._start_client()

        with patch(
            "backend.app.services.llm_service.httpx.post",
            return_value=_ResponseStub(
                {
                    "id": "chatcmpl-structured",
                    "model": "gpt-test",
                    "choices": [{"message": {"content": "# Title\n\nNormalized content"}}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 8, "total_tokens": 13},
                },
                headers={"x-request-id": "req-upstream-1"},
            ),
        ):
            response = self.client.post(
                "/agents/document-structuring/debug-run",
                json={"source_text": "第一章 绪论\n1\n第一章 绪论"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["output_markdown"], "# Title\n\nNormalized content")
        self.assertEqual(payload["model"], "gpt-test")
        self.assertEqual(payload["usage"], {"prompt_tokens": 5, "completion_tokens": 8, "total_tokens": 13})
        self.assertEqual(payload["effective_config_code"], "default")
        self.assertTrue(payload["prompt_path"].endswith("document_structuring_agent.txt"))
        self.assertIsNotNone(payload["prompt_hash"])

        records = self._load_records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].caller_service, "backend.agent.document_structuring")
        self.assertEqual(records[0].response_text, "# Title\n\nNormalized content")

    def test_debug_run_returns_422_for_blank_source_text(self):
        self._start_client()

        response = self.client.post(
            "/agents/document-structuring/debug-run",
            json={"source_text": "   "},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"], "source_text is required")

    def test_debug_run_returns_404_for_missing_config(self):
        self._start_client()

        response = self.client.post(
            "/agents/document-structuring/debug-run",
            json={"source_text": "hello", "config_id": str(uuid4())},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "LLM config not found")

    def test_debug_run_returns_409_for_disabled_config(self):
        self._start_client()
        disabled_config = self._create_config(code="disabled-1", name="Disabled", enabled=False)

        response = self.client.post(
            "/agents/document-structuring/debug-run",
            json={"source_text": "hello", "config_id": str(disabled_config.id)},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "LLM config is disabled")

    def test_debug_run_returns_502_on_upstream_timeout(self):
        self._start_client()

        with patch(
            "backend.app.services.llm_service.httpx.post",
            side_effect=httpx.TimeoutException("timed out"),
        ):
            response = self.client.post(
                "/agents/document-structuring/debug-run",
                json={"source_text": "hello"},
            )

        self.assertEqual(response.status_code, 502)
        self.assertIn("Upstream timeout", response.json()["detail"])

    def test_debug_run_returns_500_when_prompt_is_missing(self):
        missing_path = self._tmpdir / "missing-document-structuring-prompt.txt"
        missing_path.unlink(missing_ok=True)

        with patch.dict(
            os.environ,
            {"DOCUMENT_STRUCTURING_AGENT_PROMPT_PATH": str(missing_path)},
            clear=False,
        ):
            load_document_structuring_prompt.cache_clear()
            self._start_client()
            response = self.client.post(
                "/agents/document-structuring/debug-run",
                json={"source_text": "hello"},
            )

        self.assertEqual(response.status_code, 500)
        self.assertIn("Prompt file not found", response.json()["detail"])

    def test_debug_run_returns_500_when_chat_persistence_fails(self):
        self._start_client()

        with patch(
            "backend.app.services.llm_service.httpx.post",
            return_value=_ResponseStub(
                {
                    "id": "chatcmpl-structured",
                    "model": "gpt-test",
                    "choices": [{"message": {"content": "# Title"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }
            ),
        ), patch(
            "backend.app.services.llm_service.persist_llm_chat_record",
            side_effect=LlmChatPersistenceError("chat persistence failed"),
        ):
            response = self.client.post(
                "/agents/document-structuring/debug-run",
                json={"source_text": "hello"},
            )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["detail"], "chat persistence failed")
