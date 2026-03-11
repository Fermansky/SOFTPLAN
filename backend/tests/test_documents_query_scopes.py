from unittest import TestCase
from uuid import uuid4

from backend.app.api.routers import documents


class _ExecResult:
    def __init__(self, all_value=None):
        self._all_value = all_value if all_value is not None else []

    def all(self):
        return self._all_value


class _SessionCapture:
    def __init__(self, all_value=None):
        self.last_statement = None
        self.all_value = all_value if all_value is not None else []

    def exec(self, statement):
        self.last_statement = statement
        return _ExecResult(self.all_value)


class DocumentsQueryScopesTests(TestCase):
    def test_list_documents_filters_project(self):
        project_id = uuid4()
        session = _SessionCapture(all_value=[])

        result = documents.list_documents(
            project_id=project_id,
            software_id=None,
            session=session,
            offset=0,
            limit=10,
        )

        where_text = str(session.last_statement.whereclause)
        self.assertIn("documents.deleted_at IS NULL", where_text)
        self.assertIn("documents.project_id", where_text)
        self.assertNotIn("documents.software_id", where_text)
        self.assertEqual(result, [])

    def test_list_documents_filters_software(self):
        software_id = uuid4()
        session = _SessionCapture(all_value=[])

        result = documents.list_documents(
            project_id=None,
            software_id=software_id,
            session=session,
            offset=0,
            limit=10,
        )

        where_text = str(session.last_statement.whereclause)
        self.assertIn("documents.deleted_at IS NULL", where_text)
        self.assertIn("documents.software_id", where_text)
        self.assertNotIn("documents.project_id", where_text)
        self.assertEqual(result, [])

    def test_list_documents_filters_project_and_software(self):
        project_id = uuid4()
        software_id = uuid4()
        session = _SessionCapture(all_value=[])

        result = documents.list_documents(
            project_id=project_id,
            software_id=software_id,
            session=session,
            offset=0,
            limit=10,
        )

        where_text = str(session.last_statement.whereclause)
        self.assertIn("documents.deleted_at IS NULL", where_text)
        self.assertIn("documents.project_id", where_text)
        self.assertIn("documents.software_id", where_text)
        self.assertEqual(result, [])
