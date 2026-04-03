import importlib.util
import os
from unittest import TestCase
from unittest.mock import patch

CURRENT_DIR = os.path.dirname(__file__)
MODULE_PATH = os.path.abspath(os.path.join(CURRENT_DIR, "..", "app", "services", "minio_storage.py"))
SPEC = importlib.util.spec_from_file_location("backend_minio_storage_module", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Failed to load module spec from {MODULE_PATH}")

MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class MinioStorageConfigTests(TestCase):
    def tearDown(self):
        MODULE.get_minio_storage.cache_clear()

    def test_get_minio_storage_uses_new_defaults(self):
        with patch.dict(os.environ, {}, clear=True):
            storage = MODULE.get_minio_storage()

        self.assertEqual(storage.documents_bucket, "softplan-documents")
        self.assertEqual(storage.images_bucket, "softplan-images")

    def test_get_minio_storage_uses_legacy_bucket_when_new_vars_missing(self):
        with patch.dict(os.environ, {"MINIO_BUCKET": "legacy-softplan"}, clear=True):
            storage = MODULE.get_minio_storage()

        self.assertEqual(storage.documents_bucket, "legacy-softplan")
        self.assertEqual(storage.images_bucket, "legacy-softplan")

    def test_get_minio_storage_prefers_new_bucket_vars(self):
        with patch.dict(
            os.environ,
            {
                "MINIO_BUCKET": "legacy-softplan",
                "MINIO_BUCKET_DOCUMENTS": "softplan-documents-prod",
                "MINIO_BUCKET_IMAGES": "softplan-images-prod",
            },
            clear=True,
        ):
            storage = MODULE.get_minio_storage()

        self.assertEqual(storage.documents_bucket, "softplan-documents-prod")
        self.assertEqual(storage.images_bucket, "softplan-images-prod")
