import hashlib
import importlib.util
import os
from unittest import TestCase
from unittest.mock import patch

CURRENT_DIR = os.path.dirname(__file__)
MODULE_PATH = os.path.abspath(os.path.join(CURRENT_DIR, "..", "services", "minio_storage.py"))
SPEC = importlib.util.spec_from_file_location("minio_storage_module", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Failed to load module spec from {MODULE_PATH}")

MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def close(self) -> None:
        return None

    def release_conn(self) -> None:
        return None


class _FakeMinio:
    def __init__(self, *args, **kwargs):
        self._buckets = set()
        self._objects = {}
        self.put_calls = []

    def bucket_exists(self, bucket: str) -> bool:
        return bucket in self._buckets

    def make_bucket(self, bucket: str) -> None:
        self._buckets.add(bucket)

    def put_object(self, bucket: str, storage_key: str, data, *, length: int, content_type: str) -> None:
        payload = data.read(length)
        self._objects[(bucket, storage_key)] = {"payload": payload, "content_type": content_type}
        self.put_calls.append({"bucket": bucket, "storage_key": storage_key, "content_type": content_type})

    def stat_object(self, bucket: str, storage_key: str):
        if (bucket, storage_key) not in self._objects:
            raise MODULE.S3Error("NoSuchKey", "missing", None, None, None, None)
        return self._objects[(bucket, storage_key)]

    def get_object(self, bucket: str, storage_key: str):
        object_data = self._objects[(bucket, storage_key)]
        return _FakeResponse(object_data["payload"])

    def remove_object(self, bucket: str, storage_key: str) -> None:
        self._objects.pop((bucket, storage_key), None)


class MinioStorageImageUploadTests(TestCase):
    def test_upload_image_bytes_reuses_existing_object_by_hash(self):
        with patch.object(MODULE, "Minio", _FakeMinio):
            storage = MODULE.MinioStorage(
                endpoint="localhost:10000",
                access_key="minioadmin",
                secret_key="minioadmin",
                bucket="softplan",
                secure=False,
            )

            payload = b"same-image-content"
            expected_hash = hashlib.sha256(payload).hexdigest()

            first = storage.upload_image_bytes(payload, content_type="image/jpeg")
            second = storage.upload_image_bytes(payload, content_type="image/jpg")

        self.assertEqual(first.bucket, "softplan")
        self.assertEqual(second.bucket, "softplan")
        self.assertEqual(first.storage_key, f"images/{expected_hash}.jpg")
        self.assertEqual(second.storage_key, f"images/{expected_hash}.jpg")
        self.assertEqual(len(storage.client.put_calls), 1)
