import asyncio
from unittest import TestCase
from uuid import uuid4

from sqlalchemy.exc import IntegrityError

from backend.app.models import FileRecord
from backend.app.services import document_upload_service as service


class _UploadFileStub:
    def __init__(self, content: bytes, *, filename: str, content_type: str):
        self._content = content
        self.filename = filename
        self.content_type = content_type

    async def read(self) -> bytes:
        return self._content


class _StorageStub:
    def __init__(self, *, object_exists_result: bool = True):
        self.bucket = "softplan"
        self.object_exists_result = object_exists_result
        self.upload_document_calls = []
        self.exists_calls = []
        self.removed = []

    def upload_document_bytes(self, payload: bytes, *, content_type: str, extension: str = ""):
        self.upload_document_calls.append(
            {"payload": payload, "content_type": content_type, "extension": extension}
        )
        return service.StoredObjectRef(bucket=self.bucket, storage_key="documents/2026/04/upload-key.pdf")

    def object_exists(self, storage_key: str, *, bucket: str | None = None) -> bool:
        self.exists_calls.append({"storage_key": storage_key, "bucket": bucket})
        return self.object_exists_result

    def remove_object(self, storage_key: str, *, bucket: str | None = None) -> None:
        self.removed.append({"storage_key": storage_key, "bucket": bucket})


class _ExecResult:
    def __init__(self, first_value):
        self._first_value = first_value

    def first(self):
        return self._first_value


class _SessionCapture:
    def __init__(self, *, exec_first_values=None, raise_on_flush: bool = False):
        self.exec_first_values = list(exec_first_values or [])
        self.raise_on_flush = raise_on_flush
        self.added = []
        self.flushed = False
        self.committed = False
        self.rolled_back = False
        self.refreshed = []

    def add(self, item):
        self.added.append(item)

    def exec(self, statement):
        first_value = self.exec_first_values.pop(0) if self.exec_first_values else None
        return _ExecResult(first_value)

    def flush(self):
        self.flushed = True
        if self.raise_on_flush:
            raise IntegrityError("flush failed", None, None)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def refresh(self, item):
        self.refreshed.append(item)


class DocumentUploadServiceTests(TestCase):
    def test_upload_document_with_dedupe_new_upload_uses_default_bucket(self):
        session = _SessionCapture(exec_first_values=[None])
        storage = _StorageStub(object_exists_result=True)
        upload_file = _UploadFileStub(b"hello", filename="requirements.pdf", content_type="application/pdf")

        document = asyncio.run(
            service.upload_document_with_dedupe(
                session=session,
                storage=storage,
                project_id=uuid4(),
                software_id=None,
                name=None,
                description="desc",
                extra_info='{"source":"prd"}',
                upload_file=upload_file,
            )
        )

        self.assertTrue(session.committed)
        self.assertEqual(len(storage.upload_document_calls), 1)
        self.assertEqual(storage.upload_document_calls[0]["extension"], ".pdf")
        self.assertIsNotNone(document.file_id)
        self.assertEqual(session.added[0].storage_bucket, "softplan")

    def test_upload_document_with_dedupe_repairs_missing_object_into_default_bucket(self):
        existing_file = FileRecord(
            file_hash="existing-hash",
            storage_bucket="legacy-bucket",
            storage_key="legacy/key.pdf",
            file_size=10,
            content_type="application/pdf",
            extension=".pdf",
        )
        session = _SessionCapture(exec_first_values=[existing_file])
        storage = _StorageStub(object_exists_result=False)
        upload_file = _UploadFileStub(b"hello", filename="requirements.pdf", content_type="application/pdf")

        document = asyncio.run(
            service.upload_document_with_dedupe(
                session=session,
                storage=storage,
                project_id=uuid4(),
                software_id=None,
                name="Requirements",
                description="desc",
                extra_info=None,
                upload_file=upload_file,
            )
        )

        self.assertEqual(document.file_id, existing_file.id)
        self.assertEqual(existing_file.storage_bucket, "softplan")
        self.assertEqual(existing_file.storage_key, "documents/2026/04/upload-key.pdf")

    def test_upload_document_with_dedupe_cleans_up_on_flush_conflict(self):
        existing_file = FileRecord(
            file_hash="existing-hash",
            storage_bucket="softplan",
            storage_key="documents/2026/04/existing.pdf",
            file_size=10,
            content_type="application/pdf",
            extension=".pdf",
        )
        session = _SessionCapture(exec_first_values=[None, existing_file], raise_on_flush=True)
        storage = _StorageStub(object_exists_result=True)
        upload_file = _UploadFileStub(b"hello", filename="requirements.pdf", content_type="application/pdf")

        document = asyncio.run(
            service.upload_document_with_dedupe(
                session=session,
                storage=storage,
                project_id=uuid4(),
                software_id=None,
                name=None,
                description="desc",
                extra_info=None,
                upload_file=upload_file,
            )
        )

        self.assertEqual(document.file_id, existing_file.id)
        self.assertEqual(
            storage.removed,
            [{"storage_key": "documents/2026/04/upload-key.pdf", "bucket": "softplan"}],
        )
