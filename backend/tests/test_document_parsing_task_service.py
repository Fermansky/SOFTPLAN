from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

from backend.app.models import (
    DEFAULT_DOCUMENT_PARSING_IMAGE_MODEL_KEY,
    DocumentParsingImageItem,
    DocumentParsingImageItemResultSource,
    DocumentParsingImageItemStatus,
    DocumentParsingTask,
    DocumentParsingTaskStatus,
    ExtractedImage,
    ExtractedImageSemanticSnapshot,
    ExtractedImageSemanticTask,
    ExtractedImageSemanticTaskStatus,
    LayoutAnalysisTask,
    LayoutAnalysisTaskStatus,
)
from backend.app.services import document_parsing_task_service as service


class _SessionStub:
    def __init__(self):
        self.added = []
        self.committed = False
        self.refreshed = []
        self.rolled_back = False
        self.get_results = {}

    def add(self, item):
        self.added.append(item)

    def commit(self):
        self.committed = True

    def refresh(self, item):
        self.refreshed.append(item)

    def rollback(self):
        self.rolled_back = True

    def get(self, model, item_id):
        return self.get_results.get((model, item_id))


class _ExecResult:
    def __init__(self, values):
        self._values = values

    def all(self):
        return list(self._values)


class _ProcessSessionStub:
    def __init__(self, task_ids):
        self.task_ids = task_ids

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def exec(self, statement):
        return _ExecResult(self.task_ids)


