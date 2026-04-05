import os
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException
from minio.error import S3Error

from backend.app.api.routers import extracted_images
from backend.app.models import ExtractedImage
from backend.app.services import LlmImageUrlInputPart, LlmTextInputPart, LlmUsage
from backend.app.services.extracted_image_semantic_service import (
    ExtractedImageSemanticDescriptionResult,
    ExtractedImageSemanticPromptError,
    describe_extracted_image_semantics,
    load_extracted_image_semantic_prompt,
    resolve_extracted_image_semantic_model,
)
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
        text: str = "\u56fe\u7247\u63cf\u8ff0",
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


class ExtractedImageSemanticServiceTests(TestCase):
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

    def test_resolve_extracted_image_semantic_model_returns_none_when_unset(self):
        with patch.dict(os.environ, {}, clear=False):
            previous = os.environ.pop("EXTRACTED_IMAGE_SEMANTIC_MODEL", None)
            try:
                resolved = resolve_extracted_image_semantic_model(None)
            finally:
                if previous is not None:
                    os.environ["EXTRACTED_IMAGE_SEMANTIC_MODEL"] = previous

        self.assertIsNone(resolved)

    def test_load_prompt_reads_configured_file(self):
        prompt_path = self._write_temp_prompt_file("system prompt")

        with patch.dict(os.environ, {"EXTRACTED_IMAGE_SEMANTIC_PROMPT_PATH": str(prompt_path)}, clear=False):
            prompt = load_extracted_image_semantic_prompt()

        self.assertEqual(prompt, "system prompt")

    def test_load_prompt_raises_when_file_missing(self):
        missing_path = self._temp_root / "softplan-missing-semantic-prompt.txt"
        missing_path.unlink(missing_ok=True)

        with patch.dict(os.environ, {"EXTRACTED_IMAGE_SEMANTIC_PROMPT_PATH": str(missing_path)}, clear=False):
            with self.assertRaises(ExtractedImageSemanticPromptError) as ctx:
                load_extracted_image_semantic_prompt()

        self.assertIn("Prompt file not found", str(ctx.exception))

    def test_load_prompt_raises_when_file_empty(self):
        prompt_path = self._write_temp_prompt_file("   \n")

        with patch.dict(os.environ, {"EXTRACTED_IMAGE_SEMANTIC_PROMPT_PATH": str(prompt_path)}, clear=False):
            with self.assertRaises(ExtractedImageSemanticPromptError) as ctx:
                load_extracted_image_semantic_prompt()

        self.assertIn("Prompt file is empty", str(ctx.exception))

    def test_describe_extracted_image_semantics_uses_request_model_override(self):
        extracted_image = self._build_extracted_image()
        storage = _StorageStub(payload=b"png-bytes")
        client = _ClientStub(
            text="\u8fd9\u662f\u4e00\u5f20\u6d4b\u8bd5\u56fe\u7247",
            model="request-model",
            request_id="req-42",
        )

        with patch(
            "backend.app.services.extracted_image_semantic_service.load_extracted_image_semantic_prompt",
            return_value="system prompt",
        ), patch.dict(os.environ, {"EXTRACTED_IMAGE_SEMANTIC_MODEL": "env-model"}, clear=False):
            result = describe_extracted_image_semantics(
                extracted_image=extracted_image,
                storage=storage,
                client=client,
                request_id="req-42",
                model="request-model",
            )

        self.assertEqual(result.image_id, 1)
        self.assertEqual(result.description, "\u8fd9\u662f\u4e00\u5f20\u6d4b\u8bd5\u56fe\u7247")
        self.assertEqual(result.model, "request-model")
        self.assertEqual(result.request_id, "req-42")
        self.assertEqual(storage.calls, [{"storage_key": "images/hash-a.png", "bucket": "softplan"}])
        self.assertEqual(client.last_call["model"], "request-model")
        self.assertEqual(client.last_call["prompt"], "\u8bf7\u57fa\u4e8e\u8fd9\u5f20\u56fe\u7247\u751f\u6210\u4e00\u6bb5\u4e2d\u6587\u8bed\u4e49\u63cf\u8ff0\u3002")
        self.assertEqual(client.last_call["system_prompt"], "system prompt")
        self.assertIsInstance(client.last_call["input_parts"][0], LlmTextInputPart)
        self.assertIsInstance(client.last_call["input_parts"][1], LlmImageUrlInputPart)
        self.assertTrue(client.last_call["input_parts"][1].url.startswith("data:image/png;base64,"))

    def test_describe_extracted_image_semantics_falls_back_to_env_model(self):
        extracted_image = self._build_extracted_image()

        with patch(
            "backend.app.services.extracted_image_semantic_service.load_extracted_image_semantic_prompt",
            return_value="system prompt",
        ), patch.dict(os.environ, {"EXTRACTED_IMAGE_SEMANTIC_MODEL": "env-model"}, clear=False):
            client = _ClientStub(model="env-model")
            describe_extracted_image_semantics(
                extracted_image=extracted_image,
                storage=_StorageStub(payload=b"png-bytes"),
                client=client,
                model=None,
            )

        self.assertEqual(client.last_call["model"], "env-model")

    def test_describe_extracted_image_semantics_passes_none_when_no_model_is_configured(self):
        extracted_image = self._build_extracted_image()
        previous = os.environ.pop("EXTRACTED_IMAGE_SEMANTIC_MODEL", None)
        try:
            with patch(
                "backend.app.services.extracted_image_semantic_service.load_extracted_image_semantic_prompt",
                return_value="system prompt",
            ):
                client = _ClientStub(model="gpt-4o-mini")
                describe_extracted_image_semantics(
                    extracted_image=extracted_image,
                    storage=_StorageStub(payload=b"png-bytes"),
                    client=client,
                    model=None,
                )
        finally:
            if previous is not None:
                os.environ["EXTRACTED_IMAGE_SEMANTIC_MODEL"] = previous

        self.assertIsNone(client.last_call["model"])

    def test_describe_extracted_image_semantics_rejects_non_image_resource(self):
        extracted_image = self._build_extracted_image(content_type="application/pdf")

        with patch(
            "backend.app.services.extracted_image_semantic_service.load_extracted_image_semantic_prompt",
            return_value="system prompt",
        ):
            with self.assertRaises(HTTPException) as ctx:
                describe_extracted_image_semantics(
                    extracted_image=extracted_image,
                    storage=_StorageStub(payload=b"pdf-bytes"),
                    client=_ClientStub(),
                )

        self.assertEqual(ctx.exception.status_code, 422)

    def test_describe_extracted_image_semantics_returns_502_on_storage_failure(self):
        extracted_image = self._build_extracted_image()

        with patch(
            "backend.app.services.extracted_image_semantic_service.load_extracted_image_semantic_prompt",
            return_value="system prompt",
        ):
            with self.assertRaises(HTTPException) as ctx:
                describe_extracted_image_semantics(
                    extracted_image=extracted_image,
                    storage=_StorageStub(error=_MinioError("NoSuchKey")),
                    client=_ClientStub(),
                )

        self.assertEqual(ctx.exception.status_code, 502)
        self.assertIn("Extracted image storage download failed", ctx.exception.detail)

    def test_describe_extracted_image_semantics_returns_502_on_llm_error(self):
        extracted_image = self._build_extracted_image()

        with patch(
            "backend.app.services.extracted_image_semantic_service.load_extracted_image_semantic_prompt",
            return_value="system prompt",
        ):
            with self.assertRaises(HTTPException) as ctx:
                describe_extracted_image_semantics(
                    extracted_image=extracted_image,
                    storage=_StorageStub(payload=b"png-bytes"),
                    client=_ClientStub(error="upstream 400"),
                )

        self.assertEqual(ctx.exception.status_code, 502)
        self.assertIn("llm-service semantic description failed", ctx.exception.detail)


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

    def test_route_returns_semantic_description_payload(self):
        extracted_image = self._build_extracted_image()
        client = _ClientStub()
        storage = _StorageStub(payload=b"png-bytes")

        with patch.object(extracted_images, "get_extracted_image_or_404", return_value=extracted_image), patch.object(
            extracted_images,
            "describe_extracted_image_semantics",
            return_value=ExtractedImageSemanticDescriptionResult(
                image_id=1,
                description="\u4e2d\u6587\u63cf\u8ff0",
                model="gpt-4.1-mini",
                request_id="req-9",
            ),
        ) as describe_mock:
            response = extracted_images.generate_extracted_image_semantic_description(
                image_id=1,
                payload=extracted_images.ExtractedImageSemanticDescriptionRequest(request_id="req-9", model="request-model"),
                session=object(),
                storage=storage,
                client=client,
            )

        self.assertEqual(response.image_id, 1)
        self.assertEqual(response.description, "\u4e2d\u6587\u63cf\u8ff0")
        self.assertEqual(response.model, "gpt-4.1-mini")
        self.assertEqual(response.request_id, "req-9")
        self.assertEqual(describe_mock.call_args.kwargs["request_id"], "req-9")
        self.assertEqual(describe_mock.call_args.kwargs["model"], "request-model")

    def test_route_passes_none_model_when_request_does_not_override(self):
        extracted_image = self._build_extracted_image()

        with patch.object(extracted_images, "get_extracted_image_or_404", return_value=extracted_image), patch.object(
            extracted_images,
            "describe_extracted_image_semantics",
            return_value=ExtractedImageSemanticDescriptionResult(
                image_id=1,
                description="\u4e2d\u6587\u63cf\u8ff0",
                model="env-model",
                request_id="req-11",
            ),
        ) as describe_mock:
            extracted_images.generate_extracted_image_semantic_description(
                image_id=1,
                payload=extracted_images.ExtractedImageSemanticDescriptionRequest(request_id="req-11"),
                session=object(),
                storage=_StorageStub(payload=b"png-bytes"),
                client=_ClientStub(),
            )

        self.assertIsNone(describe_mock.call_args.kwargs["model"])

    def test_route_returns_503_when_prompt_unavailable(self):
        extracted_image = self._build_extracted_image()

        with patch.object(extracted_images, "get_extracted_image_or_404", return_value=extracted_image), patch.object(
            extracted_images,
            "describe_extracted_image_semantics",
            side_effect=ExtractedImageSemanticPromptError("Prompt file not found"),
        ):
            with self.assertRaises(HTTPException) as ctx:
                extracted_images.generate_extracted_image_semantic_description(
                    image_id=1,
                    payload=extracted_images.ExtractedImageSemanticDescriptionRequest(request_id="req-10", model="request-model"),
                    session=object(),
                    storage=_StorageStub(payload=b"png-bytes"),
                    client=_ClientStub(),
                )

        self.assertEqual(ctx.exception.status_code, 503)
        self.assertIn("Semantic description prompt unavailable", ctx.exception.detail)

    def test_route_returns_404_when_image_missing(self):
        session = _SessionCapture(first_value=None)

        with self.assertRaises(HTTPException) as ctx:
            extracted_images.generate_extracted_image_semantic_description(
                image_id=999,
                payload=None,
                session=session,
                storage=_StorageStub(payload=b"png-bytes"),
                client=_ClientStub(),
            )

        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(ctx.exception.detail, "Extracted image not found")
        self.assertIn("extracted_images.id", str(session.last_statement.whereclause))
