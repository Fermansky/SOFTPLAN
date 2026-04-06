import os
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException
from minio.error import S3Error

from backend.app.api.routers import extracted_images
from backend.app.models import ExtractedImage, ExtractedImageSemanticTask, ExtractedImageSemanticTaskStatus
from backend.app.services import LlmImageUrlInputPart, LlmTextInputPart, LlmUsage
from backend.app.services.extracted_image_semantic_service import (
    ExtractedImageSemanticPromptError,
    execute_extracted_image_semantic_recognition,
    get_extracted_image_semantic_target_model_key,
    load_extracted_image_semantic_prompt,
    resolve_extracted_image_semantic_model,
)
from backend.app.services.extracted_image_semantic_task_service import ExtractedImageSemanticTaskSubmissionResult
from backend.app.services.llm_service import LlmChatResult


class _ExecResult:
    def __init__(self, first_value=None):
        self._first_value = first_value

    def first(self):
        return self._first_value


class _SessionCapture:
    def __init__(self, *, first_value=None):
        self.first_value = first_value
        self.last_statement = None

    def exec(self, statement):
        self.last_statement = statement
        return _ExecResult(first_value=self.first_value)


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


class _ClientStub:
    def __init__(
        self,
        *,
        text: str = "image description",
        model: str = "gpt-4o-mini",
        request_id: str | None = "req-1",
        error: str | None = None,
    ):
        self.text = text
        self.model = model
        self.request_id = request_id
        self.error = error
        self.last_call = None

    def chat(self, **kwargs):
        self.last_call = kwargs
        if self.error is not None:
            return None, self.error
        return (
            LlmChatResult(
                text=self.text,
                model=self.model,
                usage=LlmUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
                request_id=kwargs.get("request_id", self.request_id),
            ),
            None,
        )


class _MinioError(S3Error):
    def __init__(self, code: str):
        self._code = code

    @property
    def code(self) -> str:
        return self._code


