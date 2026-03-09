from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

from backend.app.api.routers import project_software_relations
from backend.app.models import ProjectSoftwareRelationCreate


class _SessionCreateRelation:
    def __init__(self):
        self.added = []
        self.committed = False
        self.refreshed = []

    def add(self, item):
        self.added.append(item)

    def commit(self):
        self.committed = True

    def refresh(self, item):
        self.refreshed.append(item)


class ProjectSoftwareRelationVersionTests(TestCase):
    def test_create_schema_allows_missing_version(self):
        payload = ProjectSoftwareRelationCreate.model_validate(
            {"project_id": str(uuid4()), "software_id": str(uuid4())}
        )
        self.assertIsNone(payload.version)

    def test_create_router_accepts_missing_version(self):
        payload = ProjectSoftwareRelationCreate.model_validate(
            {"project_id": str(uuid4()), "software_id": str(uuid4())}
        )
        session = _SessionCreateRelation()

        with patch.object(project_software_relations, "get_active_project_or_404", return_value=object()):
            with patch.object(project_software_relations, "get_software_or_404", return_value=object()):
                relation = project_software_relations.create_project_software_relation(
                    payload=payload,
                    session=session,
                )

        self.assertIsNone(relation.version)
        self.assertTrue(session.committed)
        self.assertEqual(len(session.added), 1)

