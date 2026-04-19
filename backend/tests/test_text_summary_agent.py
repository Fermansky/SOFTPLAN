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
from backend.app.agents.text_summary import (
    TextSummaryAgentError,
    TextSummaryPromptError,
    load_text_summary_prompt,
    run_text_summary_agent,
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
    def __init__(self, *, text: str, config_id=None, config_code=None):
        self.text = text
        self.config_id = config_id
        self.config_code = config_code
        self.last_call = None

    def chat(self, **kwargs):
        self.last_call = kwargs
        return (
            LlmChatResult(
                text=self.text,
                model="gpt-summary",
                usage=LlmUsage(prompt_tokens=7, completion_tokens=9, total_tokens=16),
                request_id=kwargs.get("request_id", "req-summary"),
            ),
            None,
        )


class TextSummaryPromptTests(TestCase):
    def setUp(self) -> None:
        load_text_summary_prompt.cache_clear()
        self._temp_root = Path(os.getcwd()) / "backend" / "tests" / ".tmp"
        self._temp_root.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        load_text_summary_prompt.cache_clear()

    def _write_prompt_file(self, contents: str) -> Path:
        prompt_path = self._temp_root / f"text-summary-{uuid4().hex}.txt"
        prompt_path.write_text(contents, encoding="utf-8")
        self.addCleanup(lambda: prompt_path.unlink(missing_ok=True))
        return prompt_path

    def test_load_prompt_reads_configured_file(self):
        prompt_path = self._write_prompt_file("summary system prompt")

        with patch.dict(os.environ, {"DOCUMENT_TEXT_SUMMARY_PROMPT_PATH": str(prompt_path)}, clear=False):
            prompt = load_text_summary_prompt()

        self.assertEqual(prompt, "summary system prompt")

    def test_load_prompt_raises_when_file_missing(self):
        missing_path = self._temp_root / "missing-text-summary-prompt.txt"
        missing_path.unlink(missing_ok=True)

        with patch.dict(os.environ, {"DOCUMENT_TEXT_SUMMARY_PROMPT_PATH": str(missing_path)}, clear=False):
            with self.assertRaises(TextSummaryPromptError):
                load_text_summary_prompt()

    def test_load_prompt_raises_when_file_empty(self):
        prompt_path = self._write_prompt_file("   \n")

        with patch.dict(os.environ, {"DOCUMENT_TEXT_SUMMARY_PROMPT_PATH": str(prompt_path)}, clear=False):
            with self.assertRaises(TextSummaryPromptError):
                load_text_summary_prompt()


class TextSummaryServiceTests(TestCase):
    def tearDown(self) -> None:
        load_text_summary_prompt.cache_clear()

    def test_run_rejects_blank_source_text(self):
        with self.assertRaises(TextSummaryAgentError) as ctx:
            run_text_summary_agent(source_text="   ", session=object())

        self.assertEqual(str(ctx.exception), "source_text is required")

    def test_run_success_builds_expected_llm_call(self):
        client = _ClientStub(
            text='{"title":"示例标题","summary":"这里是摘要。"}',
            config_id=uuid4(),
            config_code="default",
        )
        session = object()
        config_id = uuid4()

        with patch(
            "backend.app.agents.text_summary.service.load_text_summary_prompt",
            return_value="summary system prompt",
        ), patch(
            "backend.app.agents.text_summary.service.get_text_summary_prompt_snapshot",
            return_value=("backend/app/prompts/text_summary_agent.txt", "hash-789"),
        ), patch(
            "backend.app.agents.text_summary.service.get_llm_service_client",
            return_value=client,
        ) as get_client_mock:
            result = run_text_summary_agent(
                source_text="  原始文本内容  ",
                session=session,
                config_id=config_id,
                model=" custom-model ",
                request_id="req-1",
            )

        get_client_mock.assert_called_once_with(config_id=config_id, session=session)
        self.assertEqual(result.title, "示例标题")
        self.assertEqual(result.summary, "这里是摘要。")
        self.assertEqual(result.model, "gpt-summary")
        self.assertEqual(result.request_id, "req-1")
        self.assertEqual(result.effective_config_id, client.config_id)
        self.assertEqual(result.effective_config_code, "default")
        self.assertEqual(result.prompt_path, "backend/app/prompts/text_summary_agent.txt")
        self.assertEqual(result.prompt_hash, "hash-789")
        self.assertEqual(client.last_call["caller_service"], "backend.agent.text_summary")
        self.assertEqual(client.last_call["system_prompt"], "summary system prompt")
        self.assertEqual(client.last_call["model"], "custom-model")
        self.assertEqual(client.last_call["temperature"], 0.1)
        self.assertIn("<<<SOURCE_TEXT>>>", client.last_call["prompt"])
        self.assertIn("原始文本内容", client.last_call["prompt"])

    def test_run_parses_json_code_fence(self):
        client = _ClientStub(
            text='''```json\n{"title":"围栏标题","summary":"围栏摘要。"}\n```''',
            config_id=uuid4(),
            config_code="default",
        )

        with patch(
            "backend.app.agents.text_summary.service.load_text_summary_prompt",
            return_value="summary system prompt",
        ), patch(
            "backend.app.agents.text_summary.service.get_text_summary_prompt_snapshot",
            return_value=("backend/app/prompts/text_summary_agent.txt", "hash-789"),
        ), patch(
            "backend.app.agents.text_summary.service.get_llm_service_client",
            return_value=client,
        ):
            result = run_text_summary_agent(source_text="内容", session=object())

        self.assertEqual(result.title, "围栏标题")
        self.assertEqual(result.summary, "围栏摘要。")

    def test_run_rejects_missing_title(self):
        client = _ClientStub(text='{"summary":"只有摘要"}')

        with patch(
            "backend.app.agents.text_summary.service.load_text_summary_prompt",
            return_value="summary system prompt",
        ), patch(
            "backend.app.agents.text_summary.service.get_text_summary_prompt_snapshot",
            return_value=("backend/app/prompts/text_summary_agent.txt", "hash-789"),
        ), patch(
            "backend.app.agents.text_summary.service.get_llm_service_client",
            return_value=client,
        ):
            with self.assertRaises(TextSummaryAgentError) as ctx:
                run_text_summary_agent(source_text="内容", session=object())

        self.assertIn("invalid field: title", str(ctx.exception))

    def test_run_rejects_missing_summary(self):
        client = _ClientStub(text='{"title":"只有标题"}')

        with patch(
            "backend.app.agents.text_summary.service.load_text_summary_prompt",
            return_value="summary system prompt",
        ), patch(
            "backend.app.agents.text_summary.service.get_text_summary_prompt_snapshot",
            return_value=("backend/app/prompts/text_summary_agent.txt", "hash-789"),
        ), patch(
            "backend.app.agents.text_summary.service.get_llm_service_client",
            return_value=client,
        ):
            with self.assertRaises(TextSummaryAgentError) as ctx:
                run_text_summary_agent(source_text="内容", session=object())

        self.assertIn("invalid field: summary", str(ctx.exception))

    def test_run_rejects_non_string_field(self):
        client = _ClientStub(text='{"title":"标题","summary":123}')

        with patch(
            "backend.app.agents.text_summary.service.load_text_summary_prompt",
            return_value="summary system prompt",
        ), patch(
            "backend.app.agents.text_summary.service.get_text_summary_prompt_snapshot",
            return_value=("backend/app/prompts/text_summary_agent.txt", "hash-789"),
        ), patch(
            "backend.app.agents.text_summary.service.get_llm_service_client",
            return_value=client,
        ):
            with self.assertRaises(TextSummaryAgentError) as ctx:
                run_text_summary_agent(source_text="内容", session=object())

        self.assertIn("invalid field: summary", str(ctx.exception))

    def test_run_rejects_overlong_title(self):
        client = _ClientStub(text='{"title":"' + ("长" * 61) + '","summary":"摘要"}')

        with patch(
            "backend.app.agents.text_summary.service.load_text_summary_prompt",
            return_value="summary system prompt",
        ), patch(
            "backend.app.agents.text_summary.service.get_text_summary_prompt_snapshot",
            return_value=("backend/app/prompts/text_summary_agent.txt", "hash-789"),
        ), patch(
            "backend.app.agents.text_summary.service.get_llm_service_client",
            return_value=client,
        ):
            with self.assertRaises(TextSummaryAgentError) as ctx:
                run_text_summary_agent(source_text="内容", session=object())

        self.assertIn("title>60", str(ctx.exception))

    def test_run_rejects_overlong_summary(self):
        client = _ClientStub(text='{"title":"标题","summary":"' + ("摘" * 201) + '"}')

        with patch(
            "backend.app.agents.text_summary.service.load_text_summary_prompt",
            return_value="summary system prompt",
        ), patch(
            "backend.app.agents.text_summary.service.get_text_summary_prompt_snapshot",
            return_value=("backend/app/prompts/text_summary_agent.txt", "hash-789"),
        ), patch(
            "backend.app.agents.text_summary.service.get_llm_service_client",
            return_value=client,
        ):
            with self.assertRaises(TextSummaryAgentError) as ctx:
                run_text_summary_agent(source_text="内容", session=object())

        self.assertIn("summary>200", str(ctx.exception))


class TextSummaryApiTests(TestCase):
    def setUp(self) -> None:
        load_text_summary_prompt.cache_clear()
        self._tmpdir = Path("backend/tests/runtime_cases") / f"case-{uuid4().hex}"
        self._tmpdir.mkdir(parents=True, exist_ok=True)
        db_path = self._tmpdir / "backend-text-summary-test.db"
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
        load_text_summary_prompt.cache_clear()
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

    def _create_config(self, *, code: str, name: str, enabled: bool = True, is_active: bool = False) -> LlmConfig:
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

    def test_debug_run_returns_title_and_summary_and_persists_chat_record(self):
        self._start_client()

        with patch(
            "backend.app.services.llm_service.httpx.post",
            return_value=_ResponseStub(
                {
                    "id": "chatcmpl-summary",
                    "model": "gpt-test",
                    "choices": [{"message": {"content": '{"title":"测试标题","summary":"测试摘要。"}'}}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 8, "total_tokens": 13},
                },
                headers={"x-request-id": "req-upstream-1"},
            ),
        ):
            response = self.client.post(
                "/agents/text-summary/debug-run",
                json={"source_text": "这是一段需要总结的文本。"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["title"], "测试标题")
        self.assertEqual(payload["summary"], "测试摘要。")
        self.assertEqual(payload["model"], "gpt-test")
        self.assertEqual(payload["usage"], {"prompt_tokens": 5, "completion_tokens": 8, "total_tokens": 13})
        self.assertEqual(payload["effective_config_code"], "default")
        self.assertTrue(payload["prompt_path"].endswith("text_summary_agent.txt"))
        self.assertIsNotNone(payload["prompt_hash"])

        records = self._load_records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].caller_service, "backend.agent.text_summary")
        self.assertEqual(records[0].response_text, '{"title":"测试标题","summary":"测试摘要。"}')

    def test_debug_run_returns_422_for_blank_source_text(self):
        self._start_client()

        response = self.client.post("/agents/text-summary/debug-run", json={"source_text": "   "})

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"], "source_text is required")

    def test_debug_run_returns_404_for_missing_config(self):
        self._start_client()

        response = self.client.post(
            "/agents/text-summary/debug-run",
            json={"source_text": "hello", "config_id": str(uuid4())},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "LLM config not found")

    def test_debug_run_returns_409_for_disabled_config(self):
        self._start_client()
        disabled_config = self._create_config(code="disabled-summary", name="Disabled", enabled=False)

        response = self.client.post(
            "/agents/text-summary/debug-run",
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
            response = self.client.post("/agents/text-summary/debug-run", json={"source_text": "hello"})

        self.assertEqual(response.status_code, 502)
        self.assertIn("Upstream timeout", response.json()["detail"])

    def test_debug_run_returns_502_on_invalid_json(self):
        self._start_client()

        with patch(
            "backend.app.services.llm_service.httpx.post",
            return_value=_ResponseStub(
                {
                    "id": "chatcmpl-summary",
                    "model": "gpt-test",
                    "choices": [{"message": {"content": "not json"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }
            ),
        ):
            response = self.client.post("/agents/text-summary/debug-run", json={"source_text": "hello"})

        self.assertEqual(response.status_code, 502)
        self.assertIn("invalid json", response.json()["detail"])

    def test_debug_run_returns_500_when_prompt_is_missing(self):
        missing_path = self._tmpdir / "missing-text-summary-prompt.txt"
        missing_path.unlink(missing_ok=True)

        with patch.dict(
            os.environ,
            {"DOCUMENT_TEXT_SUMMARY_PROMPT_PATH": str(missing_path)},
            clear=False,
        ):
            load_text_summary_prompt.cache_clear()
            self._start_client()
            response = self.client.post("/agents/text-summary/debug-run", json={"source_text": "hello"})

        self.assertEqual(response.status_code, 500)
        self.assertIn("Prompt file not found", response.json()["detail"])

    def test_debug_run_returns_500_when_chat_persistence_fails(self):
        self._start_client()

        with patch(
            "backend.app.services.llm_service.httpx.post",
            return_value=_ResponseStub(
                {
                    "id": "chatcmpl-summary",
                    "model": "gpt-test",
                    "choices": [{"message": {"content": '{"title":"标题","summary":"摘要"}'}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }
            ),
        ), patch(
            "backend.app.services.llm_service.persist_llm_chat_record",
            side_effect=LlmChatPersistenceError("chat persistence failed"),
        ):
            response = self.client.post("/agents/text-summary/debug-run", json={"source_text": "hello"})

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["detail"], "chat persistence failed")
