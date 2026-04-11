from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

import httpx
from fastapi import HTTPException

from backend.app.api.routers import document_parsing, layout_analysis
from backend.app.models import (
    Document,
    DocumentParsingImageItem,
    DocumentParsingImageItemResultSource,
    DocumentParsingImageItemStatus,
    DocumentParsingTask,
    DocumentParsingTaskStatus,
    FileRecord,
    LayoutAnalysisTask,
    LayoutAnalysisTaskStatus,
)
from backend.app.services.file_convert_service import FileConvertServiceClient


class _ResponseStub:
    def __init__(self, payload: dict[str, object], status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "error",
                request=httpx.Request("GET", "http://file-convert-service:8000/health"),
                response=httpx.Response(self.status_code),
            )

    def json(self):
        return self._payload


class _ClientStub:
    def __init__(self, *, available: bool = True, availability_error: str | None = None):
        self.available = available
        self.availability_error = availability_error

    def check_availability(self) -> tuple[bool, str | None]:
        return self.available, self.availability_error


class _SessionStub:
    def __init__(self):
        self.rolled_back = False
        self.layout_task = None

    def rollback(self) -> None:
        self.rolled_back = True

    def get(self, model, item_id):
        if self.layout_task is not None and model is LayoutAnalysisTask and self.layout_task.id == item_id:
            return self.layout_task
        return None


