import asyncio
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException
from minio.error import S3Error

from backend.app.api.routers import documents
from backend.app.models import Document, DocumentCreate, FileRecord


class _UploadFileStub:
    def __init__(self, content: bytes, *, filename: str, content_type: str):
        self._content = content
        self.filename = filename
        self.content_type = content_type

    async def read(self) -> bytes:
        return self._content


class _StoredObjectRef:
    def __init__(self, *, bucket: str, storage_key: str):
        self.bucket = bucket
        self.storage_key = storage_key


class _StorageStub:
    def __init__(
        self,
        *,
        object_exists_result: bool = True,
        download_payload: bytes = b"",
        download_error: S3Error | None = None,
    ):
        self.bucket = "softplan-documents"
        self.documents_bucket = "softplan-documents"
        self.images_bucket = "softplan-images"
        self.object_exists_result = object_exists_result
        self.download_payload = download_payload
        self.download_error = download_error
        self.upload_calls = []
        self.upload_document_calls = []
        self.removed = []
        self.exists_calls = []
        self.download_calls = []

    def upload_bytes(self, payload: bytes, *, content_type: str, extension: str = "", bucket: str | None = None) -> str:
        self.upload_calls.append(
            {"payload": payload, "content_type": content_type, "extension": extension, "bucket": bucket}
        )
        return "upload/key.pdf"

    def upload_document_bytes(self, payload: bytes, *, content_type: str, extension: str = "") -> _StoredObjectRef:
        self.upload_document_calls.append(
            {"payload": payload, "content_type": content_type, "extension": extension}
        )
        return _StoredObjectRef(bucket=self.documents_bucket, storage_key="documents/2026/04/upload-key.pdf")

    def remove_object(self, storage_key: str, *, bucket: str | None = None) -> None:
        self.removed.append({"storage_key": storage_key, "bucket": bucket})

    def object_exists(self, storage_key: str, *, bucket: str | None = None) -> bool:
        self.exists_calls.append({"storage_key": storage_key, "bucket": bucket})
        return self.object_exists_result

    def download_bytes(self, storage_key: str, *, bucket: str | None = None) -> bytes:
        self.download_calls.append({"storage_key": storage_key, "bucket": bucket})
        if self.download_error is not None:
            raise self.download_error
        return self.download_payload


class _ExecResult:
    def __init__(self, first_value):
        self._first_value = first_value

    def first(self):
        return self._first_value


class _SessionCapture:
    def __init__(self, exec_first_values=None):
        self.added = []
        self.flushed = False
        self.committed = False
        self.rolled_back = False
        self.refreshed = []
        self.exec_first_values = list(exec_first_values or [])

    def add(self, item):
        self.added.append(item)

    def exec(self, statement):
        first_value = self.exec_first_values.pop(0) if self.exec_first_values else None
        return _ExecResult(first_value)

    def flush(self):
        self.flushed = True

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def refresh(self, item):
        self.refreshed.append(item)


