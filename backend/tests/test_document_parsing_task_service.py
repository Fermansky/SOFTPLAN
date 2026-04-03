from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy.exc import IntegrityError

from backend.app.models import DocumentParsingTask, DocumentParsingTaskStatus
from backend.app.services import document_parsing_task_service as service


class _ExecResult:
    def __init__(self, first_value):
        self._first_value = first_value

    def first(self):
        return self._first_value


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

    def test_create_or_reuse_document_parsing_task_reuses_active_task(self):
        existing = DocumentParsingTask(
            document_id=uuid4(),
            file_id=uuid4(),
            storage_bucket="softplan",
            storage_key="documents/2026/04/a.pdf",
            status=DocumentParsingTaskStatus.running,
        )
        session = _SessionStub(existing_task=existing)

        result = service.create_or_reuse_document_parsing_task(
            session,
            document_id=existing.document_id,
            file_id=existing.file_id,
            storage_bucket=existing.storage_bucket,
            storage_key=existing.storage_key,
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
        )

        self.assertFalse(result.reused)
        self.assertEqual(result.task.document_id, document_id)
        self.assertEqual(result.task.file_id, file_id)
        self.assertTrue(session.committed)
        self.assertEqual(session.refreshed, [result.task])

    def test_create_or_reuse_document_parsing_task_handles_integrity_conflict(self):
        existing = DocumentParsingTask(
            document_id=uuid4(),
            file_id=uuid4(),
            storage_bucket="softplan",
            storage_key="documents/2026/04/a.pdf",
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
            )

        self.assertTrue(result.reused)
        self.assertEqual(result.task, existing)
        self.assertTrue(session.rolled_back)

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