class RouterTests(TestCase):
    def _build_pdf_document_and_file(self) -> tuple[Document, FileRecord]:
        document = Document(project_id=uuid4(), file_id=uuid4(), name="PRD")
        file_record = FileRecord(
            file_hash="hash",
            storage_bucket="softplan",
            storage_key="documents/2026/04/a.pdf",
            file_size=10,
            content_type="application/pdf",
            extension=".pdf",
        )
        return document, file_record

    def test_get_document_parsing_availability_available_true(self):
        response = document_parsing.get_document_parsing_availability(client=_ClientStub(available=True))

        self.assertTrue(response.available)
        self.assertEqual(response.service, "file-convert-service")
        self.assertEqual(response.health_path, "/health")
        self.assertIsNone(response.error)

    def test_create_document_parsing_task_returns_aggregate_fields(self):
        document, file_record = self._build_pdf_document_and_file()
        session = _SessionStub()
        config_id = uuid4()
        layout_task = LayoutAnalysisTask(
            id=uuid4(),
            document_id=document.id,
            file_id=file_record.id,
            storage_bucket=file_record.storage_bucket,
            storage_key=file_record.storage_key,
            requested_layout_model="marker",
            target_layout_model="marker",
            layout_model_key="marker",
            status=LayoutAnalysisTaskStatus.succeeded,
            markdown="# parsed",
            image_hashes={"img-1": "a" * 64},
        )
        session.layout_task = layout_task
        task = DocumentParsingTask(
            document_id=document.id,
            file_id=file_record.id,
            storage_bucket=file_record.storage_bucket,
            storage_key=file_record.storage_key,
            requested_layout_model="marker",
            target_layout_model="marker",
            layout_model_key="marker",
            requested_image_model="vision-model",
            target_image_model="vision-model",
            image_model_key="vision-model",
            image_llm_config_id=config_id,
            image_llm_config_code="vision-config",
            image_llm_config_key=str(config_id),
            force_image_semantic_recognition=True,
            layout_task_id=layout_task.id,
            status=DocumentParsingTaskStatus.running,
            markdown="# parsed",
            image_hashes={"img-1": "a" * 64},
            image_total_count=1,
            image_succeeded_count=0,
            image_failed_count=0,
        )
        image_item = DocumentParsingImageItem(
            id=1,
            document_parsing_task_id=task.id,
            source_key="img-1",
            file_hash="a" * 64,
            extracted_image_id=1,
            semantic_task_id=uuid4(),
            status=DocumentParsingImageItemStatus.pending,
            result_source=DocumentParsingImageItemResultSource.submitted_semantic_task,
        )

        with patch.object(document_parsing, "get_active_document_or_404", return_value=document), patch.object(
            document_parsing,
            "get_file_or_404",
            return_value=file_record,
        ), patch.object(
            document_parsing,
            "create_or_reuse_document_parsing_task",
            return_value=SimpleNamespace(task=task, reused=False),
        ) as create_mock, patch.object(
            document_parsing,
            "get_layout_task_for_document_parsing_task",
            return_value=layout_task,
        ), patch.object(document_parsing, "get_document_parsing_image_items", return_value=[image_item]), patch.object(
            document_parsing,
            "get_document_parsing_image_semantic_result",
            return_value=document_parsing.DocumentParsingImageSemanticResult(
                description="semantic text",
                result_model="vision-model",
                source_task_id=uuid4(),
                updated_at=datetime.now(timezone.utc),
            ),
        ):
            response = document_parsing.create_document_parsing_task(
                payload=document_parsing.DocumentParsingTaskCreateRequest(
                    document_id=document.id,
                    layout_model="marker",
                    image_model="vision-model",
                    image_llm_config_id=config_id,
                    force_image_semantic_recognition=True,
                ),
                session=session,
            )

        self.assertEqual(response.target_layout_model, "marker")
        self.assertEqual(response.target_image_model, "vision-model")
        self.assertEqual(response.image_llm_config_id, config_id)
        self.assertEqual(response.image_llm_config_code, "vision-config")
        self.assertTrue(response.force_image_semantic_recognition)
        self.assertEqual(response.layout_status, LayoutAnalysisTaskStatus.succeeded)
        self.assertEqual(response.image_analysis_status, document_parsing.DocumentParsingImageAnalysisStatus.running)
        self.assertEqual(response.markdown, "# parsed")
        self.assertEqual(len(response.image_items), 1)
        self.assertEqual(response.image_items[0].semantic.description, "semantic text")
        self.assertEqual(create_mock.call_args.kwargs["image_llm_config_id"], config_id)
        self.assertTrue(create_mock.call_args.kwargs["force_image_semantic_recognition"])

    def test_get_document_parsing_document_result_returns_latest_succeeded_task_with_image_semantic(self):
        document, file_record = self._build_pdf_document_and_file()
        session = _SessionStub()
        layout_task = LayoutAnalysisTask(
            id=uuid4(),
            document_id=document.id,
            file_id=file_record.id,
            storage_bucket=file_record.storage_bucket,
            storage_key=file_record.storage_key,
            requested_layout_model="marker",
            target_layout_model="marker",
            layout_model_key="marker",
            status=LayoutAnalysisTaskStatus.succeeded,
            markdown="# parsed",
            image_hashes={"img-1": "a" * 64},
        )
        session.layout_task = layout_task
        task = DocumentParsingTask(
            document_id=document.id,
            file_id=file_record.id,
            storage_bucket=file_record.storage_bucket,
            storage_key=file_record.storage_key,
            requested_layout_model="marker",
            target_layout_model="marker",
            layout_model_key="marker",
            requested_image_model="vision-model",
            target_image_model="vision-model",
            image_model_key="vision-model",
            layout_task_id=layout_task.id,
            status=DocumentParsingTaskStatus.succeeded,
            markdown="# parsed",
            image_hashes={"img-1": "a" * 64},
            image_total_count=1,
            image_succeeded_count=1,
            image_failed_count=0,
        )
        image_item = DocumentParsingImageItem(
            id=1,
            document_parsing_task_id=task.id,
            source_key="img-1",
            file_hash="a" * 64,
            extracted_image_id=1,
            semantic_task_id=uuid4(),
            status=DocumentParsingImageItemStatus.succeeded,
            result_source=DocumentParsingImageItemResultSource.semantic_snapshot,
        )

        with patch.object(document_parsing, "get_active_document_or_404", return_value=document), patch.object(
            document_parsing,
            "get_file_or_404",
            return_value=file_record,
        ), patch.object(
            document_parsing,
            "get_latest_succeeded_document_parsing_task_for_document_file",
            return_value=task,
        ), patch.object(
            document_parsing,
            "get_layout_task_for_document_parsing_task",
            return_value=layout_task,
        ), patch.object(document_parsing, "get_document_parsing_image_items", return_value=[image_item]), patch.object(
            document_parsing,
            "get_document_parsing_image_semantic_result",
            return_value=document_parsing.DocumentParsingImageSemanticResult(
                description="semantic text",
                result_model="vision-model",
                source_task_id=uuid4(),
                updated_at=datetime.now(timezone.utc),
            ),
        ):
            response = document_parsing.get_document_parsing_document_result(document_id=document.id, session=session)

        self.assertEqual(response.status, document_parsing.DocumentParsingDocumentResultStatus.succeeded)
        self.assertEqual(response.layout_status, LayoutAnalysisTaskStatus.succeeded)
        self.assertEqual(response.image_analysis_status, document_parsing.DocumentParsingImageAnalysisStatus.succeeded)
        self.assertEqual(response.markdown, "# parsed")
        self.assertEqual(response.image_hashes, {"img-1": "a" * 64})
        self.assertEqual(len(response.image_items), 1)
        self.assertEqual(response.image_items[0].semantic.description, "semantic text")

    def test_create_document_parsing_task_maps_llm_config_errors(self):
        document, file_record = self._build_pdf_document_and_file()

        with patch.object(document_parsing, "get_active_document_or_404", return_value=document), patch.object(
            document_parsing,
            "get_file_or_404",
            return_value=file_record,
        ), patch.object(
            document_parsing,
            "create_or_reuse_document_parsing_task",
            side_effect=document_parsing.LlmConfigNotFoundError("LLM config not found"),
        ):
            with self.assertRaises(HTTPException) as ctx:
                document_parsing.create_document_parsing_task(
                    payload=document_parsing.DocumentParsingTaskCreateRequest(
                        document_id=document.id,
                        image_llm_config_id=uuid4(),
                    ),
                    session=_SessionStub(),
                )

        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(ctx.exception.detail, "LLM config not found")

    def test_get_document_parsing_document_result_returns_no_task_without_succeeded_result(self):
        document, file_record = self._build_pdf_document_and_file()

        with patch.object(document_parsing, "get_active_document_or_404", return_value=document), patch.object(
            document_parsing,
            "get_file_or_404",
            return_value=file_record,
        ), patch.object(
            document_parsing,
            "get_latest_succeeded_document_parsing_task_for_document_file",
            return_value=None,
        ):
            response = document_parsing.get_document_parsing_document_result(document_id=document.id, session=_SessionStub())

        self.assertEqual(response.status, document_parsing.DocumentParsingDocumentResultStatus.no_task)
        self.assertEqual(response.file_id, file_record.id)
        self.assertIsNone(response.task_id)

    def test_create_layout_analysis_task_returns_layout_semantics(self):
        document, file_record = self._build_pdf_document_and_file()
        task = LayoutAnalysisTask(
            document_id=document.id,
            file_id=file_record.id,
            storage_bucket=file_record.storage_bucket,
            storage_key=file_record.storage_key,
            requested_layout_model="marker",
            target_layout_model="marker",
            layout_model_key="marker",
            status=LayoutAnalysisTaskStatus.pending,
        )

        with patch.object(layout_analysis, "get_active_document_or_404", return_value=document), patch.object(
            layout_analysis,
            "get_file_or_404",
            return_value=file_record,
        ), patch.object(
            layout_analysis,
            "create_or_reuse_layout_analysis_task",
            return_value=SimpleNamespace(task=task, reused=False),
        ):
            response = layout_analysis.create_layout_analysis_task(
                payload=layout_analysis.LayoutAnalysisTaskCreateRequest(document_id=document.id, layout_model="marker"),
                session=_SessionStub(),
            )

        self.assertEqual(response.target_layout_model, "marker")
        self.assertEqual(response.status, LayoutAnalysisTaskStatus.pending)
        self.assertFalse(response.reused)

    def test_layout_analysis_result_returns_no_task(self):
        document, file_record = self._build_pdf_document_and_file()
        with patch.object(layout_analysis, "get_active_document_or_404", return_value=document), patch.object(
            layout_analysis,
            "get_file_or_404",
            return_value=file_record,
        ), patch.object(layout_analysis, "get_latest_layout_analysis_task_for_document_file", return_value=None):
            response = layout_analysis.get_layout_analysis_document_result(document_id=document.id, session=_SessionStub())

        self.assertEqual(response.status, layout_analysis.LayoutAnalysisDocumentResultStatus.no_task)
        self.assertIsNone(response.task_id)

    def test_old_pdf_to_markdown_route_is_removed(self):
        paths = {
            (getattr(route, "path", None), tuple(sorted(getattr(route, "methods", set()))))
            for route in document_parsing.router.routes
        }
        self.assertNotIn(("/document-parsing/pdf-to-markdown", ("POST",)), paths)

    def test_new_layout_analysis_route_exists(self):
        paths = {
            (getattr(route, "path", None), tuple(sorted(getattr(route, "methods", set()))))
            for route in layout_analysis.router.routes
        }
        self.assertIn(("/layout-analysis/tasks", ("POST",)), paths)