class DocumentParsingTaskServiceTests(TestCase):
    def test_create_or_reuse_document_parsing_task_reuses_completed_aggregate_task(self):
        existing = DocumentParsingTask(
            document_id=uuid4(),
            file_id=uuid4(),
            storage_bucket="softplan",
            storage_key="documents/a.pdf",
            requested_layout_model="marker",
            target_layout_model="marker",
            layout_model_key="marker",
            requested_image_model="vision-model",
            target_image_model="vision-model",
            image_model_key="vision-model",
            layout_task_id=uuid4(),
            status=DocumentParsingTaskStatus.succeeded,
        )
        session = _SessionStub()

        with patch.object(service, "get_active_document_parsing_task_for_document", return_value=None), patch.object(
            service,
            "get_latest_succeeded_document_parsing_task_for_file",
            return_value=existing,
        ), patch.object(service, "create_or_reuse_layout_analysis_task") as create_layout_mock:
            result = service.create_or_reuse_document_parsing_task(
                session,
                document_id=uuid4(),
                file_id=existing.file_id,
                storage_bucket="softplan",
                storage_key="documents/b.pdf",
                requested_layout_model="marker",
                requested_image_model="vision-model",
            )

        self.assertTrue(result.reused)
        self.assertEqual(result.task, existing)
        create_layout_mock.assert_not_called()

    def test_create_or_reuse_document_parsing_task_creates_new_aggregate_task(self):
        session = _SessionStub()
        layout_task = LayoutAnalysisTask(
            id=uuid4(),
            document_id=uuid4(),
            file_id=uuid4(),
            storage_bucket="softplan",
            storage_key="documents/a.pdf",
            requested_layout_model="marker",
            target_layout_model="marker",
            layout_model_key="marker",
            status=LayoutAnalysisTaskStatus.pending,
        )

        with patch.object(service, "get_active_document_parsing_task_for_document", return_value=None), patch.object(
            service,
            "get_latest_succeeded_document_parsing_task_for_file",
            return_value=None,
        ), patch.object(
            service,
            "create_or_reuse_layout_analysis_task",
            return_value=SimpleNamespace(task=layout_task, reused=False),
        ), patch.object(service, "synchronize_document_parsing_task") as sync_mock:
            result = service.create_or_reuse_document_parsing_task(
                session,
                document_id=layout_task.document_id,
                file_id=layout_task.file_id,
                storage_bucket="softplan",
                storage_key="documents/b.pdf",
                requested_layout_model="marker",
                requested_image_model=None,
            )

        self.assertFalse(result.reused)
        self.assertEqual(result.task.layout_task_id, layout_task.id)
        self.assertEqual(result.task.layout_model_key, "marker")
        self.assertEqual(result.task.image_model_key, DEFAULT_DOCUMENT_PARSING_IMAGE_MODEL_KEY)
        self.assertTrue(session.committed)
        self.assertGreaterEqual(len(session.refreshed), 1)
        sync_mock.assert_called_once_with(result.task.id)

    def test_get_default_document_parsing_task_returns_none_without_tasks(self):
        with patch.object(service, "get_latest_document_parsing_task_for_document_file", return_value=None), patch.object(
            service,
            "get_latest_succeeded_document_parsing_task_for_document_file",
        ) as latest_succeeded_mock:
            result = service.get_default_document_parsing_task_for_document_file(
                _SessionStub(),
                document_id=uuid4(),
                file_id=uuid4(),
            )

        self.assertIsNone(result)
        latest_succeeded_mock.assert_not_called()

    def test_get_default_document_parsing_task_returns_latest_non_failed_task(self):
        latest_task = DocumentParsingTask(
            document_id=uuid4(),
            file_id=uuid4(),
            storage_bucket="softplan",
            storage_key="documents/a.pdf",
            target_layout_model="marker",
            layout_model_key="marker",
            image_model_key="vision-model",
            layout_task_id=uuid4(),
            status=DocumentParsingTaskStatus.running,
        )

        with patch.object(service, "get_latest_document_parsing_task_for_document_file", return_value=latest_task), patch.object(
            service,
            "get_latest_succeeded_document_parsing_task_for_document_file",
        ) as latest_succeeded_mock:
            result = service.get_default_document_parsing_task_for_document_file(
                _SessionStub(),
                document_id=latest_task.document_id,
                file_id=latest_task.file_id,
            )

        self.assertEqual(result, latest_task)
        latest_succeeded_mock.assert_not_called()

    def test_get_default_document_parsing_task_falls_back_to_latest_succeeded_task(self):
        latest_failed_task = DocumentParsingTask(
            document_id=uuid4(),
            file_id=uuid4(),
            storage_bucket="softplan",
            storage_key="documents/a.pdf",
            target_layout_model="marker",
            layout_model_key="marker",
            image_model_key="vision-model",
            layout_task_id=uuid4(),
            status=DocumentParsingTaskStatus.failed,
        )
        latest_succeeded_task = DocumentParsingTask(
            document_id=latest_failed_task.document_id,
            file_id=latest_failed_task.file_id,
            storage_bucket="softplan",
            storage_key="documents/a.pdf",
            target_layout_model="marker",
            layout_model_key="marker",
            image_model_key="vision-model",
            layout_task_id=uuid4(),
            status=DocumentParsingTaskStatus.succeeded,
        )

        with patch.object(service, "get_latest_document_parsing_task_for_document_file", return_value=latest_failed_task), patch.object(
            service,
            "get_latest_succeeded_document_parsing_task_for_document_file",
            return_value=latest_succeeded_task,
        ):
            result = service.get_default_document_parsing_task_for_document_file(
                _SessionStub(),
                document_id=latest_failed_task.document_id,
                file_id=latest_failed_task.file_id,
            )

        self.assertEqual(result, latest_succeeded_task)

    def test_get_default_document_parsing_task_keeps_latest_failed_task_when_no_success_exists(self):
        latest_failed_task = DocumentParsingTask(
            document_id=uuid4(),
            file_id=uuid4(),
            storage_bucket="softplan",
            storage_key="documents/a.pdf",
            target_layout_model="marker",
            layout_model_key="marker",
            image_model_key="vision-model",
            layout_task_id=uuid4(),
            status=DocumentParsingTaskStatus.failed,
        )

        with patch.object(service, "get_latest_document_parsing_task_for_document_file", return_value=latest_failed_task), patch.object(
            service,
            "get_latest_succeeded_document_parsing_task_for_document_file",
            return_value=None,
        ):
            result = service.get_default_document_parsing_task_for_document_file(
                _SessionStub(),
                document_id=latest_failed_task.document_id,
                file_id=latest_failed_task.file_id,
            )

        self.assertEqual(result, latest_failed_task)

    def test_recompute_document_parsing_task_state_running_when_images_pending(self):
        task = DocumentParsingTask(
            document_id=uuid4(),
            file_id=uuid4(),
            storage_bucket="softplan",
            storage_key="documents/a.pdf",
            target_layout_model="marker",
            layout_model_key="marker",
            image_model_key="vision-model",
            layout_task_id=uuid4(),
            status=DocumentParsingTaskStatus.pending,
        )
        layout_task = LayoutAnalysisTask(
            id=task.layout_task_id,
            document_id=task.document_id,
            file_id=task.file_id,
            storage_bucket="softplan",
            storage_key=task.storage_key,
            target_layout_model="marker",
            layout_model_key="marker",
            status=LayoutAnalysisTaskStatus.succeeded,
            markdown="# done",
            image_hashes={"img-1": "a" * 64},
        )
        pending_item = DocumentParsingImageItem(
            document_parsing_task_id=task.id,
            source_key="img-1",
            file_hash="a" * 64,
            extracted_image_id=1,
            semantic_task_id=uuid4(),
            status=DocumentParsingImageItemStatus.pending,
            result_source=DocumentParsingImageItemResultSource.submitted_semantic_task,
        )
        session = _SessionStub()

        with patch.object(service, "get_document_parsing_image_items", return_value=[pending_item]):
            service._recompute_document_parsing_task_state(session, task=task, layout_task=layout_task)

        self.assertEqual(task.status, DocumentParsingTaskStatus.running)
        self.assertEqual(task.markdown, "# done")
        self.assertEqual(task.image_total_count, 1)
        self.assertEqual(task.image_succeeded_count, 0)

    def test_recompute_document_parsing_task_state_succeeds_when_all_images_done(self):
        task = DocumentParsingTask(
            document_id=uuid4(),
            file_id=uuid4(),
            storage_bucket="softplan",
            storage_key="documents/a.pdf",
            target_layout_model="marker",
            layout_model_key="marker",
            image_model_key="vision-model",
            layout_task_id=uuid4(),
            status=DocumentParsingTaskStatus.running,
        )
        layout_task = LayoutAnalysisTask(
            id=task.layout_task_id,
            document_id=task.document_id,
            file_id=task.file_id,
            storage_bucket="softplan",
            storage_key=task.storage_key,
            target_layout_model="marker",
            layout_model_key="marker",
            status=LayoutAnalysisTaskStatus.succeeded,
            markdown="# done",
            image_hashes={"img-1": "a" * 64},
        )
        succeeded_item = DocumentParsingImageItem(
            document_parsing_task_id=task.id,
            source_key="img-1",
            file_hash="a" * 64,
            extracted_image_id=1,
            status=DocumentParsingImageItemStatus.succeeded,
            result_source=DocumentParsingImageItemResultSource.semantic_snapshot,
        )
        session = _SessionStub()

        with patch.object(service, "get_document_parsing_image_items", return_value=[succeeded_item]):
            service._recompute_document_parsing_task_state(session, task=task, layout_task=layout_task)

        self.assertEqual(task.status, DocumentParsingTaskStatus.succeeded)
        self.assertEqual(task.image_succeeded_count, 1)
        self.assertIsNotNone(task.finished_at)

    def test_recompute_document_parsing_task_state_fails_when_any_image_failed(self):
        task = DocumentParsingTask(
            document_id=uuid4(),
            file_id=uuid4(),
            storage_bucket="softplan",
            storage_key="documents/a.pdf",
            target_layout_model="marker",
            layout_model_key="marker",
            image_model_key="vision-model",
            layout_task_id=uuid4(),
            status=DocumentParsingTaskStatus.running,
        )
        layout_task = LayoutAnalysisTask(
            id=task.layout_task_id,
            document_id=task.document_id,
            file_id=task.file_id,
            storage_bucket="softplan",
            storage_key=task.storage_key,
            target_layout_model="marker",
            layout_model_key="marker",
            status=LayoutAnalysisTaskStatus.succeeded,
            markdown="# done",
        )
        failed_item = DocumentParsingImageItem(
            document_parsing_task_id=task.id,
            source_key="img-1",
            file_hash="a" * 64,
            extracted_image_id=1,
            status=DocumentParsingImageItemStatus.failed,
            error_message="bad image",
        )
        session = _SessionStub()

        with patch.object(service, "get_document_parsing_image_items", return_value=[failed_item]):
            service._recompute_document_parsing_task_state(session, task=task, layout_task=layout_task)

        self.assertEqual(task.status, DocumentParsingTaskStatus.failed)
        self.assertEqual(task.error_message, "Image semantic analysis failed")

    def test_recompute_document_parsing_task_state_fails_when_layout_failed(self):
        task = DocumentParsingTask(
            document_id=uuid4(),
            file_id=uuid4(),
            storage_bucket="softplan",
            storage_key="documents/a.pdf",
            target_layout_model="marker",
            layout_model_key="marker",
            image_model_key="vision-model",
            layout_task_id=uuid4(),
            status=DocumentParsingTaskStatus.running,
        )
        layout_task = LayoutAnalysisTask(
            id=task.layout_task_id,
            document_id=task.document_id,
            file_id=task.file_id,
            storage_bucket="softplan",
            storage_key=task.storage_key,
            target_layout_model="marker",
            layout_model_key="marker",
            status=LayoutAnalysisTaskStatus.failed,
            error_message="layout crashed",
        )
        session = _SessionStub()

        with patch.object(service, "get_document_parsing_image_items", return_value=[]):
            service._recompute_document_parsing_task_state(session, task=task, layout_task=layout_task)

        self.assertEqual(task.status, DocumentParsingTaskStatus.failed)
        self.assertEqual(task.error_message, "layout crashed")

    def test_dispatch_image_items_uses_model_scoped_snapshot_without_creating_task(self):
        task = DocumentParsingTask(
            document_id=uuid4(),
            file_id=uuid4(),
            storage_bucket="softplan",
            storage_key="documents/a.pdf",
            requested_image_model="vision-model",
            target_image_model="vision-model",
            image_model_key="vision-model",
            layout_task_id=uuid4(),
        )
        layout_task = LayoutAnalysisTask(
            id=task.layout_task_id,
            document_id=task.document_id,
            file_id=task.file_id,
            storage_bucket="softplan",
            storage_key=task.storage_key,
            target_layout_model="marker",
            layout_model_key="marker",
            status=LayoutAnalysisTaskStatus.succeeded,
            image_hashes={"img-1": "a" * 64},
        )
        extracted_image = ExtractedImage(
            id=1,
            file_hash="a" * 64,
            storage_bucket="softplan",
            storage_key="images/a.png",
            file_size=1,
            content_type="image/png",
            extension=".png",
            width=10,
            height=10,
        )
        snapshot = ExtractedImageSemanticSnapshot(
            extracted_image_id=1,
            target_model_key="vision-model",
            result_model="vision-model",
            description="done",
        )
        session = _SessionStub()

        with patch.object(service, "get_document_parsing_image_items", return_value=[]), patch.object(
            service,
            "_load_extracted_images_by_hash",
            return_value={"a" * 64: extracted_image},
        ), patch.object(service, "_get_semantic_snapshot", return_value=snapshot), patch.object(
            service,
            "create_or_reuse_extracted_image_semantic_task",
        ) as create_task_mock:
            service._dispatch_image_items_if_needed(session, task=task, layout_task=layout_task)

        create_task_mock.assert_not_called()
        added_item = next(item for item in session.added if isinstance(item, DocumentParsingImageItem))
        self.assertEqual(added_item.status, DocumentParsingImageItemStatus.succeeded)
        self.assertEqual(added_item.result_source, DocumentParsingImageItemResultSource.semantic_snapshot)

    def test_process_document_parsing_tasks_for_layout_task_synchronizes_all_bound_tasks(self):
        session = _ProcessSessionStub([uuid4(), uuid4()])
        with patch.object(service, "Session", return_value=session), patch.object(
            service,
            "synchronize_document_parsing_task",
        ) as sync_mock:
            service.process_document_parsing_tasks_for_layout_task(uuid4())

        self.assertEqual(sync_mock.call_count, 2)
    def test_get_document_parsing_image_semantic_result_prefers_snapshot(self):
        semantic_task_id = uuid4()
        item = DocumentParsingImageItem(
            document_parsing_task_id=uuid4(),
            source_key="img-1",
            file_hash="a" * 64,
            extracted_image_id=1,
            semantic_task_id=semantic_task_id,
            status=DocumentParsingImageItemStatus.succeeded,
        )
        snapshot = ExtractedImageSemanticSnapshot(
            extracted_image_id=1,
            target_model_key="vision-model",
            result_model="snapshot-model",
            description="snapshot description",
            source_task_id=uuid4(),
        )
        session = _SessionStub()
        session.get_results[(ExtractedImageSemanticTask, semantic_task_id)] = ExtractedImageSemanticTask(
            id=semantic_task_id,
            extracted_image_id=1,
            status=ExtractedImageSemanticTaskStatus.succeeded,
            target_model_key="vision-model",
            prompt_path="prompt.txt",
            description="task description",
            result_model="task-model",
        )

        with patch.object(service, "_get_semantic_snapshot", return_value=snapshot):
            result = service.get_document_parsing_image_semantic_result(
                session,
                item=item,
                image_model_key="vision-model",
            )

        self.assertIsNotNone(result)
        self.assertEqual(result.description, "snapshot description")
        self.assertEqual(result.result_model, "snapshot-model")
        self.assertEqual(result.source_task_id, snapshot.source_task_id)

    def test_get_document_parsing_image_semantic_result_falls_back_to_succeeded_task(self):
        semantic_task_id = uuid4()
        item = DocumentParsingImageItem(
            document_parsing_task_id=uuid4(),
            source_key="img-1",
            file_hash="a" * 64,
            extracted_image_id=1,
            semantic_task_id=semantic_task_id,
            status=DocumentParsingImageItemStatus.succeeded,
        )
        semantic_task = ExtractedImageSemanticTask(
            id=semantic_task_id,
            extracted_image_id=1,
            status=ExtractedImageSemanticTaskStatus.succeeded,
            target_model_key="vision-model",
            prompt_path="prompt.txt",
            description="task description",
            result_model="task-model",
        )
        session = _SessionStub()
        session.get_results[(ExtractedImageSemanticTask, semantic_task_id)] = semantic_task

        with patch.object(service, "_get_semantic_snapshot", return_value=None):
            result = service.get_document_parsing_image_semantic_result(
                session,
                item=item,
                image_model_key="vision-model",
            )

        self.assertIsNotNone(result)
        self.assertEqual(result.description, "task description")
        self.assertEqual(result.result_model, "task-model")
        self.assertEqual(result.source_task_id, semantic_task_id)

    def test_get_document_parsing_image_semantic_result_returns_none_for_non_succeeded_task(self):
        semantic_task_id = uuid4()
        item = DocumentParsingImageItem(
            document_parsing_task_id=uuid4(),
            source_key="img-1",
            file_hash="a" * 64,
            extracted_image_id=1,
            semantic_task_id=semantic_task_id,
            status=DocumentParsingImageItemStatus.running,
        )
        session = _SessionStub()
        session.get_results[(ExtractedImageSemanticTask, semantic_task_id)] = ExtractedImageSemanticTask(
            id=semantic_task_id,
            extracted_image_id=1,
            status=ExtractedImageSemanticTaskStatus.running,
            target_model_key="vision-model",
            prompt_path="prompt.txt",
        )

        with patch.object(service, "_get_semantic_snapshot", return_value=None):
            result = service.get_document_parsing_image_semantic_result(
                session,
                item=item,
                image_model_key="vision-model",
            )

        self.assertIsNone(result)

