from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

from backend.app.models import LayoutAnalysisTask, LayoutAnalysisTaskStatus
from backend.app.services import layout_analysis_task_service as service


class _ExecResult:
    def __init__(self, *, first_value=None, all_values=None):
        self._first_value = first_value
        self._all_values = [] if all_values is None else list(all_values)

    def first(self):
        return self._first_value

    def all(self):
        return list(self._all_values)


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


class _WorkerSessionStub:
    def __init__(self, *, task: LayoutAnalysisTask | None):
        self.task = task
        self.added = []
        self.committed = False
        self.rolled_back = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, model, item_id):
        if model is service.LayoutAnalysisTask and self.task is not None and self.task.id == item_id:
            return self.task
        return None

    def add(self, item):
        self.added.append(item)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


class LayoutAnalysisTaskServiceTests(TestCase):
    def test_recover_orphaned_layout_analysis_tasks_synchronizes_document_parsing_tasks(self):
        running_tasks = [
            LayoutAnalysisTask(
                id=uuid4(),
                document_id=uuid4(),
                file_id=uuid4(),
                storage_bucket="softplan",
                storage_key="documents/a.pdf",
                target_layout_model="marker",
                layout_model_key="marker",
                status=LayoutAnalysisTaskStatus.running,
            ),
            LayoutAnalysisTask(
                id=uuid4(),
                document_id=uuid4(),
                file_id=uuid4(),
                storage_bucket="softplan",
                storage_key="documents/b.pdf",
                target_layout_model="marker",
                layout_model_key="marker",
                status=LayoutAnalysisTaskStatus.running,
            ),
        ]
        session = _RecoverySessionStub(running_tasks)

        with patch.object(service, "Session", return_value=session), patch.object(
            service,
            "_synchronize_document_parsing_tasks",
        ) as synchronize_mock:
            recovered = service.recover_orphaned_layout_analysis_tasks()

        self.assertEqual(recovered, 2)
        self.assertTrue(session.committed)
        self.assertEqual(synchronize_mock.call_count, 2)
        synchronize_mock.assert_any_call(running_tasks[0].id)
        synchronize_mock.assert_any_call(running_tasks[1].id)
        self.assertEqual(running_tasks[0].status, LayoutAnalysisTaskStatus.failed)
        self.assertEqual(running_tasks[1].status, LayoutAnalysisTaskStatus.failed)

    def test_execute_layout_analysis_task_failure_synchronizes_document_parsing_tasks(self):
        task = LayoutAnalysisTask(
            id=uuid4(),
            document_id=uuid4(),
            file_id=uuid4(),
            storage_bucket="softplan",
            storage_key="documents/a.pdf",
            target_layout_model="marker",
            layout_model_key="marker",
            status=LayoutAnalysisTaskStatus.running,
        )
        session = _WorkerSessionStub(task=task)
        client = SimpleNamespace(convert_pdf_to_markdown=lambda **kwargs: (None, "upstream failed"))

        with patch.object(service, "Session", return_value=session), patch.object(
            service,
            "_synchronize_document_parsing_tasks",
        ) as synchronize_mock:
            service.execute_layout_analysis_task(task.id, client=client)

        self.assertTrue(session.committed)
        self.assertEqual(task.status, LayoutAnalysisTaskStatus.failed)
        self.assertEqual(task.error_message, "upstream failed")
        synchronize_mock.assert_called_once_with(task.id)