class DocumentsUploadFlowTests(TestCase):
    def test_upload_document_inserts_file_then_document(self):
        session = _SessionCapture(exec_first_values=[None])
        storage = _StorageStub(object_exists_result=True)
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
        self.assertEqual(storage.upload_document_calls[0]["extension"], ".pdf")
        self.assertEqual(created_document.name, "requirements.pdf")
        self.assertEqual(session.added[0].storage_bucket, "softplan-documents")
        self.assertTrue(session.flushed)
        self.assertTrue(session.committed)

    def test_upload_document_reuses_existing_file_by_hash(self):
        existing_file = FileRecord(
            file_hash="existing-hash",
            storage_bucket="softplan",
            storage_key="existing-key.pdf",
            file_size=5,
            content_type="application/pdf",
            extension=".pdf",
        )
        session = _SessionCapture(exec_first_values=[existing_file])
        storage = _StorageStub(object_exists_result=True)
        upload_file = _UploadFileStub(
            b"hello-softplan",
            filename="requirements.pdf",
            content_type="application/pdf",
        )

        with patch.object(documents, "get_active_project_or_404", return_value=object()):
            created_document = asyncio.run(
                documents.upload_document(
                    project_id=uuid4(),
                    software_id=None,
                    name=None,
                    description="desc",
                    extra_info=None,
                    file=upload_file,
                    session=session,
                    storage=storage,
                )
            )

        self.assertEqual(len(session.added), 1)
        self.assertEqual(created_document.file_id, existing_file.id)
        self.assertEqual(storage.upload_document_calls, [])
        self.assertEqual(storage.exists_calls, [{"storage_key": existing_file.storage_key, "bucket": existing_file.storage_bucket}])
        self.assertFalse(session.flushed)
        self.assertTrue(session.committed)

    def test_upload_document_repairs_missing_existing_object(self):
        existing_file = FileRecord(
            file_hash="existing-hash",
            storage_bucket="softplan",
            storage_key="missing-key.pdf",
            file_size=5,
            content_type="application/pdf",
            extension=".pdf",
        )
        session = _SessionCapture(exec_first_values=[existing_file])
        storage = _StorageStub(object_exists_result=False)
        upload_file = _UploadFileStub(
            b"hello-softplan",
            filename="requirements.pdf",
            content_type="application/pdf",
        )

        with patch.object(documents, "get_active_project_or_404", return_value=object()):
            created_document = asyncio.run(
                documents.upload_document(
                    project_id=uuid4(),
                    software_id=None,
                    name=None,
                    description="desc",
                    extra_info=None,
                    file=upload_file,
                    session=session,
                    storage=storage,
                )
            )

        self.assertEqual(len(storage.upload_document_calls), 1)
        self.assertEqual(existing_file.storage_key, "documents/2026/04/upload-key.pdf")
        self.assertEqual(existing_file.storage_bucket, "softplan-documents")
        self.assertEqual(len(session.added), 2)
        self.assertEqual(created_document.file_id, existing_file.id)
        self.assertTrue(session.committed)

    def test_upload_document_rejects_non_object_extra_info(self):
        session = _SessionCapture(exec_first_values=[None])
        storage = _StorageStub(object_exists_result=True)
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
        self.assertEqual(storage.upload_document_calls, [])


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


class DocumentsDownloadTests(TestCase):
    def test_download_document_returns_attachment(self):
        file_record = FileRecord(
            file_hash="hash-1",
            storage_bucket="softplan",
            storage_key="doc/key.pdf",
            file_size=12,
            content_type="application/pdf",
            extension=".pdf",
        )
        document = Document(project_id=uuid4(), file_id=file_record.id, name="requirements")
        storage = _StorageStub(download_payload=b"hello world!")
        session = _SessionCapture()

        with patch.object(documents, "get_active_document_or_404", return_value=document):
            with patch.object(documents, "get_file_or_404", return_value=file_record):
                response = documents.download_document(document_id=document.id, session=session, storage=storage)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.body, b"hello world!")
        self.assertEqual(response.media_type, "application/pdf")
        self.assertIn("attachment;", response.headers["content-disposition"])
        self.assertIn("requirements.pdf", response.headers["content-disposition"])
        self.assertEqual(
            storage.download_calls,
            [{"storage_key": "doc/key.pdf", "bucket": "softplan"}],
        )

    def test_download_document_without_file_id_returns_404(self):
        document = Document(project_id=uuid4(), file_id=None, name="only-meta")
        storage = _StorageStub(download_payload=b"")
        session = _SessionCapture()

        with patch.object(documents, "get_active_document_or_404", return_value=document):
            with patch.object(documents, "get_file_or_404") as get_file_mock:
                with self.assertRaises(HTTPException) as ctx:
                    documents.download_document(document_id=document.id, session=session, storage=storage)

        self.assertEqual(ctx.exception.status_code, 404)
        get_file_mock.assert_not_called()

    def test_download_document_when_storage_object_missing_returns_404(self):
        file_record = FileRecord(
            file_hash="hash-2",
            storage_bucket="softplan",
            storage_key="missing/key.pdf",
            file_size=10,
            content_type="application/pdf",
            extension=".pdf",
        )
        document = Document(project_id=uuid4(), file_id=file_record.id, name="requirements")
        storage_error = S3Error("NoSuchKey", "missing", None, None, None, None)
        storage = _StorageStub(download_payload=b"", download_error=storage_error)
        session = _SessionCapture()

        with patch.object(documents, "get_active_document_or_404", return_value=document):
            with patch.object(documents, "get_file_or_404", return_value=file_record):
                with self.assertRaises(HTTPException) as ctx:
                    documents.download_document(document_id=document.id, session=session, storage=storage)

        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(
            storage.download_calls,
            [{"storage_key": "missing/key.pdf", "bucket": "softplan"}],
        )
