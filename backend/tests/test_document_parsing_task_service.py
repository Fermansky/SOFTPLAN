from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from backend.app.models import (
    DEFAULT_DOCUMENT_PARSING_IMAGE_MODEL_KEY,
    DEFAULT_DOCUMENT_PARSING_PDF_MODEL,
    DocumentParsingTask,
    DocumentParsingTaskStatus,
    ExtractedImage,
    ExtractedImageSemanticTask,
    ExtractedImageSemanticTaskStatus,
)
from backend.app.services import document_parsing_task_service as service
from backend.app.services.file_convert_service import PdfToMarkdownResult, UploadedImageMetadata


class _ExecResult:
    def __init__(self, first_value=None, all_values=None):
        self._first_value = first_value
        self._all_values = all_values if all_values is not None else []

    def first(self):
        return self._first_value

    def all(self):
        return self._all_values


class _SessionStub:
    def __init__(self, *, existing_task: DocumentParsingTask | None = None, raise_integrity_on_commit: bool = False):
        self._existing_task = existing_task
        self._raise_integrity_on_commit = raise_integrity_on_commit
        self._integrity_raised = False
        self.added = []
        self.committed = False
        self.rolled_back = False
        self.refreshed = []
        self.last_statement = None

    def exec(self, statement):
        self.last_statement = statement
        return _ExecResult(first_value=self._existing_task)

    def add(self, item):
        self.added.append(item)

    def commit(self):
        if self._raise_integrity_on_commit and not self._integrity_raised:
            self._integrity_raised = True
            raise IntegrityError("commit failed", None, None)
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def refresh(self, item):
        self.refreshed.append(item)


class _WorkerSessionStub:
    def __init__(self, *, task: DocumentParsingTask | None, exec_all_values=None):
        self.task = task
        self.exec_all_values = list(exec_all_values or [])
        self.added = []
        self.committed = False
        self.commit_calls = 0
        self.rolled_back = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, model, item_id):
        if model is service.DocumentParsingTask:
            return self.task if self.task is not None and self.task.id == item_id else None
        return None

    def exec(self, statement):
        values = self.exec_all_values.pop(0) if self.exec_all_values else []
        return _ExecResult(all_values=values)

    def add(self, item):
        self.added.append(item)

    def commit(self):
        self.committed = True
        self.commit_calls += 1

    def rollback(self):
        self.rolled_back = True


