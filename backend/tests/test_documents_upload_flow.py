import asyncio
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException

from backend.app.api.routers import documents
from backend.app.models import DocumentCreate, FileRecord


class _UploadFileStub:
    def __init__(self, content: bytes, *, filename: str, content_type: str):
        self._content = content
        self.filename = filename
        self.content_type = content_type

    async def read(self) -> bytes:
        return self._content


class _StorageStub:
    def __init__(self):
        self.bucket = "softplan"
        self.upload_calls = []
        self.removed = []

    def upload_bytes(self, payload: bytes, *, content_type: str, extension: str = "") -> str:
        self.upload_calls.append(
            {"payload": payload, "content_type": content_type, "extension": extension}
        )
        return "upload/key.pdf"

    def remove_object(self, storage_key: str) -> None:
        self.removed.append(storage_key)


class _SessionCapture:
    def __init__(self):
        self.added = []
        self.flushed = False
        self.committed = False
        self.refreshed = []

    def add(self, item):
        self.added.append(item)

    def flush(self):
        self.flushed = True

    def commit(self):
        self.committed = True

    def refresh(self, item):
        self.refreshed.append(item)


class DocumentsUploadFlowTests(TestCase):
    def test_upload_document_inserts_file_then_document(self):
        session = _SessionCapture()
        storage = _StorageStub()
        upload_file = _UploadFileStub(
            b"hello-softplan",
            filename="requirements.pdf",
            content_type="application/pdf",
        )
        project_id = uuid4()

        with patch.object(documents, "get_active_project_or_404", return_value=object()):
            created_document = asyncio.run(
                documents.upload_document(
                    project_id=project_id,
                    software_id=None,
                    name=None,
                    description="desc",
                    extra_info='{"source":"prd"}',
                    file=upload_file,
                    session=session,
                    storage=storage,
                )
            )

        self.assertEqual(len(session.added), 2)
        self.assertIsInstance(session.added[0], FileRecord)
        self.assertEqual(session.added[1].file_id, session.added[0].id)
        self.assertEqual(created_document.file_id, session.added[0].id)
        self.assertEqual(storage.upload_calls[0]["extension"], ".pdf")
        self.assertEqual(created_document.name, "requirements.pdf")
        self.assertTrue(session.flushed)
        self.assertTrue(session.committed)

    def test_upload_document_rejects_non_object_extra_info(self):
        session = _SessionCapture()
        storage = _StorageStub()
        upload_file = _UploadFileStub(b"abc", filename="doc.txt", content_type="text/plain")

        with patch.object(documents, "get_active_project_or_404", return_value=object()):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(
                    documents.upload_document(
                        project_id=uuid4(),
                        software_id=None,
                        name="Doc",
                        description="",
                        extra_info='["bad"]',
                        file=upload_file,
                        session=session,
                        storage=storage,
                    )
                )

        self.assertEqual(ctx.exception.status_code, 422)
        self.assertEqual(storage.upload_calls, [])


class DocumentsCreateTests(TestCase):
    def test_create_document_validates_file_id(self):
        payload = DocumentCreate.model_validate(
            {"file_id": str(uuid4()), "project_id": str(uuid4()), "name": "PRD"}
        )
        session = _SessionCapture()

        with patch.object(documents, "get_active_project_or_404", return_value=object()):
            with patch.object(documents, "get_file_or_404", return_value=object()) as get_file_mock:
                created_document = documents.create_document(payload=payload, session=session)

        get_file_mock.assert_called_once()
        self.assertEqual(created_document.file_id, payload.file_id)
        self.assertTrue(session.committed)
