from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy.exc import IntegrityError

from backend.app.models import ExtractedImage, ExtractedImageSemanticTask, ExtractedImageSemanticTaskStatus
from backend.app.services import extracted_image_semantic_task_service as service


class _ExecResult:
    def __init__(self, first_value):
        self._first_value = first_value

    def first(self):
        return self._first_value


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
        return _ExecResult(self._existing_task)

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

    def test_create_or_reuse_task_reuses_active_task(self):
        existing = ExtractedImageSemanticTask(
            id=uuid4(),
            extracted_image_id=1,
            status=ExtractedImageSemanticTaskStatus.running,
            requested_model="request-model",
            target_model="request-model",
            target_model_key="request-model",
            request_id="req-1",
            prompt_path="backend/app/prompts/extracted_image_semantic.txt",
        )
        session = _SessionStub(existing_task=existing)

        result = service.create_or_reuse_extracted_image_semantic_task(
            session,
            extracted_image=self._build_image(),
            requested_model="request-model",
            request_id="req-1",
        )

        self.assertTrue(result.reused)
        self.assertEqual(result.task, existing)
        self.assertEqual(session.added, [])

    def test_create_or_reuse_task_creates_new_task_and_uses_default_model_key(self):
        session = _SessionStub(existing_task=None)

        with patch.object(service, "get_extracted_image_semantic_prompt_snapshot", return_value=("prompt-path", "hash-1")), patch(
            "backend.app.services.extracted_image_semantic_task_service.os.getenv",
            side_effect=lambda key, default=None: default,
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
        self.assertEqual(result.task.prompt_path, "prompt-path")
        self.assertEqual(result.task.prompt_hash, "hash-1")
        self.assertTrue(session.committed)
        self.assertEqual(session.refreshed, [result.task])

    def test_create_or_reuse_task_handles_integrity_conflict(self):
        existing = ExtractedImageSemanticTask(
            id=uuid4(),
            extracted_image_id=1,
            status=ExtractedImageSemanticTaskStatus.pending,
            requested_model="request-model",
            target_model="request-model",
            target_model_key="request-model",
            request_id="req-1",
            prompt_path="backend/app/prompts/extracted_image_semantic.txt",
        )
        session = _SessionStub(existing_task=None, raise_integrity_on_commit=True)

        with patch.object(service, "get_extracted_image_semantic_prompt_snapshot", return_value=("prompt-path", "hash-1")), patch.object(
            service,
            "get_active_extracted_image_semantic_task",
            side_effect=[None, existing],
        ):
            result = service.create_or_reuse_extracted_image_semantic_task(
                session,
                extracted_image=self._build_image(),
                requested_model="request-model",
                request_id="req-3",
            )

        self.assertTrue(result.reused)
        self.assertEqual(result.task, existing)
        self.assertTrue(session.rolled_back)

    def test_get_latest_task_filters_by_image(self):
        task = ExtractedImageSemanticTask(
            id=uuid4(),
            extracted_image_id=1,
            status=ExtractedImageSemanticTaskStatus.succeeded,
            requested_model=None,
            target_model=None,
            target_model_key="__LLM_SERVICE_DEFAULT__",
            request_id="req-4",
            prompt_path="backend/app/prompts/extracted_image_semantic.txt",
        )
        session = _SessionStub(existing_task=task)

        result = service.get_latest_extracted_image_semantic_task_for_image(session, extracted_image_id=1)

        self.assertEqual(result, task)
        self.assertIn("extracted_image_semantic_tasks.extracted_image_id", str(session.last_statement.whereclause))
        self.assertIn("ORDER BY extracted_image_semantic_tasks.created_at DESC", str(session.last_statement))