class ExtractedImageSemanticExecutionTests(TestCase):
    def setUp(self) -> None:
        load_extracted_image_semantic_prompt.cache_clear()
        self._temp_root = Path(os.getcwd()) / "backend" / "tests" / ".tmp"
        self._temp_root.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        load_extracted_image_semantic_prompt.cache_clear()

    def _build_extracted_image(self, *, content_type: str = "image/png") -> ExtractedImage:
        return ExtractedImage(
            id=1,
            file_hash="a" * 64,
            storage_bucket="softplan",
            storage_key="images/hash-a.png",
            file_size=123,
            content_type=content_type,
            extension=".png",
            width=100,
            height=200,
        )

    def _write_temp_prompt_file(self, contents: str) -> Path:
        prompt_path = self._temp_root / f"semantic-{uuid4().hex}.txt"
        prompt_path.write_text(contents, encoding="utf-8")
        self.addCleanup(lambda: prompt_path.unlink(missing_ok=True))
        return prompt_path

    def test_resolve_extracted_image_semantic_model_prefers_request_value(self):
        with patch.dict(os.environ, {"EXTRACTED_IMAGE_SEMANTIC_MODEL": "env-model"}, clear=False):
            resolved = resolve_extracted_image_semantic_model("request-model")

        self.assertEqual(resolved, "request-model")

    def test_resolve_extracted_image_semantic_model_falls_back_to_env(self):
        with patch.dict(os.environ, {"EXTRACTED_IMAGE_SEMANTIC_MODEL": "env-model"}, clear=False):
            resolved = resolve_extracted_image_semantic_model("   ")

        self.assertEqual(resolved, "env-model")

    def test_target_model_key_uses_default_sentinel(self):
        self.assertEqual(get_extracted_image_semantic_target_model_key(None), "__LLM_SERVICE_DEFAULT__")
        self.assertEqual(get_extracted_image_semantic_target_model_key("gpt-test"), "gpt-test")

    def test_load_prompt_reads_configured_file(self):
        prompt_path = self._write_temp_prompt_file("system prompt")

        with patch.dict(os.environ, {"EXTRACTED_IMAGE_SEMANTIC_PROMPT_PATH": str(prompt_path)}, clear=False):
            prompt = load_extracted_image_semantic_prompt()

        self.assertEqual(prompt, "system prompt")

    def test_load_prompt_raises_when_file_missing(self):
        missing_path = self._temp_root / "softplan-missing-semantic-prompt.txt"
        missing_path.unlink(missing_ok=True)

        with patch.dict(os.environ, {"EXTRACTED_IMAGE_SEMANTIC_PROMPT_PATH": str(missing_path)}, clear=False):
            with self.assertRaises(ExtractedImageSemanticPromptError):
                load_extracted_image_semantic_prompt()

    def test_execute_recognition_succeeds(self):
        extracted_image = self._build_extracted_image()
        storage = _StorageStub(payload=b"png-bytes")
        client = _ClientStub(text="test image description", model="request-model", request_id="req-42")

        with patch(
            "backend.app.services.extracted_image_semantic_service.load_extracted_image_semantic_prompt",
            return_value="system prompt",
        ):
            result = execute_extracted_image_semantic_recognition(
                extracted_image=extracted_image,
                storage=storage,
                client=client,
                request_id="req-42",
                target_model="request-model",
            )

        self.assertTrue(result.succeeded)
        self.assertEqual(result.description, "test image description")
        self.assertEqual(result.result_model, "request-model")
        self.assertIsNone(result.error_message)
        self.assertEqual(storage.calls, [{"storage_key": "images/hash-a.png", "bucket": "softplan"}])
        self.assertEqual(client.last_call["model"], "request-model")
        self.assertEqual(client.last_call["prompt"], "请基于这张图片生成一段中文语义描述。")
        self.assertEqual(client.last_call["system_prompt"], "system prompt")
        self.assertIsInstance(client.last_call["input_parts"][0], LlmTextInputPart)
        self.assertIsInstance(client.last_call["input_parts"][1], LlmImageUrlInputPart)
        self.assertTrue(client.last_call["input_parts"][1].url.startswith("data:image/png;base64,"))

    def test_execute_recognition_fails_for_non_image(self):
        result = execute_extracted_image_semantic_recognition(
            extracted_image=self._build_extracted_image(content_type="application/pdf"),
            storage=_StorageStub(payload=b"pdf-bytes"),
            client=_ClientStub(),
        )

        self.assertFalse(result.succeeded)
        self.assertIn("is not an image resource", result.error_message or "")

    def test_execute_recognition_fails_on_storage_error(self):
        extracted_image = self._build_extracted_image()

        with patch(
            "backend.app.services.extracted_image_semantic_service.load_extracted_image_semantic_prompt",
            return_value="system prompt",
        ):
            result = execute_extracted_image_semantic_recognition(
                extracted_image=extracted_image,
                storage=_StorageStub(error=_MinioError("NoSuchKey")),
                client=_ClientStub(),
            )

        self.assertFalse(result.succeeded)
        self.assertIn("Extracted image storage download failed", result.error_message or "")

    def test_execute_recognition_fails_on_llm_error(self):
        extracted_image = self._build_extracted_image()

        with patch(
            "backend.app.services.extracted_image_semantic_service.load_extracted_image_semantic_prompt",
            return_value="system prompt",
        ):
            result = execute_extracted_image_semantic_recognition(
                extracted_image=extracted_image,
                storage=_StorageStub(payload=b"png-bytes"),
                client=_ClientStub(error="upstream 400"),
            )

        self.assertFalse(result.succeeded)
        self.assertIn("llm-service semantic description failed", result.error_message or "")


