from unittest import TestCase
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from backend.app.api import dependencies
from backend.app.api.routers import extracted_images
from backend.app.models import ExtractedImage, ExtractedImageCreate, ExtractedImageUpdate


class _ExecResult:
    def __init__(self, first_value=None, all_value=None):
        self._first_value = first_value
        self._all_value = all_value if all_value is not None else []

    def first(self):
        return self._first_value

    def all(self):
        return self._all_value


class _SessionCapture:
    def __init__(self, *, first_value=None, all_value=None, raise_on_commit: bool = False):
        self.first_value = first_value
        self.all_value = all_value if all_value is not None else []
        self.raise_on_commit = raise_on_commit
        self.last_statement = None
        self.added = []
        self.deleted = []
        self.committed = False
        self.rolled_back = False
        self.refreshed = []

    def exec(self, statement):
        self.last_statement = statement
        return _ExecResult(first_value=self.first_value, all_value=self.all_value)

    def add(self, item):
        self.added.append(item)

    def delete(self, item):
        self.deleted.append(item)

    def commit(self):
        if self.raise_on_commit:
            raise IntegrityError("commit failed", None, None)
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def refresh(self, item):
        self.refreshed.append(item)


class ExtractedImagesLogicTests(TestCase):
    def _build_create_payload(self) -> ExtractedImageCreate:
        return ExtractedImageCreate.model_validate(
            {
                "file_hash": "a" * 64,
                "storage_bucket": "softplan",
                "storage_key": "images/a.png",
                "file_size": 123,
                "content_type": "image/png",
                "extension": ".png",
                "width": 100,
                "height": 200,
            }
        )

    def test_get_extracted_image_or_404_raises_when_missing(self):
        session = _SessionCapture(first_value=None)

        with self.assertRaises(HTTPException) as ctx:
            dependencies.get_extracted_image_or_404(1, session)

        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(ctx.exception.detail, "Extracted image not found")
        self.assertIn("extracted_images.id", str(session.last_statement.whereclause))

    def test_list_extracted_images_orders_by_created_at_desc(self):
        session = _SessionCapture(all_value=[])

        result = extracted_images.list_extracted_images(session=session, offset=0, limit=10)

        self.assertEqual(result, [])
        self.assertIn("ORDER BY extracted_images.created_at DESC", str(session.last_statement))

    def test_create_extracted_image_success(self):
        session = _SessionCapture()
        payload = self._build_create_payload()

        created = extracted_images.create_extracted_image(payload=payload, session=session)

        self.assertIsInstance(created, ExtractedImage)
        self.assertEqual(created.file_hash, "a" * 64)
        self.assertEqual(len(session.added), 1)
        self.assertTrue(session.committed)
        self.assertEqual(session.refreshed, [created])

    def test_create_extracted_image_conflict_returns_409(self):
        session = _SessionCapture(raise_on_commit=True)
        payload = self._build_create_payload()

        with self.assertRaises(HTTPException) as ctx:
            extracted_images.create_extracted_image(payload=payload, session=session)

        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.detail, "Extracted image hash already exists")
        self.assertTrue(session.rolled_back)

    def test_update_extracted_image_applies_fields(self):
        existing = ExtractedImage(
            file_hash="a" * 64,
            storage_bucket="softplan",
            storage_key="images/a.png",
            file_size=123,
            content_type="image/png",
            extension=".png",
            width=100,
            height=200,
        )
        session = _SessionCapture()
        payload = ExtractedImageUpdate.model_validate({"width": 640, "height": 480})

        with patch.object(extracted_images, "get_extracted_image_or_404", return_value=existing):
            updated = extracted_images.update_extracted_image(image_id=1, payload=payload, session=session)

        self.assertIs(updated, existing)
        self.assertEqual(existing.width, 640)
        self.assertEqual(existing.height, 480)
        self.assertTrue(session.committed)
        self.assertEqual(session.refreshed, [existing])

    def test_delete_extracted_image_hard_delete(self):
        existing = ExtractedImage(
            file_hash="a" * 64,
            storage_bucket="softplan",
            storage_key="images/a.png",
            file_size=123,
            content_type="image/png",
            extension=".png",
            width=100,
            height=200,
        )
        session = _SessionCapture()

        with patch.object(extracted_images, "get_extracted_image_or_404", return_value=existing):
            response = extracted_images.delete_extracted_image(image_id=1, session=session)

        self.assertEqual(response.status_code, 204)
        self.assertEqual(session.deleted, [existing])
        self.assertTrue(session.committed)