class ConversionTaskServiceTests(TestCase):
    def test_process_one_pending_document_parsing_task_returns_false_when_no_task(self):
        with patch.object(service, "claim_next_pending_document_parsing_task_id", return_value=None):
            with patch.object(service, "execute_document_parsing_task") as execute_mock:
                processed = service.process_one_pending_document_parsing_task()

        self.assertFalse(processed)
        execute_mock.assert_not_called()

    def test_process_one_pending_document_parsing_task_executes_claimed_task(self):
        task_id = uuid4()

        with patch.object(service, "claim_next_pending_document_parsing_task_id", return_value=task_id):
            with patch.object(service, "execute_document_parsing_task") as execute_mock:
                processed = service.process_one_pending_document_parsing_task()

        self.assertTrue(processed)
        execute_mock.assert_called_once_with(task_id, client=None)

    def test_get_active_document_parsing_task_filters_by_model_keys(self):
        existing = DocumentParsingTask(
            document_id=uuid4(),
            file_id=uuid4(),
            storage_bucket="softplan",
            storage_key="documents/2026/04/a.pdf",
            status=DocumentParsingTaskStatus.running,
            pdf_model_key="marker",
            image_model_key="vision-model",
        )
        session = _SessionStub(existing_task=existing)

        result = service.get_active_document_parsing_task_for_document(
            session,
            document_id=existing.document_id,
            pdf_model_key="marker",
            image_model_key="vision-model",
        )

        self.assertEqual(result, existing)
        where_clause = str(session.last_statement.whereclause)
        self.assertIn("document_parsing_tasks.pdf_model_key", where_clause)
        self.assertIn("document_parsing_tasks.image_model_key", where_clause)

    def test_create_or_reuse_document_parsing_task_reuses_active_task(self):
        existing = DocumentParsingTask(
            document_id=uuid4(),
            file_id=uuid4(),
            storage_bucket="softplan",
            storage_key="documents/2026/04/a.pdf",
            requested_pdf_model="marker",
            target_pdf_model="marker",
            pdf_model_key="marker",
            requested_image_model="vision-model",
            target_image_model="vision-model",
            image_model_key="vision-model",
            status=DocumentParsingTaskStatus.running,
        )
        session = _SessionStub(existing_task=existing)

        result = service.create_or_reuse_document_parsing_task(
            session,
            document_id=existing.document_id,
            file_id=existing.file_id,
            storage_bucket=existing.storage_bucket,
            storage_key=existing.storage_key,
            requested_pdf_model="marker",
            requested_image_model="vision-model",
        )

        self.assertTrue(result.reused)
        self.assertEqual(result.task, existing)
        self.assertEqual(session.added, [])

    def test_create_or_reuse_document_parsing_task_creates_new_task(self):
        session = _SessionStub(existing_task=None)
        document_id = uuid4()
        file_id = uuid4()

        result = service.create_or_reuse_document_parsing_task(
            session,
            document_id=document_id,
            file_id=file_id,
            storage_bucket="softplan",
            storage_key="documents/2026/04/a.pdf",
            requested_pdf_model=None,
            requested_image_model=None,
        )

        self.assertFalse(result.reused)
        self.assertEqual(result.task.document_id, document_id)
        self.assertEqual(result.task.file_id, file_id)
        self.assertEqual(result.task.target_pdf_model, DEFAULT_DOCUMENT_PARSING_PDF_MODEL)
        self.assertEqual(result.task.pdf_model_key, DEFAULT_DOCUMENT_PARSING_PDF_MODEL)
        self.assertEqual(result.task.target_image_model, None)
        self.assertEqual(result.task.image_model_key, DEFAULT_DOCUMENT_PARSING_IMAGE_MODEL_KEY)
        self.assertTrue(session.committed)
        self.assertEqual(session.refreshed, [result.task])

    def test_create_or_reuse_document_parsing_task_passes_distinct_model_keys(self):
        session = _SessionStub(existing_task=None)
        document_id = uuid4()
        file_id = uuid4()

        with patch.object(service, "get_active_document_parsing_task_for_document", return_value=None) as get_active_mock:
            service.create_or_reuse_document_parsing_task(
                session,
                document_id=document_id,
                file_id=file_id,
                storage_bucket="softplan",
                storage_key="documents/2026/04/a.pdf",
                requested_pdf_model="marker",
                requested_image_model="vision-model",
            )

        self.assertEqual(get_active_mock.call_args.kwargs["pdf_model_key"], "marker")
        self.assertEqual(get_active_mock.call_args.kwargs["image_model_key"], "vision-model")

    def test_create_or_reuse_document_parsing_task_handles_integrity_conflict(self):
        existing = DocumentParsingTask(
            document_id=uuid4(),
            file_id=uuid4(),
            storage_bucket="softplan",
            storage_key="documents/2026/04/a.pdf",
            requested_pdf_model="marker",
            target_pdf_model="marker",
            pdf_model_key="marker",
            requested_image_model=None,
            target_image_model=None,
            image_model_key=DEFAULT_DOCUMENT_PARSING_IMAGE_MODEL_KEY,
            status=DocumentParsingTaskStatus.pending,
        )
        session = _SessionStub(existing_task=None, raise_integrity_on_commit=True)

        with patch.object(
            service,
            "get_active_document_parsing_task_for_document",
            side_effect=[None, existing],
        ):
            result = service.create_or_reuse_document_parsing_task(
                session,
                document_id=existing.document_id,
                file_id=existing.file_id,
                storage_bucket=existing.storage_bucket,
                storage_key=existing.storage_key,
                requested_pdf_model="marker",
                requested_image_model=None,
            )

        self.assertTrue(result.reused)
        self.assertEqual(result.task, existing)
        self.assertTrue(session.rolled_back)

    def test_resolve_document_parsing_pdf_model_selection_rejects_unknown_model(self):
        with self.assertRaises(service.UnsupportedDocumentParsingPdfModelError):
            service.resolve_document_parsing_pdf_model_selection("other-model")

    def test_get_latest_document_parsing_task_for_document_file_filters_by_document_and_file(self):
        document_id = uuid4()
        file_id = uuid4()
        task = DocumentParsingTask(
            document_id=document_id,
            file_id=file_id,
            storage_bucket="softplan",
            storage_key="documents/2026/04/a.pdf",
            status=DocumentParsingTaskStatus.succeeded,
        )
        session = _SessionStub(existing_task=task)

        result = service.get_latest_document_parsing_task_for_document_file(
            session,
            document_id=document_id,
            file_id=file_id,
        )

        self.assertEqual(result, task)
        self.assertIn("document_parsing_tasks.document_id", str(session.last_statement.whereclause))
        self.assertIn("document_parsing_tasks.file_id", str(session.last_statement.whereclause))
        self.assertIn("ORDER BY document_parsing_tasks.created_at DESC", str(session.last_statement))


