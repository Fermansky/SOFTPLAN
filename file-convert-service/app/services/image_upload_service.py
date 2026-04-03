from .minio_storage import StoredObjectRef, get_minio_storage


def upload_image_bytes(payload: bytes, *, content_type: str) -> StoredObjectRef:
    storage = get_minio_storage()
    return storage.upload_image_bytes(payload, content_type=content_type)
