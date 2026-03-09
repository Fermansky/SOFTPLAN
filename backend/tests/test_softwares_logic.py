from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException

from backend.app.api import dependencies
from backend.app.api.routers import softwares
from backend.app.models import Software


class _ExecResult:
    def __init__(self, first_value=None, all_value=None):
        self._first_value = first_value
        self._all_value = all_value if all_value is not None else []

    def first(self):
        return self._first_value

    def all(self):
        return self._all_value


class _SessionCapture:
    def __init__(self, first_value=None, all_value=None):
        self.first_value = first_value
        self.all_value = all_value if all_value is not None else []
        self.last_statement = None
        self.added = []
        self.committed = False

    def exec(self, statement):
        self.last_statement = statement
        return _ExecResult(self.first_value, self.all_value)

    def add(self, item):
        self.added.append(item)

    def commit(self):
        self.committed = True


class SoftwaresLogicTests(TestCase):
    def test_get_software_or_404_filters_out_deleted(self):
        session = _SessionCapture(first_value=None)

        with self.assertRaises(HTTPException) as ctx:
            dependencies.get_software_or_404(uuid4(), session)

        self.assertEqual(ctx.exception.status_code, 404)
        stmt_text = str(session.last_statement)
        self.assertIn("softwares.deleted_at", stmt_text)
        self.assertIn("IS NULL", stmt_text.upper())

    def test_list_softwares_statement_filters_out_deleted(self):
        session = _SessionCapture(all_value=[])
        softwares.list_softwares(session=session, offset=0, limit=10)

        stmt_text = str(session.last_statement)
        self.assertIn("softwares.deleted_at", stmt_text)
        self.assertIn("IS NULL", stmt_text.upper())

    def test_delete_software_is_logical_delete(self):
        software = Software(code="SW-001", name="Core", description="d")
        session = _SessionCapture()

        with patch.object(softwares, "get_software_or_404", return_value=software):
            response = softwares.delete_software(software_id=software.id, session=session)

        self.assertEqual(response.status_code, 204)
        self.assertIsNotNone(software.deleted_at)
        self.assertIsNotNone(software.updated_at)
        self.assertIn(software, session.added)
        self.assertTrue(session.committed)
