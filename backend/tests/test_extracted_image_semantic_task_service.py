from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy.exc import IntegrityError

from backend.app.models import ExtractedImage, ExtractedImageSemanticTask, ExtractedImageSemanticTaskStatus
from backend.app.services import extracted_image_semantic_task_service as service


class _ExecResult:
    def __init__(self, first_value=None, all_values=None):
        self._first_value = first_value
        self._all_values = all_values if all_values is not None else []

    def first(self):
        return self._first_value

    def all(self):
        return self._all_values


class _SessionStub:
    def __init__(self, *, existing_task: ExtractedImageSemanticTask | None = None, raise_integrity_on_commit: bool = False):
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


class ExtractedImageSemanticTaskServiceTests(TestCase):
    def _build_image(self) -> ExtractedImage:
        return ExtractedImage(
            id=1,
            file_hash="a" * 64,
            storage_bucket="softplan",
            storage_key="images/a.png",
            file_size=123,
            content_type="image/png",
            extension=".png",
            width=100,
            height=200,
        )

    def _build_task(self, *, overwrite_existing_snapshot: bool = False) -> ExtractedImageSemanticTask:
        return ExtractedImageSemanticTask(
            id=uuid4(),
            extracted_image_id=1,
            status=ExtractedImageSemanticTaskStatus.running,
            requested_model="request-model",
            target_model="request-model",
            target_model_key="request-model",
            overwrite_existing_snapshot=overwrite_existing_snapshot,
            request_id="req-1",
            prompt_path="backend/app/prompts/extracted_image_semantic.txt",
        )

    def test_process_one_pending_task_returns_false_when_no_task(self):
        with patch.object(service, "claim_next_pending_extracted_image_semantic_task_id", return_value=None):
            with patch.object(service, "execute_extracted_image_semantic_task") as execute_mock:
                processed = service.process_one_pending_extracted_image_semantic_task()

        self.assertFalse(processed)
        execute_mock.assert_not_called()

    def test_process_one_pending_task_executes_claimed_task(self):
        task_id = uuid4()

        with patch.object(service, "claim_next_pending_extracted_image_semantic_task_id", return_value=task_id):
            with patch.object(service, "execute_extracted_image_semantic_task") as execute_mock:
                processed = service.process_one_pending_extracted_image_semantic_task()

        self.assertTrue(processed)
        execute_mock.assert_called_once_with(task_id, client=None, storage=None)

    def test_get_active_task_filters_by_overwrite_flag(self):
        existing = self._build_task(overwrite_existing_snapshot=True)
        session = _SessionStub(existing_task=existing)

        result = service.get_active_extracted_image_semantic_task(
            session,
            extracted_image_id=1,
            target_model_key="request-model",
            overwrite_existing_snapshot=True,
        )

        self.assertEqual(result, existing)
        where_clause = str(session.last_statement.whereclause)
        self.assertIn("extracted_image_semantic_tasks.overwrite_existing_snapshot", where_clause)
        self.assertIn("true", where_clause.lower())

    def test_create_or_reuse_task_reuses_active_task_with_same_overwrite_flag(self):
        existing = self._build_task(overwrite_existing_snapshot=True)
        session = _SessionStub(existing_task=existing)

        result = service.create_or_reuse_extracted_image_semantic_task(
            session,
            extracted_image=self._build_image(),
            requested_model="request-model",
            request_id="req-1",
            overwrite_existing_snapshot=True,
        )

        self.assertTrue(result.reused)
        self.assertEqual(result.task, existing)
        self.assertEqual(session.added, [])

    def test_create_or_reuse_task_creates_new_task_with_default_overwrite_false(self):
        session = _SessionStub(existing_task=None)

        with patch.object(service, "get_extracted_image_semantic_prompt_snapshot", return_value=("prompt-path", "hash-1")), patch(
            "backend.app.services.extracted_image_semantic_task_service.resolve_extracted_image_semantic_model",
            return_value=None,
        ):
            result = service.create_or_reuse_extracted_image_semantic_task(
                session,
                extracted_image=self._build_image(),
                requested_model=None,
                request_id="req-2",
            )

        self.assertFalse(result.reused)
        self.assertEqual(result.task.extracted_image_id, 1)
        self.assertEqual(result.task.target_model, None)
        self.assertEqual(result.task.target_model_key, "__LLM_SERVICE_DEFAULT__")
        self.assertFalse(result.task.overwrite_existing_snapshot)
        self.assertEqual(result.task.prompt_path, "prompt-path")
        self.assertEqual(result.task.prompt_hash, "hash-1")
        self.assertTrue(session.committed)
        self.assertEqual(session.refreshed, [result.task])

    def test_create_or_reuse_task_can_use_resolved_target_model_without_reresolving(self):
        session = _SessionStub(existing_task=None)

        with patch.object(service, "get_extracted_image_semantic_prompt_snapshot", return_value=("prompt-path", "hash-1")), patch(
            "backend.app.services.extracted_image_semantic_task_service.resolve_extracted_image_semantic_model",
            side_effect=AssertionError("should not resolve"),
        ):
            result = service.create_or_reuse_extracted_image_semantic_task(
                session,
                extracted_image=self._build_image(),
                requested_model=None,
                target_model=None,
                use_target_model=True,
                request_id="req-2b",
            )

        self.assertFalse(result.reused)
        self.assertEqual(result.task.target_model, None)
        self.assertEqual(result.task.target_model_key, "__LLM_SERVICE_DEFAULT__")

    def test_create_or_reuse_task_persists_overwrite_true(self):
        session = _SessionStub(existing_task=None)

        with patch.object(service, "get_extracted_image_semantic_prompt_snapshot", return_value=("prompt-path", "hash-1")):
            result = service.create_or_reuse_extracted_image_semantic_task(
                session,
                extracted_image=self._build_image(),
                requested_model="request-model",
                request_id="req-3",
                overwrite_existing_snapshot=True,
            )

        self.assertFalse(result.reused)
        self.assertTrue(result.task.overwrite_existing_snapshot)

    def test_create_or_reuse_task_handles_integrity_conflict_with_same_overwrite_flag(self):
        existing = self._build_task(overwrite_existing_snapshot=True)
        session = _SessionStub(existing_task=None, raise_integrity_on_commit=True)

        with patch.object(service, "get_extracted_image_semantic_prompt_snapshot", return_value=("prompt-path", "hash-1")), patch.object(
            service,
            "get_active_extracted_image_semantic_task",
            side_effect=[None, existing],
        ) as get_existing_mock:
            result = service.create_or_reuse_extracted_image_semantic_task(
                session,
                extracted_image=self._build_image(),
                requested_model="request-model",
                request_id="req-4",
                overwrite_existing_snapshot=True,
            )

        self.assertTrue(result.reused)
        self.assertEqual(result.task, existing)
        self.assertTrue(session.rolled_back)
        self.assertEqual(get_existing_mock.call_args_list[0].kwargs["overwrite_existing_snapshot"], True)
        self.assertEqual(get_existing_mock.call_args_list[1].kwargs["overwrite_existing_snapshot"], True)

    def test_get_latest_task_filters_by_image(self):
        task = ExtractedImageSemanticTask(
            id=uuid4(),
            extracted_image_id=1,
            status=ExtractedImageSemanticTaskStatus.succeeded,
            requested_model=None,
            target_model=None,
            target_model_key="__LLM_SERVICE_DEFAULT__",
            overwrite_existing_snapshot=False,
            request_id="req-4",
            prompt_path="backend/app/prompts/extracted_image_semantic.txt",
        )
        session = _SessionStub(existing_task=task)

        result = service.get_latest_extracted_image_semantic_task_for_image(session, extracted_image_id=1)

        self.assertEqual(result, task)
        self.assertIn("extracted_image_semantic_tasks.extracted_image_id", str(session.last_statement.whereclause))
        self.assertIn("ORDER BY extracted_image_semantic_tasks.created_at DESC", str(session.last_statement))