class DocumentParsingTaskExecutionTests(TestCase):
    def _build_running_task(self) -> DocumentParsingTask:
        return DocumentParsingTask(
            id=uuid4(),
            document_id=uuid4(),
            file_id=uuid4(),
            storage_bucket="softplan",
            storage_key="documents/2026/04/a.pdf",
            requested_pdf_model="marker",
            target_pdf_model="marker",
            pdf_model_key="marker",
            requested_image_model="vision-model",
            target_image_model="vision-model",
            image_model_key="vision-model",
            status=DocumentParsingTaskStatus.running,
        )

    def _build_uploaded_image(self, *, source_key: str, file_hash: str) -> UploadedImageMetadata:
        return UploadedImageMetadata(
            source_key=source_key,
            file_hash=file_hash,
            storage_bucket="softplan",
            storage_key=f"images/{file_hash}.png",
            file_size=123,
            content_type="image/png",
            extension=".png",
            width=100,
            height=200,
        )

    def _build_extracted_image(self, *, image_id: int, file_hash: str, semantic_description: str | None = None) -> ExtractedImage:
        return ExtractedImage(
            id=image_id,
            file_hash=file_hash,
            storage_bucket="softplan",
            storage_key=f"images/{file_hash}.png",
            file_size=123,
            content_type="image/png",
            extension=".png",
            width=100,
            height=200,
            semantic_description=semantic_description,
        )

    def test_execute_document_parsing_task_skips_images_with_existing_semantics(self):
        task = self._build_running_task()
        uploaded_images = [
            self._build_uploaded_image(source_key="img-1", file_hash="a" * 64),
            self._build_uploaded_image(source_key="img-2", file_hash="b" * 64),
        ]
        session = _WorkerSessionStub(
            task=task,
            exec_all_values=[
                [
                    self._build_extracted_image(image_id=1, file_hash="a" * 64, semantic_description="already there"),
                    self._build_extracted_image(image_id=2, file_hash="b" * 64, semantic_description="present"),
                ]
            ],
        )
        client_calls = []

        def convert_pdf_to_markdown(**kwargs):
            client_calls.append(kwargs)
            return (
                PdfToMarkdownResult(markdown="# done", image_hashes={"img-1": "a" * 64}, uploaded_images=uploaded_images),
                None,
            )

        with patch.object(service, "Session", return_value=session), patch.object(
            service,
            "persist_extracted_images",
        ) as persist_mock, patch.object(
            service,
            "create_or_reuse_extracted_image_semantic_task",
        ) as create_semantic_mock:
            service.execute_document_parsing_task(
                task.id,
                client=SimpleNamespace(convert_pdf_to_markdown=convert_pdf_to_markdown),
            )

        self.assertTrue(session.committed)
        self.assertEqual(client_calls[0]["model"], "marker")
        self.assertEqual(task.status, DocumentParsingTaskStatus.succeeded)
        self.assertEqual(len(task.semantic_dispatches), 2)
        self.assertEqual(task.semantic_dispatches[0]["dispatch_status"], "skipped_existing_snapshot")
        self.assertEqual(task.semantic_dispatches[0]["target_model"], "vision-model")
        self.assertEqual(task.semantic_dispatches[1]["dispatch_status"], "skipped_existing_snapshot")
        persist_mock.assert_called_once()
        create_semantic_mock.assert_not_called()

    def test_execute_document_parsing_task_creates_semantic_tasks_for_images_without_snapshot(self):
        task = self._build_running_task()
        uploaded_image = self._build_uploaded_image(source_key="img-1", file_hash="a" * 64)
        extracted_image = self._build_extracted_image(image_id=1, file_hash="a" * 64, semantic_description=None)
        semantic_task = ExtractedImageSemanticTask(
            id=uuid4(),
            extracted_image_id=1,
            status=ExtractedImageSemanticTaskStatus.pending,
            target_model="vision-model",
            target_model_key="vision-model",
            prompt_path="backend/app/prompts/extracted_image_semantic.txt",
        )
        session = _WorkerSessionStub(task=task, exec_all_values=[[extracted_image]])

        with patch.object(service, "Session", return_value=session), patch.object(service, "persist_extracted_images"), patch.object(
            service,
            "create_or_reuse_extracted_image_semantic_task",
            return_value=SimpleNamespace(task=semantic_task, reused=False),
        ) as create_semantic_mock:
            service.execute_document_parsing_task(
                task.id,
                client=SimpleNamespace(
                    convert_pdf_to_markdown=lambda **kwargs: (
                        PdfToMarkdownResult(markdown="# done", image_hashes={"img-1": uploaded_image.file_hash}, uploaded_images=[uploaded_image]),
                        None,
                    )
                ),
            )

        self.assertTrue(session.committed)
        self.assertEqual(task.status, DocumentParsingTaskStatus.succeeded)
        self.assertEqual(task.semantic_dispatches[0]["dispatch_status"], "submitted")
        self.assertEqual(task.semantic_dispatches[0]["semantic_task_id"], str(semantic_task.id))
        self.assertEqual(task.semantic_dispatches[0]["target_model"], "vision-model")
        self.assertEqual(create_semantic_mock.call_args.kwargs["requested_model"], "vision-model")
        self.assertEqual(create_semantic_mock.call_args.kwargs["target_model"], "vision-model")
        self.assertTrue(create_semantic_mock.call_args.kwargs["use_target_model"])

    def test_execute_document_parsing_task_reuses_active_semantic_task(self):
        task = self._build_running_task()
        uploaded_image = self._build_uploaded_image(source_key="img-1", file_hash="a" * 64)
        extracted_image = self._build_extracted_image(image_id=1, file_hash="a" * 64, semantic_description=None)
        semantic_task = ExtractedImageSemanticTask(
            id=uuid4(),
            extracted_image_id=1,
            status=ExtractedImageSemanticTaskStatus.pending,
            target_model="vision-model",
            target_model_key="vision-model",
            prompt_path="backend/app/prompts/extracted_image_semantic.txt",
        )
        session = _WorkerSessionStub(task=task, exec_all_values=[[extracted_image]])

        with patch.object(service, "Session", return_value=session), patch.object(service, "persist_extracted_images"), patch.object(
            service,
            "create_or_reuse_extracted_image_semantic_task",
            return_value=SimpleNamespace(task=semantic_task, reused=True),
        ):
            service.execute_document_parsing_task(
                task.id,
                client=SimpleNamespace(
                    convert_pdf_to_markdown=lambda **kwargs: (
                        PdfToMarkdownResult(markdown="# done", image_hashes={"img-1": uploaded_image.file_hash}, uploaded_images=[uploaded_image]),
                        None,
                    )
                ),
            )

        self.assertEqual(task.status, DocumentParsingTaskStatus.succeeded)
        self.assertEqual(task.semantic_dispatches[0]["dispatch_status"], "reused")

    def test_execute_document_parsing_task_fails_when_persisted_image_cannot_be_reloaded(self):
        task = self._build_running_task()
        uploaded_image = self._build_uploaded_image(source_key="img-1", file_hash="a" * 64)
        session = _WorkerSessionStub(task=task, exec_all_values=[[]])

        with patch.object(service, "Session", return_value=session), patch.object(service, "persist_extracted_images"), patch.object(
            service,
            "create_or_reuse_extracted_image_semantic_task",
        ) as create_semantic_mock:
            service.execute_document_parsing_task(
                task.id,
                client=SimpleNamespace(
                    convert_pdf_to_markdown=lambda **kwargs: (
                        PdfToMarkdownResult(markdown="# done", image_hashes={"img-1": uploaded_image.file_hash}, uploaded_images=[uploaded_image]),
                        None,
                    )
                ),
            )

        self.assertTrue(session.committed)
        self.assertEqual(task.status, DocumentParsingTaskStatus.failed)
        self.assertEqual(task.error_message, "Failed to dispatch extracted image semantic tasks")
        create_semantic_mock.assert_not_called()

    def test_execute_document_parsing_task_fails_when_semantic_dispatch_raises(self):
        task = self._build_running_task()
        uploaded_image = self._build_uploaded_image(source_key="img-1", file_hash="a" * 64)
        extracted_image = self._build_extracted_image(image_id=1, file_hash="a" * 64, semantic_description=None)
        session = _WorkerSessionStub(task=task, exec_all_values=[[extracted_image]])

        with patch.object(service, "Session", return_value=session), patch.object(service, "persist_extracted_images"), patch.object(
            service,
            "create_or_reuse_extracted_image_semantic_task",
            side_effect=SQLAlchemyError("db failed"),
        ):
            service.execute_document_parsing_task(
                task.id,
                client=SimpleNamespace(
                    convert_pdf_to_markdown=lambda **kwargs: (
                        PdfToMarkdownResult(markdown="# done", image_hashes={"img-1": uploaded_image.file_hash}, uploaded_images=[uploaded_image]),
                        None,
                    )
                ),
            )

        self.assertTrue(session.committed)
        self.assertEqual(task.status, DocumentParsingTaskStatus.failed)
        self.assertEqual(task.error_message, "Failed to dispatch extracted image semantic tasks")