class ExtractedImageSemanticRouteTests(TestCase):
    def _build_extracted_image(self) -> ExtractedImage:
        return ExtractedImage(
            id=1,
            file_hash="a" * 64,
            storage_bucket="softplan",
            storage_key="images/hash-a.png",
            file_size=123,
            content_type="image/png",
            extension=".png",
            width=100,
            height=200,
        )

    def _build_task(
        self,
        *,
        status: ExtractedImageSemanticTaskStatus = ExtractedImageSemanticTaskStatus.pending,
        overwrite_existing_snapshot: bool = False,
    ) -> ExtractedImageSemanticTask:
        return ExtractedImageSemanticTask(
            id=uuid4(),
            extracted_image_id=1,
            status=status,
            requested_model="request-model",
            target_model="request-model",
            target_model_key="request-model",
            overwrite_existing_snapshot=overwrite_existing_snapshot,
            result_model="request-model" if status == ExtractedImageSemanticTaskStatus.succeeded else None,
            request_id="req-9",
            prompt_path="backend/app/prompts/extracted_image_semantic.txt",
            prompt_hash="abc123",
            description="中文描述" if status == ExtractedImageSemanticTaskStatus.succeeded else None,
            error_message="boom" if status == ExtractedImageSemanticTaskStatus.failed else None,
            attempt_count=1,
        )

    def test_create_task_route_uses_default_overwrite_false(self):
        extracted_image = self._build_extracted_image()
        task = self._build_task(overwrite_existing_snapshot=False)

        with patch.object(extracted_images, "get_extracted_image_or_404", return_value=extracted_image), patch.object(
            extracted_images,
            "create_or_reuse_extracted_image_semantic_task",
            return_value=ExtractedImageSemanticTaskSubmissionResult(task=task, reused=True),
        ) as create_mock:
            response = extracted_images.create_extracted_image_semantic_task(
                image_id=1,
                payload=extracted_images.ExtractedImageSemanticTaskCreateRequest(request_id="req-9", model="request-model"),
                session=object(),
            )

        self.assertEqual(response.image_id, 1)
        self.assertEqual(response.status, ExtractedImageSemanticTaskStatus.pending)
        self.assertTrue(response.reused)
        self.assertFalse(response.overwrite_existing_snapshot)
        self.assertEqual(create_mock.call_args.kwargs["requested_model"], "request-model")
        self.assertEqual(create_mock.call_args.kwargs["request_id"], "req-9")
        self.assertFalse(create_mock.call_args.kwargs["overwrite_existing_snapshot"])

    def test_create_task_route_passes_overwrite_true(self):
        extracted_image = self._build_extracted_image()
        task = self._build_task(overwrite_existing_snapshot=True)

        with patch.object(extracted_images, "get_extracted_image_or_404", return_value=extracted_image), patch.object(
            extracted_images,
            "create_or_reuse_extracted_image_semantic_task",
            return_value=ExtractedImageSemanticTaskSubmissionResult(task=task, reused=False),
        ) as create_mock:
            response = extracted_images.create_extracted_image_semantic_task(
                image_id=1,
                payload=extracted_images.ExtractedImageSemanticTaskCreateRequest(
                    request_id="req-10",
                    model="request-model",
                    overwrite_existing_snapshot=True,
                ),
                session=object(),
            )

        self.assertTrue(response.overwrite_existing_snapshot)
        self.assertTrue(create_mock.call_args.kwargs["overwrite_existing_snapshot"])

    def test_get_task_route_returns_404_when_missing(self):
        with patch.object(extracted_images, "get_extracted_image_semantic_task_by_id", return_value=None):
            with self.assertRaises(HTTPException) as ctx:
                extracted_images.get_extracted_image_semantic_task(uuid4(), session=object())

        self.assertEqual(ctx.exception.status_code, 404)

    def test_get_latest_result_returns_no_task(self):
        extracted_image = self._build_extracted_image()

        with patch.object(extracted_images, "get_extracted_image_or_404", return_value=extracted_image), patch.object(
            extracted_images,
            "get_latest_extracted_image_semantic_task_for_image",
            return_value=None,
        ):
            response = extracted_images.get_extracted_image_semantic_result(image_id=1, session=object())

        self.assertEqual(response.image_id, 1)
        self.assertEqual(response.status, extracted_images.ExtractedImageSemanticResultStatus.no_task)
        self.assertIsNone(response.task_id)

    def test_get_latest_result_returns_succeeded_payload(self):
        extracted_image = self._build_extracted_image()
        task = self._build_task(status=ExtractedImageSemanticTaskStatus.succeeded)

        with patch.object(extracted_images, "get_extracted_image_or_404", return_value=extracted_image), patch.object(
            extracted_images,
            "get_latest_extracted_image_semantic_task_for_image",
            return_value=task,
        ):
            response = extracted_images.get_extracted_image_semantic_result(image_id=1, session=object())

        self.assertEqual(response.status, extracted_images.ExtractedImageSemanticResultStatus.succeeded)
        self.assertEqual(response.description, "中文描述")
        self.assertEqual(response.result_model, "request-model")

    def test_route_returns_404_when_image_missing(self):
        session = _SessionCapture(first_value=None)

        with self.assertRaises(HTTPException) as ctx:
            extracted_images.get_extracted_image_semantic_result(
                image_id=999,
                session=session,
            )

        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(ctx.exception.detail, "Extracted image not found")
        self.assertIn("extracted_images.id", str(session.last_statement.whereclause))