class _WorkerSessionStub:
    def __init__(self, *, task: ExtractedImageSemanticTask | None, image: ExtractedImage | None):
        self.task = task
        self.image = image
        self.added = []
        self.committed = False
        self.rolled_back = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, model, item_id):
        if model is service.ExtractedImageSemanticTask:
            return self.task if self.task is not None and self.task.id == item_id else None
        if model is service.ExtractedImage:
            return self.image if self.image is not None and self.image.id == item_id else None
        return None

    def exec(self, statement):
        return _ExecResult(all_values=[])

    def add(self, item):
        self.added.append(item)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


class _RecoverySessionStub:
    def __init__(self, tasks):
        self.tasks = tasks
        self.added = []
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def exec(self, statement):
        return _ExecResult(all_values=self.tasks)

    def add(self, item):
        self.added.append(item)

    def commit(self):
        self.committed = True


class ExtractedImageSemanticTaskExecutionTests(TestCase):
    def _build_running_task(self, *, overwrite_existing_snapshot: bool = False) -> ExtractedImageSemanticTask:
        return ExtractedImageSemanticTask(
            id=uuid4(),
            extracted_image_id=1,
            status=ExtractedImageSemanticTaskStatus.running,
            requested_model="request-model",
            target_model="target-model",
            target_model_key="target-model",
            overwrite_existing_snapshot=overwrite_existing_snapshot,
            request_id="req-7",
            prompt_path="backend/app/prompts/extracted_image_semantic.txt",
        )

    def _build_image(self) -> ExtractedImage:
        return ExtractedImage(
            id=1,
            file_hash="a" * 64,
            storage_bucket="softplan",
            storage_key="images/a.png",
            file_size=123,
            content_type="image/png",
            extension=".png",
            width=100,
            height=200,
        )

    def test_execute_task_success_updates_image_snapshot_when_empty(self):
        task = self._build_running_task(overwrite_existing_snapshot=False)
        image = self._build_image()
        session = _WorkerSessionStub(task=task, image=image)

        with patch.object(service, "Session", return_value=session):
            with patch.object(service, "_synchronize_document_parsing_tasks") as synchronize_mock:
                with patch.object(
                    service,
                    "execute_extracted_image_semantic_recognition",
                    return_value=SimpleNamespace(
                        succeeded=True,
                        description="fresh description",
                        result_model="qwen-test",
                    ),
                ):
                    service.execute_extracted_image_semantic_task(task.id, client=object(), storage=object())

        synchronize_mock.assert_called_once_with(task.id)
        self.assertTrue(session.committed)
        self.assertEqual(task.status, ExtractedImageSemanticTaskStatus.succeeded)
        self.assertEqual(task.description, "fresh description")
        self.assertEqual(image.semantic_description, "fresh description")
        self.assertEqual(image.semantic_description_model, "qwen-test")
        self.assertIsNotNone(image.semantic_description_updated_at)
        self.assertIn(image, session.added)
        self.assertIn(task, session.added)

    def test_execute_task_success_does_not_overwrite_existing_snapshot_by_default(self):
        task = self._build_running_task(overwrite_existing_snapshot=False)
        image = self._build_image()
        image.semantic_description = "existing description"
        image.semantic_description_model = "old-model"
        session = _WorkerSessionStub(task=task, image=image)

        with patch.object(service, "Session", return_value=session):
            with patch.object(service, "_synchronize_document_parsing_tasks") as synchronize_mock:
                with patch.object(
                    service,
                    "execute_extracted_image_semantic_recognition",
                    return_value=SimpleNamespace(
                        succeeded=True,
                        description="new description",
                        result_model="new-model",
                    ),
                ):
                    service.execute_extracted_image_semantic_task(task.id, client=object(), storage=object())

        synchronize_mock.assert_called_once_with(task.id)
        self.assertTrue(session.committed)
        self.assertEqual(task.status, ExtractedImageSemanticTaskStatus.succeeded)
        self.assertEqual(task.description, "new description")
        self.assertEqual(task.result_model, "new-model")
        self.assertEqual(image.semantic_description, "existing description")
        self.assertEqual(image.semantic_description_model, "old-model")
        self.assertNotIn(image, session.added)
        self.assertIn(task, session.added)

    def test_recover_orphaned_tasks_synchronizes_document_parsing_tasks(self):
        running_tasks = [
            ExtractedImageSemanticTask(
                id=uuid4(),
                extracted_image_id=1,
                status=ExtractedImageSemanticTaskStatus.running,
                requested_model="request-model",
                target_model="target-model",
                target_model_key="target-model",
                overwrite_existing_snapshot=False,
                request_id="req-8",
                prompt_path="backend/app/prompts/extracted_image_semantic.txt",
            ),
            ExtractedImageSemanticTask(
                id=uuid4(),
                extracted_image_id=2,
                status=ExtractedImageSemanticTaskStatus.running,
                requested_model="request-model",
                target_model="target-model",
                target_model_key="target-model",
                overwrite_existing_snapshot=False,
                request_id="req-9",
                prompt_path="backend/app/prompts/extracted_image_semantic.txt",
            ),
        ]
        session = _RecoverySessionStub(running_tasks)

        with patch.object(service, "Session", return_value=session), patch.object(
            service,
            "_synchronize_document_parsing_tasks",
        ) as synchronize_mock:
            recovered = service.recover_orphaned_extracted_image_semantic_tasks()

        self.assertEqual(recovered, 2)
        self.assertTrue(session.committed)
        self.assertEqual(synchronize_mock.call_count, 2)
        synchronize_mock.assert_any_call(running_tasks[0].id)
        synchronize_mock.assert_any_call(running_tasks[1].id)
        self.assertEqual(running_tasks[0].status, ExtractedImageSemanticTaskStatus.failed)
        self.assertEqual(running_tasks[1].status, ExtractedImageSemanticTaskStatus.failed)

    def test_execute_task_success_overwrites_existing_snapshot_when_requested(self):
        task = self._build_running_task(overwrite_existing_snapshot=True)
        image = self._build_image()
        image.semantic_description = "existing description"
        image.semantic_description_model = "old-model"
        session = _WorkerSessionStub(task=task, image=image)

        with patch.object(service, "Session", return_value=session):
            with patch.object(service, "_synchronize_document_parsing_tasks") as synchronize_mock:
                with patch.object(
                    service,
                    "execute_extracted_image_semantic_recognition",
                    return_value=SimpleNamespace(
                        succeeded=True,
                        description="new description",
                        result_model="new-model",
                    ),
                ):
                    service.execute_extracted_image_semantic_task(task.id, client=object(), storage=object())

        synchronize_mock.assert_called_once_with(task.id)
        self.assertTrue(session.committed)
        self.assertEqual(image.semantic_description, "new description")
        self.assertEqual(image.semantic_description_model, "new-model")
        self.assertIsNotNone(image.semantic_description_updated_at)
        self.assertIn(image, session.added)

    def test_execute_task_failure_keeps_existing_image_snapshot(self):
        task = self._build_running_task(overwrite_existing_snapshot=True)
        image = self._build_image()
        image.semantic_description = "existing description"
        image.semantic_description_model = "old-model"
        session = _WorkerSessionStub(task=task, image=image)

        with patch.object(service, "Session", return_value=session):
            with patch.object(service, "_synchronize_document_parsing_tasks") as synchronize_mock:
                with patch.object(
                    service,
                    "execute_extracted_image_semantic_recognition",
                    return_value=SimpleNamespace(
                        succeeded=False,
                        error_message="upstream failed",
                    ),
                ):
                    service.execute_extracted_image_semantic_task(task.id, client=object(), storage=object())

        synchronize_mock.assert_called_once_with(task.id)
        self.assertTrue(session.committed)
        self.assertEqual(task.status, ExtractedImageSemanticTaskStatus.failed)
        self.assertEqual(task.error_message, "upstream failed")
        self.assertEqual(image.semantic_description, "existing description")
        self.assertEqual(image.semantic_description_model, "old-model")