class _WorkerSessionWithSourceStub(_WorkerSessionStub):
    def __init__(self, *, task: DocumentParsingTask | None, source_task: DocumentParsingTask | None, exec_all_values=None):
        super().__init__(task=task, exec_all_values=exec_all_values)
        self.source_task = source_task

    def get(self, model, item_id):
        if model is service.DocumentParsingTask:
            if self.task is not None and self.task.id == item_id:
                return self.task
            if self.source_task is not None and self.source_task.id == item_id:
                return self.source_task
        return None


class DocumentParsingTaskReuseTests(TestCase):
    def test_create_or_reuse_document_parsing_task_reuses_successful_full_task(self):
        existing = DocumentParsingTask(
            document_id=uuid4(),
            file_id=uuid4(),
            storage_bucket="softplan",
            storage_key="documents/2026/04/a.pdf",
            requested_pdf_model="marker",
            target_pdf_model="marker",
            pdf_model_key="marker",
            requested_image_model="vision-model",
            target_image_model="vision-model",
            image_model_key="vision-model",
            status=DocumentParsingTaskStatus.succeeded,
            markdown="# done",
            image_hashes={"img-1": "a" * 64},
            semantic_dispatches=[
                {
                    "source_key": "img-1",
                    "file_hash": "a" * 64,
                    "image_id": 1,
                    "semantic_task_id": str(uuid4()),
                    "dispatch_status": "submitted",
                    "target_model": "vision-model",
                }
            ],
        )
        session = _SessionStub(existing_task=None)

        with patch.object(service, "get_active_document_parsing_task_for_document", return_value=None), patch.object(
            service,
            "get_latest_succeeded_document_parsing_task_for_file_pdf_image",
            return_value=existing,
        ) as full_mock, patch.object(service, "get_latest_succeeded_document_parsing_task_for_file_pdf") as pdf_mock:
            result = service.create_or_reuse_document_parsing_task(
                session,
                document_id=uuid4(),
                file_id=existing.file_id,
                storage_bucket="softplan",
                storage_key="documents/2026/04/b.pdf",
                requested_pdf_model="marker",
                requested_image_model="vision-model",
                dispatch_semantic_tasks=True,
            )

        self.assertTrue(result.reused)
        self.assertEqual(result.task, existing)
        self.assertEqual(session.added, [])
        self.assertFalse(session.committed)
        full_mock.assert_called_once()
        pdf_mock.assert_not_called()

    def test_create_or_reuse_document_parsing_task_creates_new_task_with_pdf_result_source(self):
        source_task = DocumentParsingTask(
            id=uuid4(),
            document_id=uuid4(),
            file_id=uuid4(),
            storage_bucket="softplan",
            storage_key="documents/2026/04/a.pdf",
            requested_pdf_model="marker",
            target_pdf_model="marker",
            pdf_model_key="marker",
            status=DocumentParsingTaskStatus.succeeded,
            markdown="# cached",
            image_hashes={"img-1": "a" * 64},
        )
        session = _SessionStub(existing_task=None)

        with patch.object(service, "get_active_document_parsing_task_for_document", return_value=None), patch.object(
            service,
            "get_latest_succeeded_document_parsing_task_for_file_pdf_image",
            return_value=None,
        ), patch.object(
            service,
            "get_latest_succeeded_document_parsing_task_for_file_pdf",
            return_value=source_task,
        ):
            result = service.create_or_reuse_document_parsing_task(
                session,
                document_id=uuid4(),
                file_id=source_task.file_id,
                storage_bucket="softplan",
                storage_key="documents/2026/04/b.pdf",
                requested_pdf_model="marker",
                requested_image_model="other-vision-model",
                dispatch_semantic_tasks=True,
            )

        self.assertFalse(result.reused)
        self.assertEqual(result.task.pdf_result_source_task_id, source_task.id)
        self.assertFalse(result.task.force_pdf_parse)
        self.assertTrue(session.committed)

    def test_create_or_reuse_document_parsing_task_force_pdf_parse_skips_success_reuse(self):
        session = _SessionStub(existing_task=None)

        with patch.object(service, "get_active_document_parsing_task_for_document", return_value=None), patch.object(
            service,
            "get_latest_succeeded_document_parsing_task_for_file_pdf_image",
        ) as full_mock, patch.object(service, "get_latest_succeeded_document_parsing_task_for_file_pdf") as pdf_mock:
            result = service.create_or_reuse_document_parsing_task(
                session,
                document_id=uuid4(),
                file_id=uuid4(),
                storage_bucket="softplan",
                storage_key="documents/2026/04/a.pdf",
                requested_pdf_model="marker",
                requested_image_model="vision-model",
                force_pdf_parse=True,
                dispatch_semantic_tasks=True,
            )

        self.assertFalse(result.reused)
        self.assertTrue(result.task.force_pdf_parse)
        self.assertIsNone(result.task.pdf_result_source_task_id)
        full_mock.assert_not_called()
        pdf_mock.assert_not_called()

    def test_execute_document_parsing_task_reuses_cached_pdf_result(self):
        task_id = uuid4()
        source_task_id = uuid4()
        task = DocumentParsingTask(
            id=task_id,
            document_id=uuid4(),
            file_id=uuid4(),
            storage_bucket="softplan",
            storage_key="documents/2026/04/a.pdf",
            requested_pdf_model="marker",
            target_pdf_model="marker",
            pdf_model_key="marker",
            requested_image_model="vision-model",
            target_image_model="vision-model",
            image_model_key="vision-model",
            pdf_result_source_task_id=source_task_id,
            status=DocumentParsingTaskStatus.running,
        )
        source_task = DocumentParsingTask(
            id=source_task_id,
            document_id=uuid4(),
            file_id=task.file_id,
            storage_bucket="softplan",
            storage_key="documents/2026/04/a.pdf",
            requested_pdf_model="marker",
            target_pdf_model="marker",
            pdf_model_key="marker",
            status=DocumentParsingTaskStatus.succeeded,
            markdown="# cached",
            image_hashes={"img-1": "a" * 64},
        )
        extracted_image = ExtractedImage(
            id=1,
            file_hash="a" * 64,
            storage_bucket="softplan",
            storage_key="images/a.png",
            file_size=123,
            content_type="image/png",
            extension=".png",
            width=100,
            height=100,
        )
        semantic_task = ExtractedImageSemanticTask(
            id=uuid4(),
            extracted_image_id=1,
            status=ExtractedImageSemanticTaskStatus.pending,
            target_model="vision-model",
            target_model_key="vision-model",
            prompt_path="backend/app/prompts/extracted_image_semantic.txt",
        )
        session = _WorkerSessionWithSourceStub(task=task, source_task=source_task, exec_all_values=[[extracted_image]])
        client_calls = []

        with patch.object(service, "Session", return_value=session), patch.object(
            service,
            "persist_extracted_images",
        ) as persist_mock, patch.object(
            service,
            "create_or_reuse_extracted_image_semantic_task",
            return_value=SimpleNamespace(task=semantic_task, reused=False),
        ):
            service.execute_document_parsing_task(
                task.id,
                client=SimpleNamespace(
                    convert_pdf_to_markdown=lambda **kwargs: client_calls.append(kwargs) or (None, "should not be called")
                ),
            )

        self.assertEqual(client_calls, [])
        persist_mock.assert_not_called()
        self.assertEqual(task.status, DocumentParsingTaskStatus.succeeded)
        self.assertEqual(task.markdown, "# cached")
        self.assertEqual(task.image_hashes, {"img-1": "a" * 64})
        self.assertEqual(task.pdf_result_source_task_id, source_task_id)
        self.assertEqual(task.semantic_dispatches[0]["dispatch_status"], "submitted")

    def test_execute_document_parsing_task_falls_back_when_cached_pdf_source_is_invalid(self):
        task_id = uuid4()
        source_task_id = uuid4()
        task = DocumentParsingTask(
            id=task_id,
            document_id=uuid4(),
            file_id=uuid4(),
            storage_bucket="softplan",
            storage_key="documents/2026/04/a.pdf",
            requested_pdf_model="marker",
            target_pdf_model="marker",
            pdf_model_key="marker",
            requested_image_model="vision-model",
            target_image_model="vision-model",
            image_model_key="vision-model",
            pdf_result_source_task_id=source_task_id,
            status=DocumentParsingTaskStatus.running,
        )
        source_task = DocumentParsingTask(
            id=source_task_id,
            document_id=uuid4(),
            file_id=task.file_id,
            storage_bucket="softplan",
            storage_key="documents/2026/04/a.pdf",
            requested_pdf_model="marker",
            target_pdf_model="marker",
            pdf_model_key="marker",
            status=DocumentParsingTaskStatus.failed,
        )
        uploaded_image = UploadedImageMetadata(
            source_key="img-1",
            file_hash="a" * 64,
            storage_bucket="softplan",
            storage_key="images/a.png",
            file_size=123,
            content_type="image/png",
            extension=".png",
            width=100,
            height=100,
        )
        extracted_image = ExtractedImage(
            id=1,
            file_hash="a" * 64,
            storage_bucket="softplan",
            storage_key="images/a.png",
            file_size=123,
            content_type="image/png",
            extension=".png",
            width=100,
            height=100,
        )
        session = _WorkerSessionWithSourceStub(task=task, source_task=source_task, exec_all_values=[[extracted_image]])
        client_calls = []

        with patch.object(service, "Session", return_value=session), patch.object(service, "persist_extracted_images") as persist_mock, patch.object(
            service,
            "create_or_reuse_extracted_image_semantic_task",
            return_value=SimpleNamespace(
                task=ExtractedImageSemanticTask(
                    id=uuid4(),
                    extracted_image_id=1,
                    status=ExtractedImageSemanticTaskStatus.pending,
                    target_model="vision-model",
                    target_model_key="vision-model",
                    prompt_path="backend/app/prompts/extracted_image_semantic.txt",
                ),
                reused=False,
            ),
        ):
            service.execute_document_parsing_task(
                task.id,
                client=SimpleNamespace(
                    convert_pdf_to_markdown=lambda **kwargs: client_calls.append(kwargs)
                    or (
                        PdfToMarkdownResult(
                            markdown="# live",
                            image_hashes={"img-1": uploaded_image.file_hash},
                            uploaded_images=[uploaded_image],
                        ),
                        None,
                    )
                ),
            )

        self.assertEqual(len(client_calls), 1)
        persist_mock.assert_called_once()
        self.assertEqual(task.status, DocumentParsingTaskStatus.succeeded)
        self.assertEqual(task.markdown, "# live")
        self.assertIsNone(task.pdf_result_source_task_id)
